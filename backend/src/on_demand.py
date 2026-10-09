# -*- coding: utf-8 -*-
"""
按需抓取(on-demand fetch)：本地知识库缺什么补什么，不重抓全站。

背景：全量抓取要 15 分钟+，且永远抓不齐（未来还要接多个 wiki）。
按需抓取 = 用户问到本地没有的条目 → MediaWiki API 秒级抓单页 → 清洗切分 →
向量化新增块 → 增量写回 FAISS 索引 + chunks.json → 下次直接命中，永久生效。

被 agent_engine 的工具 fetch_wiki_page 调用；也可独立脚本化使用。
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from .config import settings
from .games import data_file, get_game


# 按需抓取时尽力归类到已知分类(与 DEFAULT_CATEGORIES 一致), 否则留空
_KNOWN_CATS = ("道具", "人物", "卡牌", "胶囊", "饰品", "房间")


def _chunks_path(game: Optional[str] = None) -> Path:
    """按游戏取 chunks 文件——多游戏下不能固定用全局 wiki_name，
    否则给 B 游戏补的条目会写进 A 游戏的数据文件里。"""
    return data_file(game or settings.wiki_name, "chunks.json")


def _load_chunks(game: Optional[str] = None) -> List[Dict]:
    p = _chunks_path(game)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_chunks(chunks: List[Dict], game: Optional[str] = None) -> None:
    p = _chunks_path(game)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)


def _page_category(scraper, title: str) -> str:
    """反查页面归属的已知分类(用于 chunk 元数据/枚举索引)。抓不到就留空。"""
    params = {
        "action": "query",
        "titles": title,
        "prop": "categories",
        "cllimit": "30",
        "format": "json",
    }
    try:
        data = scraper._get(params)
        for _pid, pg in (data.get("query", {}) or {}).get("pages", {}).items():
            for c in pg.get("categories", []):
                cat = c.get("title", "").replace("分类:", "").strip()
                if cat in _KNOWN_CATS:
                    return cat
    except Exception:
        pass
    return ""


def _existing_preview(title: str, game: Optional[str] = None) -> Optional[Dict]:
    """页面已在库时, 返回已有块的预览, 供模型直接作答(不再重复抓取/入库)。"""
    chunks = _load_chunks(game)
    matched = [c for c in chunks if c.get("title") == title]
    if not matched:
        return None
    preview = "\n\n".join(
        f"【{c.get('section', '')}】{c.get('text', '')[:160]}" for c in matched[:3]
    )
    return {
        "ok": True,
        "title": title,
        "category": matched[0].get("category", ""),
        "n_chunks": len(matched),
        "url": next((c.get("url", "") for c in matched if c.get("url")), ""),
        "preview": preview,
        "already_in_db": True,  # 标记: 未新增, 只是取回已有内容
    }


def fetch_and_index(title: str, engine=None, game: Optional[str] = None) -> Dict:
    """抓单页 → 处理 → 增量入索引 + 持久化。
    返回: {ok, title, category, n_chunks, preview, url}，preview 供模型作答。
    已在库的页面直接返回已有内容预览, 不重复入库(避免索引膨胀)。
    game: 目标游戏；不传则取 engine 自带的，再退到默认游戏。"""
    from .wiki_scraper import WikiScraper
    from .data_processor import extract_sections, split_chunks, _prefix_text

    if engine is None:
        from .qa_engine import get_engine  # 延迟导入, 避免循环依赖
        engine = get_engine(game)

    # 单页抓取必须打对游戏的 wiki：engine 自己知道服务的是哪个游戏
    game = game or getattr(engine, "game", None) or settings.wiki_name
    g = get_game(game) or {}

    # 已在库 → 直接返回已有内容, 不重抓(游魂/金心/六面骰这类全量已抓的页面)
    existing = _existing_preview(title, game)
    if existing:
        return existing

    scraper = WikiScraper(
        api_url=g.get("wiki_api_url") or settings.wiki_api_url,
        base_url=g.get("wiki_base_url") or settings.wiki_base_url,
    )
    page = scraper.get_page_html(title)
    if not page:
        return {"ok": False, "title": title, "category": "", "n_chunks": 0,
                "preview": f"页面「{title}」抓取失败（可能不存在或网络错误）。"}

    category = _page_category(scraper, title)
    page["category"] = category

    # 与全量流程一致: 清洗 → 按小节切分 → 语义前缀
    sections = extract_sections(page["html"], page["title"], page["url"], category)
    chunks = split_chunks(sections)
    for ch in chunks:
        ch["text"] = _prefix_text(ch["text"], ch["section"], ch["title"])

    if not chunks:
        return {"ok": False, "title": page["title"], "category": category,
                "n_chunks": 0, "preview": f"页面「{page['title']}」没有提取到有效正文。"}

    # 增量写入 FAISS: 复用 engine 已加载的 vectorstore 与 embeddings, 只向量化新增块
    texts = [c["text"] for c in chunks]
    metadatas = [
        {k: c.get(k, "") for k in ("title", "section", "url", "category")} for c in chunks
    ]
    try:
        engine.vectorstore.add_texts(texts=texts, metadatas=metadatas)
        # 必须写回「该游戏自己的索引目录」：若写根目录而读 <game>/，等于
        # 写一个文件、读另一个文件，新内容永远检索不到（2026-10-09 实际踩到）。
        engine.vectorstore.save_local(str(engine.index_dir))
    except Exception as e:
        # 入库失败(如沙箱写盘被拦): 降级为「仅本次可用」, 但把内容给模型, 不硬失败
        preview = "\n\n".join(
            f"【{c['section']}】{c['text'][:160]}" for c in chunks[:3]
        )
        return {"ok": True, "title": page["title"], "category": category,
                "n_chunks": len(chunks), "url": page["url"],
                "preview": preview + "\n（注：本次未持久化入库，仅本次问答可用）"}

    # 追加到 chunks.json, 保证以后全量重建也不丢这条
    try:
        existing = _load_chunks(game)
        existing.extend(chunks)
        _save_chunks(existing, game)
    except Exception as e:
        print(f"[OnDemand] 写 chunks.json 失败(索引已更新): {e}")

    preview = "\n\n".join(
        f"【{c['section']}】{c['text'][:160]}" for c in chunks[:3]
    )
    print(f"[OnDemand] 已抓取并入库: {page['title']} (分类={category or '未知'}, {len(chunks)} 块)")
    return {
        "ok": True,
        "title": page["title"],
        "category": category,
        "n_chunks": len(chunks),
        "url": page["url"],
        "preview": preview,
    }


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "六面骰"
    res = fetch_and_index(target)
    print("\n", json.dumps(res, ensure_ascii=False, indent=2))

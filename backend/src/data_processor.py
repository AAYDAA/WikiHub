import json
import re
from pathlib import Path
from typing import List, Dict

from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import settings


SKIP_CLASSES = [
    "toc",
    "navbox",
    "navbox-inner",
    "reference",
    "references",
    "infobox",
    "mw-editsection",
    "mw-normal-catlinks",
    "catlinks",
    "printfooter",
    "noprint",
]

# 注意: 不要在这里写 "table.infobox" —— 那会在 _strip_noise 里把含「解锁条件」的
# infobox 也删掉(解锁条件在 class 循环里由 _keep_infobox 保护, 见 _strip_noise)
SKIP_TAGS = ["script", "style", "noscript"]

# 正文短于此值(字符)时, 前缀只拼道具名, 避免「标题 - 小节」喧宾夺主(超短块被标题主导)
MIN_PREFIX_BODY = 15


def _prefix_text(text: str, section: str, title: str) -> str:
    """给 chunk 正文前拼语义锚点: 长块拼完整 section(如「硫磺火 - 效果」),
    超短块只拼道具名 title, 让检索时标题能命中又不压过正文。"""
    if len(text) >= MIN_PREFIX_BODY:
        return f"{section}\n{text}"
    return f"{title}\n{text}"


def _strip_noise(soup) -> None:
    """清洗噪声节点(就地)。infobox 里只有「解锁条件/成就」框值得保留,
    其余实体数据(图片、模板)是噪声, 一并移除。"""
    for tag in SKIP_TAGS:
        for t in soup.select(tag):
            t.decompose()
    for cls in SKIP_CLASSES:
        for t in soup.find_all(class_=cls):
            if "infobox" in cls and _keep_infobox(t):
                continue  # infobox 内包含解锁条件, 保留
            t.decompose()
    for t in soup.find_all("table"):
        if t.find(class_="navbox") or "navbox" in t.get("class", []):
            t.decompose()


def _keep_infobox(tag) -> bool:
    """判断 infobox 节点是否包含值得保留的「解锁条件/成就」信息。"""
    return bool(tag.select(".achievement-box, .bg-primary, [class*='achievement']"))


def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    _strip_noise(soup)
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text


def extract_sections(html: str, title: str, url: str, category: str = "") -> List[Dict]:
    """提取页面各小节为 chunk。category 透传到 chunk 元数据(人物/道具/...),
    供检索期按分类定向召回与「枚举/列举类问题」使用。"""
    soup = BeautifulSoup(html, "lxml")
    _strip_noise(soup)

    def _mk(text, section, cat):
        return {"text": text, "section": section, "title": title, "url": url, "category": cat}

    chunks = []
    current_section = title
    current_content_parts = []

    intro_text_parts = []
    for child in soup.contents:
        name = getattr(child, "name", None)
        if name in ["h2", "h3", "h4"]:
            break
        if name:
            text = child.get_text(strip=True)
            if text and len(text) > 10:
                intro_text_parts.append(text)
        elif hasattr(child, "strip"):
            text = child.strip()
            if text and len(text) > 10:
                intro_text_parts.append(text)

    if intro_text_parts:
        intro = "\n".join(intro_text_parts)
        if len(intro) > 30:
            chunks.append(_mk(intro, title, category))

    for element in soup.find_all(["h2", "h3", "h4", "p", "li", "ul", "ol", "dl", "dd", "dt", "blockquote", "pre", "table"]):
        if element.name in ["h2", "h3", "h4"]:
            if current_content_parts:
                text = "\n".join(current_content_parts)
                if len(text.strip()) > 20:
                    chunks.append(_mk(text.strip(), current_section, category))
                current_content_parts = []

            heading_text = element.get_text(strip=True)
            heading_text = re.sub(r"\[.*?\]", "", heading_text).strip()
            current_section = f"{title} - {heading_text}"
        else:
            # 普通 table(如掉落概率表)是噪声, 只提取含「解锁条件/成就」的 infobox
            if element.name == "table" and not _keep_infobox(element):
                continue
            text = element.get_text(strip=True)
            if text and len(text) > 5:
                current_content_parts.append(text)

    if current_content_parts:
        text = "\n".join(current_content_parts)
        if len(text.strip()) > 20:
            chunks.append(_mk(text.strip(), current_section, category))

    if not chunks:
        full_text = clean_html(html)
        if full_text:
            chunks.append(_mk(full_text[:3000], title, category))

    return chunks


def split_chunks(chunks: List[Dict], chunk_size: int = 500, chunk_overlap: int = 100) -> List[Dict]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", " ", ""],
    )

    result = []
    for chunk in chunks:
        text = chunk["text"]
        if len(text) <= chunk_size:
            result.append(chunk)  # 直接引用, 保留 category 等元数据
            continue

        splits = text_splitter.split_text(text)
        for i, split_text in enumerate(splits):
            new_chunk = dict(chunk)  # 保留 category 等元数据(勿只重建 4 个字段)
            new_chunk["text"] = split_text
            if len(splits) > 1:
                new_chunk["section"] = f"{chunk['section']} ({i+1}/{len(splits)})"
            result.append(new_chunk)

    return result


def process_wiki_data(input_path: str, chunk_size: int = 500, chunk_overlap: int = 100,
                      base_url: str = "") -> List[Dict]:
    print(f"[Process] 加载数据: {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        pages = json.load(f)

    print(f"[Process] 共 {len(pages)} 个页面，开始提取章节...")

    all_sections = []
    for page in pages:
        sections = extract_sections(
            page["html"], page["title"], page["url"], page.get("category", "")
        )
        all_sections.extend(sections)

    print(f"[Process] 提取到 {len(all_sections)} 个章节块")

    final_chunks = split_chunks(all_sections, chunk_size, chunk_overlap)
    print(f"[Process] 切分后共 {len(final_chunks)} 个文档块")

    # 必须在切分「之后」加前缀: 否则长块被 split 成多段后, 只有首段带锚点, 续段丢失标题信号
    # (超短块只拼道具名, 见 _prefix_text)。改了文本 → 重建索引前须删 embeddings_cache.npz
    for ch in final_chunks:
        ch["text"] = _prefix_text(ch["text"], ch["section"], ch["title"])

    # ============ 分类索引文档(枚举/列举类问题的"可引用原文") ============
    # 这类问题(如"以撒有哪些角色")的答案是多个页面的并集, 任何单页都没有现成结论,
    # 纯语义检索必然答不出。解法: 把"分类成员清单"做成一条文档进索引,
    # 让枚举题命中它 → LLM 照抄列表并附分类页链接, 有据可查。见 README 评测节说明。
    final_chunks.extend(_build_category_index_chunks(pages, base_url))
    print(f"[Process] 加分类索引文档后共 {len(final_chunks)} 个文档块")

    return final_chunks


# 分类「别名」提示词: 帮助玩家口语(query)命中分类。如玩家说"角色", 数据分类是"人物"
_CATEGORY_ALIAS = {
    "人物": "（即玩家可玩角色/人物）",
}


def _build_category_index_chunks(pages: List[Dict], base_url: str = "") -> List[Dict]:
    """把已收录页面按分类聚合成"清单文档"。计数严格来自知识库, 不虚报。
    base_url: 该游戏 wiki 的地址前缀（多游戏下不能再依赖全局 settings）。"""
    from collections import OrderedDict

    base_url = base_url or settings.wiki_base_url

    by_cat: Dict[str, List[Dict]] = OrderedDict()
    for p in pages:
        by_cat.setdefault(p.get("category", "?"), []).append(p)

    chunks = []
    for cat, items in by_cat.items():
        if cat in ("?", ""):
            continue
        titles = sorted(i.get("title", "") for i in items)
        n = len(titles)
        cap_hint = ""
        # 道具恰好 500 页 = 撞到抓取上限(见 wiki_scraper 的 per_category_limit), 如实标注
        if n >= 500:
            cap_hint = "（注：该分类已达抓取上限 500 页，可能未收录全部分类内容）"

        alias = _CATEGORY_ALIAS.get(cat, "")
        body = (
            f"『{cat}』分类索引{alias}（来源：灰机 Wiki 的 {cat} 分类页）。\n"
            f"本知识库已收录『{cat}』分类下的 {n} 个页面：\n"
            + "、".join(titles)
            + ("\n" + cap_hint if cap_hint else "")
        )
        cat_title = f"{cat}（分类索引）"
        chunks.append({
            "text": body,
            "section": cat_title,
            "title": cat_title,
            "url": f"{base_url}Category:{cat}",
            "category": cat,
            "is_index": True,
        })
    return chunks


if __name__ == "__main__":
    import sys

    from .games import data_file, get_game

    args = sys.argv[1:]
    game = None
    if "--game" in args:
        i = args.index("--game")
        game = args[i + 1] if i + 1 < len(args) else None
    game = game or settings.wiki_name

    # 多游戏：数据文件按 <game>_ 前缀隔离，wiki 地址也要用该游戏自己的
    g = get_game(game) or {}
    base_url = g.get("wiki_base_url") or settings.wiki_base_url

    input_file = data_file(game, "wiki_all.json")
    if not input_file.exists():
        print(f"找不到数据文件: {input_file}")
        print("请先抓取该游戏的数据（或用首页「一键建库」）")
    else:
        chunks = process_wiki_data(str(input_file), base_url=base_url)
        output_file = data_file(game, "chunks.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)
        print(f"[Process] 结果已保存到 {output_file}")

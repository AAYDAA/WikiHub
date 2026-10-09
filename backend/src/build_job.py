# -*- coding: utf-8 -*-
"""一键建库：把「自动发现分类 → 抓取 → 切分 → 建索引」串成一条后台任务。

为什么必须做成后台任务：
  一个中型 wiki 抓取 + 建索引要几十分钟，HTTP 请求不可能挂那么久。
  所以接口只负责「启动」，进度另开接口轮询。

并发控制（重要）：
  全局同一时刻只允许一个构建任务。建索引是内存大户（要加载 embedding 模型 +
  全部 chunk 向量），本机 16GB 内存本来就紧，并发跑必 OOM。

分类为什么要「自动发现」而不是硬编码（项目的血泪教训）：
  早先抓取清单里写死了「角色 / 怪物 / Boss」，但该 wiki 真实分类叫「人物」，
  另外两个压根不存在 —— MediaWiki 对不存在的分类**返回空列表而不报错**，
  于是人物数据整整漏掉一类，用户提问答不上来才发现。详见 wiki_probe.discover_categories。
"""
import json
import threading
import time
from typing import Dict, Optional

from .games import data_file, get_game
from .wiki_probe import discover_categories
from .wiki_scraper import WikiScraper

PER_CATEGORY_LIMIT = 500   # 每个分类最多抓多少页（与既有脚本保持一致）
CATEGORY_GAP = 0.2         # 分类之间的间隔，别把对方站点打爆

# {game_id: 状态字典}
_jobs: Dict[str, dict] = {}
_lock = threading.Lock()
_current_job: Optional[str] = None   # 正在跑的游戏 id；None 表示空闲


def _idle_status(game_id: str) -> dict:
    return {
        "game_id": game_id,
        "status": "idle",
        "stage": "",
        "message": "尚未开始构建",
        "percent": 0,
        "error": None,
        "started_at": None,
        "finished_at": None,
    }


def get_status(game_id: str) -> dict:
    return _jobs.get(game_id) or _idle_status(game_id)


def _update(game_id: str, **kw) -> None:
    job = _jobs.setdefault(game_id, _idle_status(game_id))
    job.update(kw)


def start_build(game_id: str) -> dict:
    """启动后台构建；已有任务在跑则拒绝。"""
    global _current_job

    if not get_game(game_id):
        raise ValueError(f"未登记的游戏：{game_id}")

    with _lock:
        if _current_job:
            raise ValueError(
                f"已有构建任务在运行（{_current_job}），请等它结束再试"
                "（建索引很吃内存，不做并发）"
            )
        cur = _jobs.get(game_id)
        if cur and cur.get("status") == "running":
            raise ValueError("该游戏的构建正在进行中")

        _jobs[game_id] = {
            "game_id": game_id,
            "status": "running",
            "stage": "starting",
            "message": "准备中…",
            "percent": 0,
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        }
        _current_job = game_id

    threading.Thread(target=_run, args=(game_id,), daemon=True).start()
    return _jobs[game_id]


def _run(game_id: str) -> None:
    global _current_job
    try:
        _build(game_id)
        _update(game_id, status="done", stage="done",
                message="知识库已就绪，可以开始问答了", percent=100,
                finished_at=time.time())
    except Exception as e:
        _update(game_id, status="failed", stage="failed",
                message=f"构建失败：{e}", error=str(e), finished_at=time.time())
        print(f"[Build:{game_id}] 失败: {e}")
    finally:
        with _lock:
            _current_job = None


def _build(game_id: str) -> None:
    g = get_game(game_id) or {}
    api_url = g.get("wiki_api_url")
    base_url = g.get("wiki_base_url") or ""
    if not api_url:
        raise ValueError("该游戏缺少 wiki 接口地址，请删掉后重新添加")

    # ---- 1) 自动发现分类 ----
    _update(game_id, stage="discovering", message="正在发现站点分类…", percent=2)
    cats = discover_categories(api_url)
    if not cats:
        raise ValueError("没能发现任何内容分类，该站点结构可能不常见")
    _update(game_id, stage="scraping",
            message=f"发现 {len(cats)} 个分类，开始抓取", percent=5)

    # ---- 2) 抓取（占进度 5% → 75%）----
    scraper = WikiScraper(api_url=api_url, base_url=base_url)
    all_pages: Dict[str, dict] = {}
    empty_cats = []
    for i, cat in enumerate(cats):
        _update(game_id,
                message=f"抓取分类 {i + 1}/{len(cats)}：{cat}（已收 {len(all_pages)} 页）",
                percent=5 + int(70 * (i / max(1, len(cats)))))
        try:
            pages = scraper.scrape_category(cat, limit=PER_CATEGORY_LIMIT)
        except Exception as e:
            print(f"[Build:{game_id}] 分类 '{cat}' 抓取失败: {e}")
            continue
        if not pages:
            empty_cats.append(cat)
        for p in pages:
            all_pages.setdefault(p["raw_title"], p)
        time.sleep(CATEGORY_GAP)

    if not all_pages:
        raise ValueError("抓取到 0 个页面，请确认该 wiki 可以正常访问")
    if empty_cats:
        # 如实记录，不做静默处理 —— 这正是当初漏掉整个人物分类的根因
        print(f"[Build:{game_id}] 以下分类抓不到页面（已跳过）: {empty_cats}")

    pages = list(all_pages.values())
    out_file = data_file(game_id, "wiki_all.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)
    _update(game_id, message=f"抓取完成，共 {len(pages)} 页", percent=76)

    # ---- 3) 切分 ----
    _update(game_id, stage="processing", message="正在切分文本…", percent=80)
    from .data_processor import process_wiki_data

    chunks = process_wiki_data(str(out_file), base_url=base_url)
    chunks_file = data_file(game_id, "chunks.json")
    with open(chunks_file, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    _update(game_id, message=f"切分完成，{len(chunks)} 个知识块", percent=86)

    # ---- 4) 建索引（最慢的一步）----
    _update(game_id, stage="indexing",
            message=f"正在向量化 {len(chunks)} 个知识块，这一步最慢…", percent=88)
    from .build_index import build_index_from_chunks

    build_index_from_chunks(chunks, collection_name=game_id)
    _update(game_id, message="索引写入完成", percent=99)

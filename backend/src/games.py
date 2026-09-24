# -*- coding: utf-8 -*-
"""游戏注册表：把一个「游戏」映射到一套独立的 Wiki 知识库。

设计：
  每个游戏 = games.json 里的一条记录 + faiss_index/<id>/ 下的一份独立索引。
  新增游戏不需要改任何代码，登记一条记录 + 建好索引即可（见 README 或 build_index）。

向后兼容：
  games.json 缺失或解析失败时，回退为「仅默认 wiki」的单游戏模式，服务不会因此起不来。
"""
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from .config import BASE_DIR, settings

GAMES_FILE: Path = BASE_DIR / "games.json"

# 新建 games.json 时写入的说明（与既有文件保持一致，避免新老文件格式漂移）
_GAMES_FILE_COMMENT = (
    "游戏注册表：每个游戏对应一套独立的 Wiki 知识库。新增游戏时在此登记，"
    "并确保 faiss_index/<id>/ 下存在对应索引（用 python -m src.build_index --game <id> 构建）。"
)

# id 会作为目录名使用（faiss_index/<id>/、data/<id>_*），所以要限制字符合法性
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def _default_game() -> Dict:
    """没有 games.json 时的兜底：沿用 .env 里的单游戏配置。"""
    return {
        "id": settings.wiki_name,
        "name": settings.wiki_name,
        "icon": "🎮",
        "wiki_base_url": settings.wiki_base_url,
        "wiki_api_url": settings.wiki_api_url,
        "enabled": True,
    }


def load_games() -> List[Dict]:
    if not GAMES_FILE.exists():
        return [_default_game()]
    try:
        data = json.loads(GAMES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Games] games.json 解析失败，回退默认游戏: {e}")
        return [_default_game()]

    games = [g for g in (data.get("games") or []) if g.get("enabled", True)]
    return games or [_default_game()]


def get_game(game_id: Optional[str]) -> Optional[Dict]:
    """按 id 取游戏；game_id 为空时返回第一个（单游戏场景的向后兼容）。"""
    games = load_games()
    if not game_id:
        return games[0] if games else None
    for g in games:
        if g.get("id") == game_id:
            return g
    return None


def index_dir_for(game_id: Optional[str]) -> Path:
    """该游戏的索引目录：faiss_index/<game_id>/"""
    return settings.chroma_persist_dir / (game_id or settings.wiki_name)


def data_file(game_id: Optional[str], suffix: str) -> Path:
    """该游戏的数据文件：data/<game_id>_<suffix>（chunks / wiki_all 等）。"""
    return settings.data_dir / f"{game_id or settings.wiki_name}_{suffix}"


def is_index_ready(game_id: Optional[str]) -> bool:
    """索引是否就绪（FAISS 的两个文件都在）。"""
    d = index_dir_for(game_id)
    return (d / "index.faiss").exists() and (d / "index.pkl").exists()


def list_games_with_status() -> List[Dict]:
    """/api/games 用：附上 ready 标记，前端可据此把「没数据」的游戏置灰。"""
    out = []
    for g in load_games():
        item = dict(g)
        item["ready"] = is_index_ready(g.get("id"))
        out.append(item)
    return out


def _read_doc() -> Dict:
    """读整份 games.json（含 _comment 等顶层字段）。

    这里刻意不复用 load_games()：那个会把 enabled=false 的条目过滤掉，
    若基于它的结果回写，用户手动禁用的游戏会被顺手删掉。
    """
    if not GAMES_FILE.exists():
        return {"_comment": _GAMES_FILE_COMMENT, "games": []}
    try:
        doc = json.loads(GAMES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"games.json 解析失败，请先修好它再新增：{e}")
    if not isinstance(doc, dict):
        raise ValueError("games.json 顶层不是对象，无法新增")
    doc.setdefault("_comment", _GAMES_FILE_COMMENT)
    doc.setdefault("games", [])
    return doc


def add_game(entry: Dict) -> Dict:
    """登记一个新游戏并写入 games.json（原子写入）。

    为什么必须原子写：直接用 open(w) 会先把文件截成 0 字节，
    若此刻进程被杀或磁盘写满，games.json 就成了半截内容——
    后果是 load_games() 回退成「默认单游戏」、前端列表整个空掉。
    所以先写同目录临时文件，再用 os.replace 原子替换。
    """
    gid = (entry.get("id") or "").strip().lower()
    name = (entry.get("name") or "").strip()
    api_url = (entry.get("wiki_api_url") or "").strip()
    base_url = (entry.get("wiki_base_url") or "").strip()

    if not _ID_RE.match(gid):
        raise ValueError("id 只能用小写字母、数字和连字符，且以字母或数字开头（最长 31 位）")
    if not name:
        raise ValueError("游戏名不能为空")
    if not base_url or not api_url:
        raise ValueError("wiki_base_url 与 wiki_api_url 都不能为空")
    for u in (base_url, api_url):
        if not u.startswith(("http://", "https://")):
            raise ValueError("wiki 地址必须是 http/https")

    doc = _read_doc()
    games = doc["games"]
    if any((g or {}).get("id") == gid for g in games):
        raise ValueError(f"id「{gid}」已存在，请换一个")

    record = {
        "id": gid,
        "name": name,
        "icon": (entry.get("icon") or "🎮").strip() or "🎮",
        "wiki_base_url": base_url,
        "wiki_api_url": api_url,
        "enabled": True,
    }
    games.append(record)

    tmp = GAMES_FILE.parent / (GAMES_FILE.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, GAMES_FILE)
    return record

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import uuid

from src.config import settings
from src.games import add_game, is_index_ready, list_games_with_status
from src.qa_engine import get_engine
from src.wiki_probe import ProbeError, probe_wiki

# 会话历史(内存存储, 单机演示够用; 生产可换 Redis):
# {session_id: [{role: "user"|"assistant", content: str}, ...]}
_SESSIONS: Dict[str, List[Dict]] = {}
MAX_HISTORY_TURNS = 6  # 每会话保留最近 6 轮(12 条消息)


def _get_history(sid: str) -> List[Dict]:
    return _SESSIONS.get(sid, [])


def _append_history(sid: str, question: str, answer: str) -> None:
    h = _SESSIONS.setdefault(sid, [])
    h.append({"role": "user", "content": question})
    h.append({"role": "assistant", "content": answer})
    if len(h) > MAX_HISTORY_TURNS * 2:
        del h[: len(h) - MAX_HISTORY_TURNS * 2]


def _ask_router(question: str, mode: Optional[str] = None,
                history: Optional[List[Dict]] = None, game: Optional[str] = None) -> dict:
    """按「游戏 + 模式」路由。
    game: 使用哪套知识库（不同游戏的索引完全独立，互不干扰）；
    mode: rag(默认, 普通检索增强) / agent(大模型调度工具)，请求级参数优先于 .env 的 QA_MODE。
    未知 mode 回退 rag（安全默认）。"""
    engine = get_engine(game)
    qa_mode = (mode or settings.qa_mode or "rag").lower()
    if qa_mode == "agent":
        from src.agent_engine import get_agent_engine  # 懒加载, 普通模式零开销
        return get_agent_engine(engine).ask(question, history)
    return engine.ask(question, history)

app = FastAPI(
    title="WikiHub API",
    description="多游戏 Wiki 聚合智能问答 API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str
    game: Optional[str] = None
    mode: Optional[str] = None  # 请求级问答模式: rag(默认) / agent, 优先于 .env 的 QA_MODE
    session_id: Optional[str] = None  # 会话 id: 传则续接历史, 不传则新建会话


class SourceItem(BaseModel):
    title: str
    section: str
    url: str


class AskResponse(BaseModel):
    question: str
    answer: str
    sources: List[SourceItem]
    trace: Optional[List[Dict]] = None  # agent 模式的工具调用轨迹(调试/成本分析用)
    session_id: str  # 会话 id, 前端存下来下次携带即可续接上下文


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    game: Optional[str] = None


@app.get("/")
async def root():
    return {
        "name": "WikiHub",
        "version": "0.1.0",
        "wiki": settings.wiki_name,
        "status": "ok",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/api/games")
async def list_games():
    """游戏列表（读 games.json）。
    ready=false 表示该游戏还没建索引，前端可据此置灰、避免点进去报错。"""
    return {"games": list_games_with_status()}


@app.post("/api/ask", response_model=AskResponse)
async def ask_question(req: AskRequest):
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")

    try:
        # 会话: 有 session_id 续接历史, 无则新建
        sid = req.session_id or str(uuid.uuid4())
        history = _get_history(sid)

        result = _ask_router(req.question.strip(), req.mode, history, req.game)
        _append_history(sid, req.question.strip(), result["answer"])

        return AskResponse(
            question=result["question"],
            answer=result["answer"],
            sources=[SourceItem(**s) for s in result["sources"]],
            trace=result.get("trace"),
            session_id=sid,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search")
async def search(req: SearchRequest):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="查询内容不能为空")

    try:
        engine = get_engine(req.game)
        results = engine.search(req.query.strip(), top_k=req.top_k)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===== 首页「添加 Wiki」=====
# 拆成 probe / register 两步，是为了让用户先看到 AI 解析结果再确认落库：
# 直接把探测结果写进去的话，AI 把游戏名剥错了就没法救（还得手动改 games.json）。

class WikiProbeRequest(BaseModel):
    url: str


class WikiRegisterRequest(BaseModel):
    id: str
    name: str
    icon: Optional[str] = None
    wiki_base_url: str
    wiki_api_url: str


@app.post("/api/wiki/probe")
async def wiki_probe(req: WikiProbeRequest):
    """探测一个 Wiki 地址：是不是可用的 MediaWiki 站、游戏名叫什么、图标是哪个。
    只返回预览，不写任何文件。"""
    try:
        return probe_wiki(req.url)
    except ProbeError as e:
        # ProbeError 都是「用户输入/目标站点」的问题，用 400，
        # 前端可以拿 detail 直接弹提示；500 会让人以为是服务端崩了。
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"探测失败：{e}")


@app.post("/api/wiki/register")
async def wiki_register(req: WikiRegisterRequest):
    """把用户确认后的 Wiki 登记进 games.json。

    注意：这里只登记，**不建索引**。索引要另跑
    `python -m src.build_index --game <id>`（爬全站，耗时较长）。
    未建索引的游戏 ready=false，前端会把卡片置灰并提示「未建知识库」。
    """
    try:
        record = add_game(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"登记失败：{e}")
    return {"ok": True, "game": {**record, "ready": is_index_ready(record["id"])}}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

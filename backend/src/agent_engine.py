# -*- coding: utf-8 -*-
"""
Agent 模式：让大模型当"调度员"，自己决定调哪个工具、要不要兜底。

- 普通 RAG 模式(QA_MODE=rag, 默认)完全保留不动；此文件只实现 agent 壳。
- 工具极少(3个)：本地检索 / 分类清单 / 站外搜索兜底 —— 手写 function calling 循环,
  不引 agent 框架, 逻辑全透明, 每一步都能讲清楚(面试友好)。
- 与普通 RAG 共享同一份 FAISS / 分类索引文档(复用 QaEngine 实例), 不重复加载模型/内存。
- 来源在工具调用过程中收集, 最终答案附可引用来源。
"""
import json
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from .config import settings

MAX_STEPS = 6  # 单次问答最多工具往返轮数, 防死循环/失控成本

AGENT_SYSTEM = """你是游戏攻略智能助手，手上有一批工具。先判断再决定用哪个，最后基于工具结果用中文简洁作答。

工具使用判断：
1. 具体条目类问题（某道具/角色/房间的效果、解锁、属性、掉落）→ 用 search_wiki 查本地知识库
2. "有哪些 / 多少种 / 列出某分类 / 一共有哪些" → 用 list_category 查该分类的完整清单
3. 本地检索结果明显不足、或需要站外/最新信息 → 用 web_search 兜底再答
4. 本地检索和站外搜索都确认某个条目缺失、但你知道它应该存在（如某个道具/角色的解锁条件）→ 用 fetch_wiki_page 直接抓取该页面正文并永久入库，再基于抓到的内容作答

硬性规则：
- 不要编造：答案必须来自工具返回的内容；查不到就如实说"知识库未收录，建议到 Wiki 原文查看"
- search_wiki 一次可返回多条；不够就换词再查或 web_search，最多来回 {max_steps} 轮
- 最终回答要简洁、可列点，别把工具原文整段抄给用户"""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": "在本地 Wiki 知识库按语义检索相关内容（道具/角色/房间的效果、解锁、属性、掉落等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词/问题，尽量含条目名"},
                    "top_k": {"type": "integer", "description": "返回条数", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_category",
            "description": "列出某分类下的完整成员清单，用于\"有哪些/多少种/列出/一共有哪些\"类问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "分类名：人物/道具/饰品/卡牌/胶囊/房间"},
                },
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "本地知识库查不到时，到灰机 Wiki 站外搜索兜底（返回匹配条目标题与摘要）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词"},
                    "limit": {"type": "integer", "description": "返回条数", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_wiki_page",
            "description": "本地知识库缺失某个条目时，按需从灰机 Wiki 抓取该页面正文并永久入库（下次直接命中）。适合'某道具/角色/机制的解锁条件、获取方式'这类本地查不到、且站外搜索只找到标题没有正文的问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "页面标题，如'六面骰'、'阿撒泻勒'；不确定确切标题时可先用 web_search 确认"},
                },
                "required": ["title"],
            },
        },
    },
]

_SUPPORTED_CATS = ("人物", "道具", "饰品", "卡牌", "胶囊", "房间")


class AgentEngine:
    def __init__(self, engine):
        """engine: 共享的 QaEngine 实例(复用其 vectorstore/index_docs/retriever)。"""
        self._engine = engine
        self._llm = ChatOpenAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        self._llm_tools = self._llm.bind_tools(TOOL_SCHEMAS)
        self._sources: List[Dict] = []  # 本轮问答收集的来源(工具调用过程中)

    # ---------------- 工具 ----------------
    def _tool_search_wiki(self, query: str, top_k: int = 5) -> str:
        top_k = max(1, min(int(top_k or 5), 10))
        results = self._engine.search(query, top_k=top_k)
        if not results:
            return "本地知识库无相关结果。"
        lines = []
        for r in results:
            self._add_source(r)
            head = " | ".join(x for x in [r.get("title", ""), r.get("section", "")] if x)
            text = (r.get("text", "") or "").strip().replace("\n", " ")
            lines.append(f"### {head}\n{text[:220]}")
        return "\n\n".join(lines)

    def _tool_list_category(self, category: str) -> str:
        cat = (category or "").strip()
        doc = self._engine._index_docs.get(cat)
        if doc:
            meta = doc.metadata or {}
            self._add_source({
                "title": meta.get("title", f"{cat}（分类索引）"),
                "section": "",
                "url": meta.get("url", settings.wiki_base_url + "Category:" + cat),
            })
            return doc.page_content
        return f"支持的分类: {'、'.join(_SUPPORTED_CATS)}。未收录分类「{cat}」，请用支持的分类名重试。"

    def _tool_web_search(self, query: str, limit: int = 5) -> str:
        limit = max(1, min(int(limit or 5), 8))
        url = (
            f"{settings.wiki_api_url}?action=query&list=search"
            f"&srsearch={urllib.parse.quote(query)}&srlimit={limit}&format=json&utf8=1"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WikiHub/0.1"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
            hits = (data.get("query") or {}).get("search") or []
            lines = []
            for h in hits:
                title = h.get("title", "")
                snippet = re.sub(r"<[^>]+>", "", h.get("snippet", "") or "")
                lines.append(f"- {title}: {snippet[:160]}")
            if not lines:
                return "站外搜索无结果。"
            self._add_source({"title": "站外搜索", "section": query, "url": settings.wiki_base_url})
            return "\n".join(lines) + f"\n(来源: {settings.wiki_base_url})"
        except Exception as e:
            return f"站外搜索暂不可用（{type(e).__name__}），请基于本地知识库作答。"

    def _tool_fetch_wiki_page(self, title: str) -> str:
        from .on_demand import fetch_and_index  # 延迟导入, 避免启动开销
        res = fetch_and_index(title, self._engine)
        if res.get("ok"):
            url = res.get("url") or settings.wiki_base_url.rstrip("/") + "/wiki/" + title
            self._add_source({
                "title": res.get("title", title),
                "section": "按需抓取入库" if not res.get("already_in_db") else "页面已收录",
                "url": url,
            })
            tag = "已在库(未重复入库)" if res.get("already_in_db") else f"已抓取并入库(分类={res.get('category') or '未知'}, {res.get('n_chunks')} 块)"
            return f"{tag}。页面内容：\n\n{res.get('preview', '')}"
        return res.get("preview", f"抓取「{title}」失败。")

    def _run_tool(self, name: str, args: Dict) -> str:
        if name == "search_wiki":
            return self._tool_search_wiki(args.get("query", ""), args.get("top_k", 5))
        if name == "list_category":
            return self._tool_list_category(args.get("category", ""))
        if name == "web_search":
            return self._tool_web_search(args.get("query", ""), args.get("limit", 5))
        if name == "fetch_wiki_page":
            return self._tool_fetch_wiki_page(args.get("title", ""))
        return f"未知工具: {name}"

    def _add_source(self, r: Dict):
        url = r.get("url", "")
        if not url:
            return
        for s in self._sources:
            if s.get("url") == url and s.get("section", "") == r.get("section", ""):
                return
        self._sources.append({
            "title": r.get("title", ""),
            "section": r.get("section", ""),
            "url": url,
        })

    # ---------------- 主循环 ----------------
    def ask(self, question: str, history: Optional[List[Dict]] = None) -> Dict:
        self._sources = []
        history = history or []
        messages = [
            SystemMessage(content=AGENT_SYSTEM.format(max_steps=MAX_STEPS)),
        ]
        # 多轮: 把历史作为真实对话消息插入, 支持「它/这个」指代前文条目
        from langchain_core.messages import AIMessage
        for h in history[-6:]:
            if h.get("role") == "user":
                messages.append(HumanMessage(content=h["content"]))
            elif h.get("role") == "assistant":
                messages.append(AIMessage(content=h["content"]))
        messages.append(HumanMessage(content=question))
        trace = []
        ai = None
        for step in range(MAX_STEPS):
            ai = self._llm_tools.invoke(messages)
            tool_calls = getattr(ai, "tool_calls", None) or []
            if not tool_calls:
                break  # 模型选择直接回答, 结束循环

            messages.append(ai)
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("args") or {}
                result = self._run_tool(name, args)
                trace.append({"step": step + 1, "tool": name, "args": args, "result_len": len(result)})
                messages.append(ToolMessage(content=result, tool_call_id=tc.get("id", "")))
        else:
            # 达到轮数上限仍未给最终答案: 强制直接作答, 不再调工具
            ai = self._llm.invoke(messages + [
                HumanMessage(content="请基于你已经拿到的信息，直接给出最终答案（不要再调用任何工具）。")
            ])

        answer = ai.content if isinstance(ai.content, str) else str(ai.content or "")
        return {
            "question": question,
            "answer": answer,
            "sources": self._sources[:6],
            "trace": trace,
        }


_agent_instance: Optional[AgentEngine] = None


def get_agent_engine(engine=None) -> AgentEngine:
    global _agent_instance
    if _agent_instance is None:
        from .qa_engine import get_engine  # 延迟导入, 避免循环依赖
        _agent_instance = AgentEngine(engine or get_engine())
    return _agent_instance

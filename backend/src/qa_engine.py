import os
from typing import List, Dict, Optional

from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.chains import RetrievalQA
from langchain_core.retrievers import BaseRetriever
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_community.vectorstores import FAISS

from .config import settings
from .build_index import get_embedding_function
from .games import data_file, index_dir_for


SYSTEM_PROMPT_TEMPLATE = """你是游戏攻略助手，基于 Wiki 知识库回答玩家问题。

请严格根据以下参考资料回答问题。如果资料里没有明确答案，就说"这个问题我暂时不太清楚，建议查看 Wiki 原文获取最新信息"，不要编造答案。
回答要求：
- 简洁准确，重点突出
- 有条理，可以使用简短的列表
- 只说与问题相关的内容
- 如果有多个答案或条件，分点说明

参考资料：
{context}

玩家问题：{question}

回答："""


# ============================================================
# 轻量混合重排(可开关): 向量相似度为主, 叠加两项小加成:
#   1) 标题字符命中   —— 缓解「圣心 vs 圣饼」「该隐 vs 幸运脚」这类标题混淆
#   2) 小节关键词命中 —— 问题含「效果/解锁」等词时, 把对应小节块往前排
# 权重刻意调小, 让向量相似度仍然主导排序; 用 eval/ 的 20 题回归验证效果,
# 环境变量 HYBRID_RERANK=0 可关闭(用于 A/B 对比)。
# ============================================================
_HYBRID_RERANK = os.getenv("HYBRID_RERANK", "1").lower() in ("1", "true", "yes", "on")
_RERANK_CANDIDATES = 60           # 先向量粗召回 N 条再重排
_TITLE_BONUS = 0.06               # 标题字符命中率加成
_SECTION_KW_BONUS = 0.05          # 小节命中问题关键词加成
_PAGE_CAP = 3                     # 同一页面(标题)最多占坑数, 防「以撒」页垄断召回
# 枚举题: 目标分类条目的大幅加成(≈"分类浏览"而非纯相似度) + 更大候选窗
# 词法重合(以撒的心脏/以撒的头)会让真实成员页排在 top-60 之外, 加成必须大到压过相似度差距
_ENUM_CAT_BONUS = 0.35
_ENUM_CANDIDATES = 400
_STOP_CHARS = set("的了是在有和与或以及怎么如何怎样什么哪哪个为什么吗呢吧啊呀哟哦嗯这那您你我一很都也还就")
_SECTION_KEYWORDS = ("效果", "解锁", "掉落", "能力", "属性", "作用", "获得", "策略", "成就", "注意")

# ============ 别名定向召回的「名额控制」============
# 坑(实测): _alias_docs_for 原本按 docstore 顺序取该标题下前 N 个文档, 而 docstore
# 顺序 = 入库顺序, 该条目的头几段几乎总是 intro 碎片(如「硫磺火 (1/13)」); 再加上
# k 小的时候 budget 会被吃成 0, 导致后面的标题/小节重排结果一个都进不来。
# 表现: top_k=3 时「硫磺火有什么效果」返回的全是 (1/13)(2/13)(3/13) intro 碎片,
#       小节精确率从 77.8% 掉到 22.2%(2026-09-22 对照实验确认, 唯一变量是 alias)。
# 对策: ①别名最多占一半名额 ②优先挑关键小节而非顺序前 N 个(见 _alias_docs_for)
_ALIAS_MAX_RATIO = 0.5
_ALIAS_CAP = 3

# ============ 枚举/列举类问题: 定向命中「分类索引」文档 ============
# 玩家口语里的分类词可能 ≠ 数据分类名(如玩家说"角色", 数据分类是"人物")。
# 仅当问题同时含枚举意图(有哪些/列出/多少种...)与分类词时才定向提升,
# 避免"天使房和恶魔房有哪些区别"这类含"哪些"的对比题被误伤(故只用≥2字的分类词)。
_CATEGORY_TERM_ALIAS = {
    "角色": "人物", "人物": "人物",
    "道具": "道具", "物品": "道具",
    "饰品": "饰品", "首饰": "饰品",
    "卡牌": "卡牌", "塔罗牌": "卡牌",
    "符文": "卡牌",  # wiki 上符文(Rune)属卡牌类物品
    "胶囊": "胶囊", "药丸": "胶囊",
    "房间": "房间",
}
_ENUM_PHRASES = ("有哪些", "都有哪些", "一共有哪些", "列出", "多少种", "哪几种", "有几种", "一览", "分别有", "分别有哪些", "所有")
# 枚举题查询扩展词: 把玩家口语注入向量查询, 把真实的分类成员页拉回候选窗口
# (否则「以撒的结合有哪些角色」会被同名/前缀内容垄断, 其他角色页根本进不了 top-60)
_CAT_EXPAND = {
    "人物": ["人物", "角色"],
    "道具": ["道具", "物品"],
    "饰品": ["饰品", "首饰"],
    "卡牌": ["卡牌", "塔罗牌"],
    "胶囊": ["胶囊", "药丸"],
    "房间": ["房间"],
}


def _enum_target_cats(query: str) -> set:
    """识别"枚举某分类"意图 → 返回应命中的分类集合; 无意图返回空集。"""
    has_cue = any(p in query for p in _ENUM_PHRASES)
    if not has_cue:
        return set()
    out = set()
    for term, cat in _CATEGORY_TERM_ALIAS.items():
        if term in query:  # 分类词已≥2字, 且 query 同时含枚举意图词, 误伤可控
            out.add(cat)
    return out


def _char_overlap(query: str, text: str) -> float:
    """查询字符(去停用词后)在目标文本字符集中的覆盖率, 用于标题级轻量匹配。"""
    q = set(c for c in query if c not in _STOP_CHARS and not c.isspace())
    t = set(text.strip())
    if not q or not t:
        return 0.0
    return sum(1 for c in t if c in q) / len(t)


class HybridRetriever(BaseRetriever):
    """向量召回 + 标题/小节关键词重排 + 同页限块 + 枚举题索引文档置顶 + 条目别名定向。
    关闭时退化为纯向量检索。"""

    def __init__(self, vectorstore, k: int, index_docs: Optional[Dict[str, object]] = None,
                 alias: Optional[object] = None):
        super().__init__()
        self._vs = vectorstore
        self._k = k
        # {category: 分类索引 Document}, 枚举题直接置顶(不靠语义召回)
        self._index_docs = index_docs or {}
        # 条目别名索引: 玩家俗称/英文名 -> 标准标题, 命中则定向召回该条目
        self._alias = alias

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List:
        if not _HYBRID_RERANK:
            return self._vs.similarity_search(query, k=self._k)

        enum_cats = _enum_target_cats(query)
        # 枚举题: 对应分类的「分类索引」文档直接进结果(它就是该题的权威"原文")
        forced = [self._index_docs[c] for c in enum_cats if c in self._index_docs]

        # 条目别名: 用户用俗称/英文名提问(如 AZ/Lost/天使房和恶魔房) → 定向召回这些条目
        alias_titles = self._alias.resolve_all(query) if self._alias else []
        alias_docs = []
        for t in alias_titles:
            alias_docs.extend(self._alias_docs_for(t, query))

        # 别名定向召回最多占一半名额：k 小时若不截断，budget 会被吃成 0，
        # 后面的重排结果一个都进不来（详见文件顶部 _ALIAS_MAX_RATIO 的踩坑说明）
        alias_cap = max(0, int(self._k * _ALIAS_MAX_RATIO))
        if len(alias_docs) > alias_cap:
            alias_docs = alias_docs[:alias_cap]
        budget = max(0, self._k - len(forced) - len(alias_docs))

        # 枚举题查询扩展: 把目标分类词注入查询文本, 让真实成员页进候选窗口
        # (否则「以撒的结合有哪些角色」会被同名/前缀内容垄断, 其他角色页进不了 top-60)
        search_text = query
        if enum_cats:
            extra = " ".join(w for c in enum_cats for w in _CAT_EXPAND.get(c, [c]))
            search_text = f"{query} {extra}".strip()

        # 注意: 用 similarity_search_with_relevance_scores (归一化到 [0,1], 越大越相关),
        # 不要直接拿 similarity_search_with_score 的原始值——FAISS 默认 L2 距离是越小越好,
        # 若按原始值降序会恰好把最不相关的排到最前 (A/B 评测曾实测踩坑, Top1 90%→45%)。
        candidates = self._vs.similarity_search_with_relevance_scores(
            search_text, k=(_ENUM_CANDIDATES if enum_cats else _RERANK_CANDIDATES)
        )

        scored = []
        for doc, rel_score in candidates:
            meta = doc.metadata or {}
            title = meta.get("title", "")
            if title.endswith("分类索引）"):
                continue  # 索引文档只在枚举题经 forced 进入, 避免占正常检索名额

            score = rel_score
            cat = meta.get("category", "")
            if cat in enum_cats:
                score += _ENUM_CAT_BONUS  # 枚举题=分类浏览: 目标分类成员大幅优先

            ov = _char_overlap(query, title)
            if ov:
                score += _TITLE_BONUS * ov

            section = meta.get("section", "")
            if section:
                for kw in _SECTION_KEYWORDS:
                    if kw in query and kw in section:
                        score += _SECTION_KW_BONUS
                        break

            scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)

        # 同页限块: 防止同一页面碎片占满 top-k(如「以撒」页), 保证多样性
        picked: List = []
        picked_titles = {}
        for _, doc in scored:
            t = (doc.metadata or {}).get("title", "")
            if picked_titles.get(t, 0) >= _PAGE_CAP:
                continue
            picked_titles[t] = picked_titles.get(t, 0) + 1
            picked.append(doc)
            if len(picked) >= budget:
                break

        return forced + alias_docs + picked[:budget]

    def _alias_docs_for(self, title: str, query: str = "") -> List:
        """别名命中的条目标题 → 挑该条目下**信息量最高**的几个文档（确定性，不靠语义）。

        坑：原本按 docstore 顺序取前 _PAGE_CAP 个，而 docstore 顺序 = 入库顺序，
            该条目头几段几乎总是 intro 碎片（如「硫磺火 (1/13)」），答非所问。
        对策：①优先挑 section 命中了 query 关键词（效果/解锁/掉落…）的文档；
              ②对同一 section 去重（(1/13)(2/13) 属同一小节，不重复占位），
                保证挑出来的几段覆盖不同信息面。
        """
        ds = self._vs.docstore
        items = getattr(ds, "_dict", None)
        if items is None:
            uuids = list(self._vs.index_to_docstore_id.values())
            items = {u: ds.search(u) for u in uuids}

        hits = []
        for _uuid, doc in items.items():
            meta = getattr(doc, "metadata", None) or {}
            if meta.get("title") == title:
                hits.append(doc)
        if not hits:
            return []

        keys = [kw for kw in _SECTION_KEYWORDS if kw in query] if query else []
        preferred, rest = [], []
        for doc in hits:
            section = (doc.metadata or {}).get("section", "")
            if keys and any(k in section for k in keys):
                preferred.append(doc)
            else:
                rest.append(doc)

        out, seen = [], set()
        for doc in preferred + rest:
            base = (doc.metadata or {}).get("section", "").split(" (")[0]
            if base in seen:
                continue
            seen.add(base)
            out.append(doc)
            if len(out) >= _ALIAS_CAP:
                break
        return out


class QaEngine:
    def __init__(self, game: Optional[str] = None):
        """game: 游戏 id，对应 faiss_index/<game>/ 下的一整套独立索引。
        不传则回退到 .env 的单游戏配置（向后兼容）。"""
        self.game = game or settings.wiki_name
        self.collection_name = self.game
        self.index_dir = index_dir_for(self.game)
        self.embeddings = get_embedding_function()
        self._init_vectorstore()
        self._init_chain()

    def _init_vectorstore(self):
        if not (self.index_dir / "index.faiss").exists():
            raise FileNotFoundError(
                f"游戏 '{self.game}' 的索引不存在: {self.index_dir}\n"
                f"请先构建索引:  python -m src.build_index --game {self.game}"
            )
        self.vectorstore = FAISS.load_local(
            str(self.index_dir),
            self.embeddings,
            allow_dangerous_deserialization=True,
        )
        self._index_docs = self._collect_index_docs()
        self._alias = self._build_alias_index()
        self.retriever = HybridRetriever(
            self.vectorstore, k=20, index_docs=self._index_docs, alias=self._alias,
        )

    def _build_alias_index(self):
        """扫全库 chunks 构建条目别名索引(AZ→阿撒泻勒 / Lost→游魂 / D6→六面骰)。"""
        from .entity_alias import EntityAliasIndex
        alias = EntityAliasIndex()
        chunks_file = data_file(self.game, "chunks.json")
        if chunks_file.exists():
            import json
            with open(chunks_file, encoding="utf-8") as f:
                alias.build(json.load(f))
        return alias

    def _collect_index_docs(self) -> Dict[str, object]:
        """从 FAISS docstore 收集「分类索引」文档 {category: Document},
        供枚举题确定性置顶——不依赖语义召回(索引清单相关度天然偏低)。"""
        out = {}
        ds = self.vectorstore.docstore
        items = getattr(ds, "_dict", None)
        if items is None:
            uuids = list(self.vectorstore.index_to_docstore_id.values())
            items = {u: ds.search(u) for u in uuids}
        for _uuid, doc in items.items():
            meta = getattr(doc, "metadata", None) or {}
            if meta.get("title", "").endswith("分类索引）"):
                cat = meta.get("category", "")
                if cat:
                    out[cat] = doc
        print(f"[QA] 分类索引文档就绪: {sorted(out.keys())}")
        return out

    def _init_chain(self):
        llm = ChatOpenAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )

        prompt = PromptTemplate(
            template=SYSTEM_PROMPT_TEMPLATE,
            input_variables=["context", "question"],
        )

        self.chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=self.retriever,
            return_source_documents=True,
            chain_type_kwargs={"prompt": prompt},
        )

    def ask(self, question: str, history: Optional[List[Dict]] = None) -> Dict:
        """多轮问答。history=[{role,content},...]; 支持用「它/这个」指代前文条目。"""
        history = history or []

        # 轻量指代消解: 检索 query 拼接最近一轮历史, 让「它怎么解锁」能命中上文条目
        query = question
        if history:
            prev = " ".join(f"{h['role']}: {h['content']}" for h in history[-2:])
            query = f"{prev}\n{question}"

        docs = self.retriever.invoke(query)
        context = "\n\n".join(
            f"【{d.metadata.get('title', '')} - {d.metadata.get('section', '')}】\n{d.page_content}"
            for d in docs
        )

        # 历史拼进上下文, 让模型能读懂指代
        hist_text = ""
        if history:
            hist_text = "\n\n历史对话（当前问题可能用'它/这个'指代其中提到的条目）:\n" + \
                "\n".join(f"{h['role']}: {h['content']}" for h in history[-6:])

        system = SYSTEM_PROMPT_TEMPLATE.replace("{context}", context + hist_text).replace("{question}", question)

        if not hasattr(self, "_llm"):
            self._llm = ChatOpenAI(
                model=settings.llm_model,
                temperature=settings.llm_temperature,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
        ai = self._llm.invoke([SystemMessage(content=system), HumanMessage(content=question)])
        answer = ai.content if isinstance(ai.content, str) else str(ai.content or "")

        sources = []
        seen_urls = set()
        for doc in docs:
            meta = doc.metadata or {}
            url = meta.get("url", "")
            if url and url not in seen_urls:
                sources.append({
                    "title": meta.get("title", ""),
                    "section": meta.get("section", ""),
                    "url": url,
                })
                seen_urls.add(url)
            if len(sources) >= 5:
                break

        return {
            "question": question,
            "answer": answer,
            "sources": sources,
        }

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        docs = HybridRetriever(self.vectorstore, k=top_k, index_docs=self._index_docs,
                               alias=self._alias).invoke(query)
        results = []
        for doc in docs:
            meta = doc.metadata or {}
            results.append({
                "text": doc.page_content,
                "title": meta.get("title", ""),
                "section": meta.get("section", ""),
                "url": meta.get("url", ""),
                "category": meta.get("category", ""),
            })
        return results


# 每个游戏一个引擎实例，常驻内存（加载 embedding 模型 + 索引代价高，不能每请求重建）
_engine_instances: Dict[str, QaEngine] = {}


def get_engine(game: Optional[str] = None) -> QaEngine:
    """按游戏取问答引擎：同一游戏复用实例，不同游戏各自独立、互不干扰。"""
    gid = game or settings.wiki_name
    if gid not in _engine_instances:
        _engine_instances[gid] = QaEngine(gid)
    return _engine_instances[gid]


if __name__ == "__main__":
    engine = QaEngine()

    test_questions = [
        "硫磺火道具有什么效果？",
        "怎么解锁 Lost 角色？",
        "以撒有多少个角色？",
    ]

    for q in test_questions:
        print(f"\n{'='*50}")
        print(f"问题: {q}")
        result = engine.ask(q)
        print(f"回答:\n{result['answer']}")
        print(f"来源 ({len(result['sources'])} 个):")
        for s in result["sources"]:
            print(f"  - {s['section']}: {s['url']}")

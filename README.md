# WikiHub — 游戏 Wiki 智能问答（RAG + Agent 双模式）

基于 RAG 的游戏 Wiki 聚合问答系统：玩家用自然语言提问，系统从 Wiki 知识库检索相关内容，由大模型生成带来源链接的答案。
支持**双问答模式**：默认 `rag`（快速、便宜、稳定），可一键切 `agent`（大模型调度工具，本地检索查不到时自动联网兜底，拒答率归零）。

> **MVP 范围**：《以撒的结合》灰机 Wiki，6 个分类 893 页 / 8471 个文档块。架构上可按"配置一个 Wiki + 抓一次数据"的方式扩展更多游戏。

## 技术栈

| 端 | 技术 |
|---|---|
| 后端 | Python 3.13 · FastAPI · LangChain 0.3.x · FAISS（向量库） |
| Embedding | BGE `bge-small-zh-v1.5`（模型已本地化到 `backend/models/`，离线可用） |
| LLM | OpenAI 兼容接口（默认 DeepSeek），支持 function calling |
| Agent | 手写 function calling 循环（不引 agent 框架，工具：本地检索 / 分类清单 / 站外搜索） |
| 前端 | Vue 3 · Vite · Element Plus |

## 数据处理流水线

```
抓取 Wiki 页面  →  清洗 & 按小节切分  →  向量化 & 建索引  →  RAG 问答
wiki_scraper    data_processor        build_index         qa_engine + main.py
```

1. **抓取**：经 MediaWiki API 按分类（道具/人物/卡牌/胶囊/饰品/房间）拉取页面 HTML，落 `data/<game>/raw/wiki_all.json`
2. **处理**：清洗导航/信息框等噪声，按 h2/h3 提取小节文本，递归切分（500/100 重叠），并为每块拼接小节标题作语义锚点，落 `data/<game>/interim/chunks.json`
3. **建索引**：BGE 向量化（带 `data/<game>/interim/embeddings_cache.npz` 缓存，命中则跳过），构建并持久化 FAISS 索引到 `faiss_index/<game>/`
4. **问答**：检索 top20 → 拼入 prompt → LLM 生成答案，去重后返回 top5 来源链接

## 项目结构

```
backend/
├── main.py                # FastAPI 入口（/api/ask、/api/search 等）
├── requirements.txt       # 依赖（已锁定，兼容 Python 3.13）
├── .env.example           # 环境变量示例
├── src/
│   ├── config.py          # 配置（env 驱动）
│   ├── wiki_scraper.py    # Wiki 数据抓取 + 覆盖率校验
│   ├── data_processor.py  # 清洗 / 切分 / 语义锚点
│   ├── build_index.py     # 向量化 + FAISS 索引构建（带缓存）
│   ├── qa_engine.py       # RAG 问答引擎（默认模式）
│   └── agent_engine.py    # Agent 问答引擎（QA_MODE=agent，手写工具循环）
├── data/                  # 数据产物，按游戏 + 阶段分层（全部可重建，不入库）
│   └── <game>/
│       ├── raw/           #   抓取原文（唯一不可再生的）
│       └── interim/       #   切分块 / embedding 缓存（删了只是重建变慢）
├── faiss_index/           # FAISS 索引，按游戏分目录 faiss_index/<game>/（可重建）
├── models/                # 本地 BGE embedding 模型
├── logs/                  # 运行日志（server_run.log，不入库）
└── eval/                  # 检索质量评测（run_eval.py / baseline.json）
frontend/                  # Vue3 聊天 UI（Vite 开发代理到 :8000）
```

## 快速开始

```bash
# 1. 后端（项目根自带 .venv，Python 3.13）
cd backend
pip install -r requirements.txt
cp .env.example .env      # 填入 LLM_API_KEY 等

# 2. 建数据（若 data/ 已有产物可跳过 2~4 直接跑 5）
python -m src.wiki_scraper     # 抓 Wiki → data/<game>/raw/wiki_all.json
python -m src.data_processor   # 切分   → data/<game>/interim/chunks.json
python -m src.build_index      # 建索引 → faiss_index/<game>/

# 3. 启动后端
uvicorn main:app --host 0.0.0.0 --port 8000   # 文档: /docs

# 4. 启动前端
cd frontend && npm install && npm run dev     # http://localhost:5173
```

## 配置（.env）

| 变量 | 说明 | 默认 |
|---|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 大模型（OpenAI 兼容） | DeepSeek |
| `EMBEDDING_PROVIDER` | `local_bge` 或 `openai` | `local_bge` |
| `EMBEDDING_MODEL` | 本地路径或模型名 | `backend/models/bge-small-zh-v1.5` |
| `CHROMA_PERSIST_DIR` | 向量索引目录（沿用旧变量名） | `./faiss_index` |
| `QA_MODE` | 问答模式：`rag`（默认，快速稳定）/ `agent`（工具调度+联网兜底） | `rag` |
| `WIKI_NAME` / `WIKI_API_URL` / `WIKI_BASE_URL` | 目标 Wiki | isaac 灰机 Wiki |

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/ask` | 问答，返回 `answer` + `sources[]`（标题/小节/原文链接）+ `session_id` |
| POST | `/api/search` | 检索 Top-k 文档（调试 / 评测用，不走 LLM） |
| GET | `/api/games` | 已接入的 Wiki 列表 |
| GET | `/health` | 健康检查 |

**多轮对话**：`/api/ask` 传 `session_id`（首次不传则服务端生成并返回）即续接上下文，支持"它/这个"指代前文条目（RAG 模式检索时拼接最近一轮历史做轻量指代消解；agent 模式把历史作为真实对话消息注入）。每会话保留最近 6 轮；前端「✚ 新对话」按钮可开新会话。

## 检索质量评测

`backend/eval/run_eval.py`：20 道真实条目题 × 4 类玩家场景（道具效果/解锁获取/人物角色/机制攻略），只评估**检索（R）**不评估生成（G，依赖 LLM key 有成本）。指标：Top1 命中率、Top3 条目命中率、小节精确率、MRR。

| 版本 | Top1 | Top3 | 小节精确率 | MRR |
|---|---|---|---|---|
| baseline（人物数据缺失时） | 70.0% | 75.0% | 57.1% | 0.725 |
| after（补齐人物分类后） | 90.0% | 100.0% | 57.1% | 0.950 |
| after2（+混合重排 v2） | 100.0% | 100.0% | 71.4% | 1.000 |
| after3（+分类索引/枚举支持） | 100.0% | 100.0% | 71.4% | 1.000 |

枚举/聚合题（after3 起新增题型，`eval/run_eval.py` 的 `enum_summary`）：

| 版本 | 通过率（5 题） | 分类索引命中 |
|---|---|---|
| after3 | 100.0% (5/5) | 100.0% |

迭代记录：

1. **补数据**：baseline 中"人物角色"类 5/5 题全部检索失败（0 命中），根因是分类名写错——人物挂在「人物」分类而非「角色」，MediaWiki 返回空列表而非报错，属"静默数据缺失"。补抓 34 个人物页并重建索引 → 人物类 0% → 100%
2. **混合重排**：针对"标题混淆"与"小节排位"做轻量重排（`qa_engine.py`：向量相似度为主，叠加标题字符/小节关键词小加成，`HYBRID_RERANK=0` 可关闭做 A/B）→ Top1 90% → 100%、小节精确率 57.1% → 71.4%
3. **分类索引文档 + 枚举支持**（治"以撒有哪些角色"拒答）：chunk 透传 `category` 元数据；为每个分类生成「X（分类索引）」清单文档（成员数严格取自知识库，撞抓取上限会如实标注）；检索加同页限块防"以撒页垄断"，识别枚举意图（有哪些/列出/多少种/哪几种 + 分类词，如"角色"→人物）时**确定性置顶**对应索引文档并做"分类浏览"召回。实测：该问题从"抱歉我不太清楚" → 完整 34 角色列表 + 可引用的分类页来源
4. **Agent 模式（手写工具循环，QA_MODE=agent）**：大模型当调度员，自选工具——`search_wiki` 本地检索 / `list_category` 分类清单 / `web_search` 联网兜底（灰机 Wiki 站内搜索，无需额外 API key）。与 RAG 共享同一份 FAISS/索引文档（不重复加载），普通 RAG 逻辑原样保留，改 `.env` 的 `QA_MODE` 即可切换。
5. **多轮对话（会话历史）**：`/api/ask` 支持 `session_id` 续接上下文（服务端内存存储，保留最近 6 轮）。RAG 模式检索时拼接最近一轮历史做轻量指代消解（"它怎么解锁"能命中前文条目）；agent 模式把历史作为真实对话消息注入。前端顶栏「✚ 新对话」开新会话。
6. **按需抓取去重 + 重定向/解锁条件修复**：`fetch_wiki_page` 抓到重定向页时原本只拿到骨架页（金心→实体/5/10#金心），加 `redirects=1` 跟随重定向；infobox 里的「解锁条件」曾被 `SKIP_TAGS=table.infobox` 误删，改为保留含成就/解锁条件的 infobox。并修重复入库：已在库的页面直接返回已有内容、不再重复 add_texts（此前游魂页被重复入库 3 遍共 84 块，全库 193 块完全重复，检索被稀释）。
7. **条目别名层（Entity Alias，治"俗称提问搜不到"）**：用户用「AZ / Lost / D6 / 科技X / 天使房和恶魔房」这类中文名/英文名/缩写提问时，语义检索会瞎匹配（"AZ"被匹配到符文页）。新增 `entity_alias.py`：扫全库提取每条目开头「中文名（English）」的英文名 + 人工俗称字典，构建 `别名→标准标题` 映射。检索时先做**别名识别**（支持多条目、按 query 出现顺序排序），命中则**确定性定向召回**该条目标题下的文档（与分类索引置顶同一套"能确定就不靠语义"哲学）。RAG 和 agent 的 `search_wiki` 共用。实测：AZ→阿撒泻勒、Lost/the lost→游魂、D6→六面骰、科技X 不再被"科技"劫持、天使房和恶魔房双召回且按提问顺序排列；eval 回归 Top1/MRR 保持 100%/1.000。

### 成本/延迟基线（同 7 道代表题，真实流程，`eval/measure_baseline.py`）

| 指标 | RAG 基线 | Agent v1 | 变化 |
|---|---|---|---|
| 平均耗时 | 9.97s | **6.50s** | ↓35% |
| 拒答率 | 14.3%（1/7） | **0.0%** | **拒答清零** |
| 平均 token/题（估算） | ~1123 | ~1215 | ↑8% |

解读：agent 对简单题更会"抄近路"（枚举题一次 `list_category` 即得，比 RAG 语义检索更快），对 RAG 拒答的题（如"圣心怎么解锁"→ 属天使房道具池、无独立解锁成就）则多轮检索 + 联网兜底作答——**用可控成本（+8% token）换拒答率归零**，属于"慢一点也要答出来"的取舍。Agent 版实测 `圣心` 单题 13.7s vs RAG 拒答 1.8s。

> 说明：eval 为严格的标题/小节字符串匹配。重排 v1 曾因排序方向写反（FAISS L2 距离越小越好）被评测当场拦下（Top1 一度崩到 45%），修复后达标——**检索/数据任何改动都应以 eval 回归为准**。剩余 2 题小节 miss（圣心/伯大尼的「解锁」小节）属页面结构/内容缺口，非排序问题。

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000   # 需先起服务
python eval/run_eval.py after2                # 跑评测, 生成 eval/after2.json
python eval/compare.py baseline after2        # 与基线自动对比 (任意两版皆可)
```

### 评测集持续生长（bad cases 自动进集）

人工设计的 25 题是"回归底盘"，但**真实用户踩过的坑要靠坏例反哺**，否则 25 题 100% 会掩盖盲区（历史教训：金心重定向/解锁条件、枚举题都是用户测出来才补进集的）。

**新增一个坏例 = 加一条 JSON**，下次评测自动带上并单独报 Bad Case 通过率（与人工题分开统计，防污染指标）：

```jsonc
// backend/eval/bad_cases.json 追加：
{
  "q": "腐心是什么效果",          // 真实用户问题
  "cat": "机制攻略",             // 分类(详情题)或 聚合-XX(枚举题)
  "kind": "detail",             // detail=判标题/小节命中; enum=判分类索引+多样性
  "expect_title": "心",          // kind=detail 时: 应命中的页面标题
  "expect_sec": "腐心",          // 可选: 期望小节(含此词即算小节命中)
  "note": "从坏例文档迁移"        // 备注来源
}
```

坏例字段说明：
- **detail 类**（有标准答案）：`expect_title` 必填，`expect_sec` 可选（要严格校验小节就填）
- **enum 类**（聚合/列举）：`expect_index_cat`（应命中的分类）+ `min_distinct`（top-N 内至少几个该分类条目）
- 无标准答案的开放题（如"有什么主动道具生成金心"）**不适合自动判**，记入 `eval/bad_cases.md` 人工跟踪即可

流程：发现真实问题 → 修好后**永久留在 bad_cases.json** → 任何后续改动都要过它（防回归）。运行 `run_eval.py` 时会在输出里看到 `Bad Case 通过率` 独立一行。

## 开发中的经验沉淀

- **数据"静默缺失"陷阱**：MediaWiki 分类名写错时返回空列表而非报错，抓取后按分类做覆盖率校验（`report_coverage`），0 页立即告警
- **向量索引按数据源重建**：改切分逻辑/文本后需删 `embeddings_cache.npz` 再重建，否则缓存命中跳过向量化导致索引与数据不一致
- **chunk 前缀锚点**：切分后再拼小节标题，避免长块被拆后后续片段丢失标题信号
- **评测驱动迭代**：检索/数据任何改动都跑 `run_eval.py` + `compare.py` 回归。重排 v1 排序方向写反导致 Top1 90% → 45%，正是被评测当场拦下——没有量化评测，这类坏改动会静默上线
- **评测集要持续生长，不能只靠人工题**：25 题人工题是底盘，但真实用户踩的坑（枚举、重定向、解锁条件）必须反哺进集（`bad_cases.json`），否则 100% 会掩盖盲区；坏例与人工题分开统计，避免"测的都是自己会做的题"
- **排序方向坑**：FAISS 默认 L2 距离越小越相似，自写排序须用 `similarity_search_with_relevance_scores`（已归一化）或显式按升序排
- **枚举/查表题 ≠ 语义检索**："有哪些角色"这类聚合题的答案是多页并集，单页无原文——正确做法是准备**结构化清单文档**（分类索引）让枚举走"查表"，而非让模型跨页脑补；评测也要单独补这类反向题型，否则 100% 会掩盖盲区
- **Agent 是兜底不是替代**：简单问题一步 RAG 又快又稳（还更便宜），agent 的价值在"检索不到时能自己想办法"（换词重查 / 查分类 / 联网）。手写工具循环（不引框架）在工具少、流程线性时逻辑全透明，每步可讲清——比套框架更值得写进简历
- **改 agent 前先留成本基线**：`eval/measure_baseline.py` 在改造前后跑同一批题，用「耗时 / token / 拒答率」的相对变化判断改造值不值，别只比命中率
- **增量入库必须去重**：按需抓取每次 `add_texts` 都会把整页重写进 FAISS，若不加"页面已在库"判断，重复抓同一页会累积重复向量（游魂页 84 块 = 28 块 × 3，全库 193 块完全重复），稀释检索质量——增量写入前先查 chunks.json 判重
- **重定向页只抓骨架**：MediaWiki `action=parse` 对重定向页默认返回"重定向到 XX"的骨架（金心→实体/5/10#金心），需显式 `redirects=1` 才会返回目标页正文；`SKIP_TAGS` 里别写 `table.infobox`，否则 infobox 内的「解锁条件」会被提前误删
- **俗称提问要靠别名层，不能靠语义**：wiki 条目有「中文名/英文名/玩家俗称」三套名字（AZ=阿撒泻勒、Lost=游魂、D6=六面骰），向量检索只认中文标题，用户用俗称提问必然瞎匹配。系统性解法是构建「别名→标准标题」映射并确定性置顶，而不是逐个补数据。注意：中文标题匹配用原始 query（不能小写，否则"科技X"的 X 对不上）；通用词（道具/心/房间）要设停用词防误导向；多条目（天使房和恶魔房）按 query 出现顺序排序

## 优化路线图

**P0（已完成）**：人物数据补齐（0%→100%）· 混合重排（Top1 90→100%，小节 57.1→71.4%）· embedding 缓存 bug 修复 · eval 对比工具 `compare.py` · 分类索引文档与枚举题支持（原拒答问题 → 34 角色全表 + 分类来源；枚举题 5/5）· **Agent 双模式**（QA_MODE 切换；拒答率 14.3% → 0%，耗时 -35%）

**P1（建议下一步）**
- ~~多轮对话~~：✅ 已完成（`session_id` 会话历史 + 指代消解，见上文 API 说明）
- Agent 精调：给 `search_wiki` 的查询结果带回传来源、web_search 支持抓取正文（当前仅标题/摘要）、限制单题工具轮数上限（圣心题 9 轮偏多）
- 生成质量（G）抽评：LLM-as-judge 小样本 + 人工复核（控制 key 成本）
- 坏例反馈闭环：前端答案 👍/👎 → 扩充评测题集（当前仅 20 题，样本偏小）
- 数据缺口核查：圣心/伯大尼「解锁」小节是否缺内容
- 工程化：单元/冒烟测试、git 化、Docker 部署

**P2（远期）**：多游戏接入 + 问题自动路由 · 跨游戏对比问答 · BM25+向量混合检索与真 reranker 模型 · 流式输出 · 增量更新与埋点

## 数据来源与 License

Wiki 数据来自 [以撒的结合灰机 Wiki](https://isaac.huijiwiki.com/)，仅供学习交流。MIT License。

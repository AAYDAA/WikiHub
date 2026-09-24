# -*- coding: utf-8 -*-
"""
WikiHub 检索质量评估脚本（产品岗视角的效果量化）

用法:
    python eval/run_eval.py baseline     # 历史基线(人物数据缺失前)
    python eval/run_eval.py after        # 补人物数据后
    python eval/run_eval.py after2       # 混合重排后(自定 tag, 任意跑)

题型分两类:
- 详情题(默认): "给定条目名 → 应召回到该条目页/小节", 判 Top1/Top3/MRR/小节精确率
- 枚举题(kind=enum): "列举某分类下的成员"(如"以撒有哪些角色"), 无单页原文,
  判据 = ① 分类索引文档命中(可引用的"原文") ② 召回到 ≥min_distinct 个该分类条目
  详情与枚举分开汇总, 避免口径混淆; 枚举题数据存 eval/<tag>.json 的 enum_summary

每次结果存 eval/<tag>.json, 用 eval/compare.py 与任意版本对比。
"""
import json
import os
import sys
import urllib.request

API = "http://127.0.0.1:8000/api/search"
TOP_K = 3
ENUM_TOP_K = 12          # 枚举题需要看更多召回才能数多样性
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))

# ============ 详情题: 全部基于 wiki 真实存在的条目名设计（不编造） ============
QUESTIONS = [
    # A. 道具效果（8 题）
    {"q": "硫磺火有什么效果", "expect_title": "硫磺火", "expect_sec": "效果", "cat": "道具效果"},
    {"q": "科技X有什么效果", "expect_title": "科技X", "expect_sec": "效果", "cat": "道具效果"},
    {"q": "妈妈的菜刀怎么用", "expect_title": "妈妈的菜刀", "expect_sec": None, "cat": "道具效果"},
    {"q": "鲁多维科科技有什么效果", "expect_title": "鲁多维科科技", "expect_sec": None, "cat": "道具效果"},
    {"q": "吐根酊有什么作用", "expect_title": "吐根酊", "expect_sec": None, "cat": "道具效果"},
    {"q": "圣光有什么效果", "expect_title": "圣光", "expect_sec": "效果", "cat": "道具效果"},
    {"q": "断魂椒有什么用", "expect_title": "断魂椒", "expect_sec": None, "cat": "道具效果"},
    {"q": "胎儿博士的效果是什么", "expect_title": "胎儿博士", "expect_sec": "效果", "cat": "道具效果"},
    # B. 解锁获取（4 题）
    {"q": "天秤座怎么解锁", "expect_title": "天秤座", "expect_sec": "解锁", "cat": "解锁获取"},
    {"q": "亚巴顿怎么获得", "expect_title": "亚巴顿", "expect_sec": None, "cat": "解锁获取"},
    {"q": "兽性面具在哪里掉落", "expect_title": "兽性面具", "expect_sec": None, "cat": "解锁获取"},
    {"q": "圣心怎么解锁", "expect_title": "圣心", "expect_sec": "解锁", "cat": "解锁获取"},
    # C. 人物角色（5 题）
    {"q": "阿撒泻勒有什么能力", "expect_title": "阿撒泻勒", "expect_sec": None, "cat": "人物角色"},
    {"q": "参孙的特点是什么", "expect_title": "参孙", "expect_sec": None, "cat": "人物角色"},
    {"q": "伯大尼怎么解锁", "expect_title": "伯大尼", "expect_sec": "解锁", "cat": "人物角色"},
    {"q": "该隐初始带什么道具", "expect_title": "该隐", "expect_sec": None, "cat": "人物角色"},
    {"q": "以撒的初始属性是什么", "expect_title": "以撒", "expect_sec": None, "cat": "人物角色"},
    # D. 机制攻略（3 题）
    {"q": "恶魔房怎么开启", "expect_title": "恶魔房", "expect_sec": None, "cat": "机制攻略"},
    {"q": "天使房和恶魔房有什么区别", "expect_title": "天使房", "expect_sec": None, "cat": "机制攻略"},
    {"q": "隐藏房怎么找", "expect_title": "隐藏房", "expect_sec": None, "cat": "机制攻略"},
]

# ============ 枚举题: 无单页原文, 判分类索引命中 + 多样性 ============
# expect_index_cat: 应命中的分类索引文档的分类; min_distinct: top-N 内至少召回到几个该分类条目
ENUM_QUESTIONS = [
    {"q": "以撒的结合有哪些角色", "cat": "聚合-人物", "expect_index_cat": "人物", "min_distinct": 6},
    {"q": "以撒里有哪些道具", "cat": "聚合-道具", "expect_index_cat": "道具", "min_distinct": 6},
    {"q": "游戏里有哪几种卡牌", "cat": "聚合-卡牌", "expect_index_cat": "卡牌", "min_distinct": 5},
    {"q": "有哪些饰品", "cat": "聚合-饰品", "expect_index_cat": "饰品", "min_distinct": 6},
    {"q": "胶囊一共有多少种", "cat": "聚合-胶囊", "expect_index_cat": "胶囊", "min_distinct": 5},
]

DETAIL_CATS = ["道具效果", "解锁获取", "人物角色", "机制攻略"]


# ============ Bad Cases 自动进集 ============
# bad_cases.json: 真实用户踩过的坑/发现的问题, 追加进去后下次评测自动带上,
# 与人工设计的 25 题分开统计(避免"人工题 100%"掩盖真实问题命中率)。
_BAD_CASES_PATH = os.path.join(OUT_DIR, "bad_cases.json")


def load_bad_cases() -> dict:
    """返回 {"detail": [...], "enum": [...], "cross": [...]}, 文件不存在/损坏时返回空。"""
    out = {"detail": [], "enum": [], "cross": []}
    if not os.path.exists(_BAD_CASES_PATH):
        return out
    try:
        with open(_BAD_CASES_PATH, encoding="utf-8") as f:
            items = json.load(f)
    except Exception as e:
        print(f"[Eval] bad_cases.json 读取失败, 忽略: {e}")
        return out
    for it in items:
        row = dict(it)
        row["src"] = "bad_case"  # 标记来源, 汇总时单独统计
        if row.get("kind") == "enum":
            out["enum"].append(row)
        elif row.get("kind") == "cross":
            out["cross"].append(row)
        else:
            out["detail"].append(row)
    return out


def search(query, top_k=TOP_K):
    body = json.dumps({"query": query, "top_k": top_k}).encode("utf-8")
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def _is_index_doc(title):
    return title.endswith("分类索引）")


def run_detail(rows, item, i):
    """详情题: 标题 rank + 小节命中。"""
    try:
        data = search(item["q"])
    except Exception as e:
        print("[%2d] %s  请求失败: %s" % (i, item["q"], e))
        rows.append({**item, "error": str(e), "rank": 0, "sec_ok": False, "tops": []})
        return

    tops = [(r.get("title", ""), r.get("section", "")) for r in data.get("results", [])]
    rank, sec_ok = 0, False
    for pos, (t, s) in enumerate(tops, 1):
        if t == item["expect_title"]:
            if rank == 0:
                rank = pos
            if item.get("expect_sec") and item["expect_sec"] in s:
                sec_ok = True

    rows.append({**item, "rank": rank, "sec_ok": sec_ok, "tops": tops})
    mark = "命中#%d" % rank if rank else "未命中"
    print("\n[%2d] %s  (%s)" % (i, item["q"], item["cat"]))
    print("     期望: %s%s" % (
        item["expect_title"],
        " + 小节[%s]" % item["expect_sec"] if item.get("expect_sec") else "",
    ))
    for pos, (t, s) in enumerate(tops, 1):
        flag = " <<<" if t == item["expect_title"] else ""
        print("     top%d: %s | %s%s" % (pos, t, s, flag))
    print("     => %s" % mark)


def run_enum(rows, item, i):
    """枚举题: 分类索引命中 && 该分类条目多样性达标。"""
    try:
        data = search(item["q"], top_k=ENUM_TOP_K)
    except Exception as e:
        print("\n[E%2d] %s  请求失败: %s" % (i, item["q"], e))
        rows.append({**item, "error": str(e), "rank": 0, "index_hit": False, "distinct": 0, "tops": []})
        return

    res = data.get("results", [])
    index_hit = any(_is_index_doc(r.get("title", "")) for r in res)
    cat = item["expect_index_cat"]
    # 多样性 = top-N 内「非索引文档」且分类匹配的条目标题数
    distinct = len({r.get("title", "") for r in res
                    if not _is_index_doc(r.get("title", ""))
                    and r.get("category", "") == cat})
    ok = index_hit and distinct >= item["min_distinct"]
    rows.append({**item, "rank": 1 if ok else 0, "index_hit": index_hit,
                 "distinct": distinct, "tops": [(r.get("title", ""), r.get("section", "")) for r in res]})

    print("\n[E%2d] %s  (%s)" % (i, item["q"], item["cat"]))
    print("     期望: 命中『%s』分类索引 + ≥%d 个%s条目" % (cat, item["min_distinct"], cat))
    for pos, (t, s) in enumerate([(r.get("title", ""), r.get("section", "")) for r in res], 1):
        tag = "  <<< 索引文档" if _is_index_doc(t) else ""
        print("     top%2d: %s | %s%s" % (pos, t, s, tag))
    print("     => %s (索引命中=%s, 该分类条目数=%d)" % (
        "通过" if ok else "未通过", index_hit, distinct))


def run_cross(rows, item, i):
    """跨实体/反向聚合题: 无标准答案页(答案散落在多页), 不判对错,
    只记录检索到的 top 结果供人工观察——这是后续做反向索引/多跳的积累数据。
    期望行为(人工判断): 能否召回到"与答案相关的候选页"。"""
    try:
        data = search(item["q"], top_k=TOP_K)
        res = data.get("results", [])
        tops = [(r.get("title", ""), r.get("section", "")) for r in res]
        rows.append({**item, "error": None, "tops": tops})
        print("\n[C%2d] %s  (%s)" % (i, item["q"], item.get("cat", "")))
        for pos, (t, s) in enumerate(tops[:8], 1):
            print("     top%2d: %s | %s" % (pos, t, s))
        print("     => 人工观察: 检索到的候选页是否构成回答线索")
    except Exception as e:
        print("\n[C%2d] %s  请求失败: %s" % (i, item["q"], e))
        rows.append({**item, "error": str(e), "tops": []})


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    bad = load_bad_cases()
    all_detail = [dict(q) for q in QUESTIONS] + bad["detail"]
    all_enum = [dict(q) for q in ENUM_QUESTIONS] + bad["enum"]
    print("=" * 78)
    print("WikiHub 检索质量评估  [tag=%s]  详情题 %d(含bad_case %d) + 枚举题 %d(含bad_case %d) + 跨实体题 %d" % (
        tag, len(all_detail), len(bad["detail"]), len(all_enum), len(bad["enum"]), len(bad["cross"])))
    print("=" * 78)

    rows = []
    for i, item in enumerate(all_detail, 1):
        run_detail(rows, item, i)

    print("\n" + "=" * 78)
    print("枚举题(分类索引命中 + 多样性):")
    enum_rows = []
    for j, item in enumerate(all_enum, 1):
        run_enum(enum_rows, item, j)

    print("\n" + "=" * 78)
    print("跨实体/反向聚合题(收集阶段, 不判对错, 人工观察检索线索):")
    cross_rows = []
    for k, item in enumerate(bad["cross"], 1):
        run_cross(cross_rows, item, k)

    # ---- 详情题汇总 ----
    total = len(rows)
    ok_rows = [r for r in rows if not r.get("error")]
    n = len(ok_rows)
    top1 = sum(1 for r in ok_rows if r["rank"] == 1)
    top3 = sum(1 for r in ok_rows if r["rank"] > 0)
    sec_rows = [r for r in ok_rows if r.get("expect_sec")]
    sec_hit = sum(1 for r in sec_rows if r["sec_ok"])
    mrr = sum(1.0 / r["rank"] for r in ok_rows if r["rank"] > 0) / n if n else 0

    print("\n" + "=" * 78)
    print("汇总 [tag=%s] 详情题" % tag)
    print("=" * 78)
    print("有效题数: %d / %d" % (n, total))
    print("Top1 命中率(严格):     %.1f%%  (%d/%d)" % (top1 * 100.0 / n, top1, n))
    print("Top3 条目命中率:       %.1f%%  (%d/%d)" % (top3 * 100.0 / n, top3, n))
    if sec_rows:
        print("小节精确率(校验小节的题): %.1f%%  (%d/%d)" % (
            sec_hit * 100.0 / len(sec_rows), sec_hit, len(sec_rows)))
    print("MRR:                   %.3f" % mrr)

    cat_stat = {}
    for c in DETAIL_CATS:
        sub = [r for r in ok_rows if r["cat"] == c]
        hit = sum(1 for r in sub if r["rank"] > 0)
        cat_stat[c] = {"total": len(sub), "top3_hit": hit,
                       "rate": round(hit * 100.0 / len(sub), 1) if sub else 0}
        print("  %-8s Top3命中 %d/%d  (%.1f%%)" % (c, hit, len(sub), cat_stat[c]["rate"]))

    # ---- 枚举题汇总 ----
    enum_ok_rows = [r for r in enum_rows if not r.get("error")]
    en = len(enum_ok_rows)
    e_ok = sum(1 for r in enum_ok_rows if r["rank"] == 1)
    e_idx = sum(1 for r in enum_ok_rows if r["index_hit"])
    print("\n汇总 [tag=%s] 枚举题" % tag)
    print("有效题数: %d / %d" % (en, len(enum_rows)))
    print("通过率(索引命中且多样性达标): %.1f%%  (%d/%d)" % (e_ok * 100.0 / en if en else 0, e_ok, en))
    print("分类索引命中率:            %.1f%%  (%d/%d)" % (e_idx * 100.0 / en if en else 0, e_idx, en))

    # ---- Bad Cases 单独汇总(真实用户问题, 与人工题分开) ----
    bad_rows = [r for r in rows + enum_rows if r.get("src") == "bad_case" and not r.get("error")]
    bn = len(bad_rows)
    b_ok = sum(1 for r in bad_rows if r.get("rank") and r["rank"] > 0)
    if bn:
        print("\n汇总 [tag=%s] Bad Cases(真实问题, 含于上表)" % tag)
        print("Bad Case 通过率: %.1f%%  (%d/%d)" % (b_ok * 100.0 / bn, b_ok, bn))
        for r in bad_rows:
            mark = "✓" if r.get("rank", 0) > 0 else "✗"
            print("  %s [%s] %s" % (mark, r.get("cat", ""), r.get("q", "")))

    out = {
        "tag": tag,
        "summary": {   # 详情题口径(与历史版本可比)
            "total": total, "valid": n,
            "top1": top1, "top1_rate": round(top1 * 100.0 / n, 1) if n else 0,
            "top3": top3, "top3_rate": round(top3 * 100.0 / n, 1) if n else 0,
            "sec_hit": sec_hit, "sec_total": len(sec_rows),
            "sec_rate": round(sec_hit * 100.0 / len(sec_rows), 1) if sec_rows else 0,
            "mrr": round(mrr, 3),
        },
        "enum_summary": {
            "total": len(enum_rows), "valid": en,
            "ok": e_ok, "ok_rate": round(e_ok * 100.0 / en, 1) if en else 0,
            "index_hit": e_idx, "index_hit_rate": round(e_idx * 100.0 / en, 1) if en else 0,
        },
        "bad_case_summary": {
            "total": bn, "ok": b_ok,
            "ok_rate": round(b_ok * 100.0 / bn, 1) if bn else 0,
        },
        "by_category": cat_stat,
        "rows": rows,
        "enum_rows": enum_rows,
        "cross_rows": cross_rows,
    }
    path = os.path.join(OUT_DIR, "%s.json" % tag)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n结果已保存: %s" % path)


if __name__ == "__main__":
    main()

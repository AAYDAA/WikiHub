# -*- coding: utf-8 -*-
"""
WikiHub 评测结果对比工具

用法:
    python eval/compare.py                 # baseline vs after
    python eval/compare.py after2          # baseline vs after2
    python eval/compare.py after after2    # 任意两版对比

每次改动检索/数据后: 跑 run_eval.py <tag> 生成新结果, 再用本脚本对比,
把"这次改动到底有没有变好"变成一行可读的数字 —— 数据驱动迭代的收尾动作。
"""
import json
import os
import sys

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))


def load(tag):
    path = os.path.join(OUT_DIR, "%s.json" % tag)
    if not os.path.exists(path):
        print("找不到结果文件: %s (先运行 run_eval.py %s)" % (path, tag))
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_rate(v):
    return "%5.1f%%" % v if v is not None else "   n/a"


def main():
    tags = sys.argv[1:] if len(sys.argv) > 1 else ["baseline", "after"]
    if len(tags) == 1:
        tags = ["baseline", tags[0]]

    base = load(tags[0])
    cur = load(tags[1])
    bs, cs = base["summary"], cur["summary"]

    print("=" * 66)
    print("评测对比: %s (基线)  vs  %s (当前)" % (tags[0], tags[1]))
    print("=" * 66)

    keys = [
        ("top1_rate", "Top1 命中率"),
        ("top3_rate", "Top3 条目命中率"),
        ("sec_rate", "小节精确率"),
        ("mrr", "MRR"),
    ]
    for k, label in keys:
        b, c = bs.get(k), cs.get(k)
        if k == "mrr":
            b_s, c_s = ("%.3f" % b) if b is not None else "n/a", ("%.3f" % c) if c is not None else "n/a"
            delta = ("+%.3f" % (c - b)) if (b is not None and c is not None) else "  -"
            print("%-14s 基线 %-8s → 当前 %-8s  (%s)" % (label, b_s, c_s, delta))
        else:
            b_s, c_s = fmt_rate(b), fmt_rate(c)
            delta = ("%+.1fpp" % (c - b)) if (b is not None and c is not None) else "   -"
            print("%-14s 基线 %-10s → 当前 %-10s  (%s)" % (label, b_s, c_s, delta))

    print("-" * 66)
    print("分类 Top3 命中率:")
    all_cats = []
    for r in cur.get("by_category", {}):
        if r not in all_cats:
            all_cats.append(r)
    for cat in all_cats:
        bb = base.get("by_category", {}).get(cat, {})
        cc = cur["by_category"][cat]
        b_hit, b_tot = bb.get("top3_hit", 0), bb.get("total", 0)
        c_hit, c_tot = cc["top3_hit"], cc["total"]
        print("  %-8s %d/%d → %d/%d" % (cat, b_hit, b_tot, c_hit, c_tot))

    # 枚举题(如存在)
    be, ce = base.get("enum_summary"), cur.get("enum_summary")
    if ce:
        b_str = "n/a"
        if be:
            b_str = "%d/%d (%.0f%%)" % (be.get("ok", 0), be.get("valid", 0), be.get("ok_rate", 0))
        print("-" * 66)
        print("枚举题通过率: 基线 %s → 当前 %d/%d (%.0f%%)" % (
            b_str, ce.get("ok", 0), ce.get("valid", 0), ce.get("ok_rate", 0)))

    # 逐题差异: 基线命中但当前未命中 / 反之
    print("-" * 66)
    base_rank = {r["q"]: r["rank"] for r in base["rows"]}
    cur_rows = {r["q"]: r for r in cur["rows"]}
    changed = []
    for q in sorted(set(base_rank) | set(cur_rows)):
        br = base_rank.get(q)
        cr = cur_rows[q]["rank"] if q in cur_rows else 0
        if (br == 0) != (cr == 0):
            changed.append((q, br, cr))
    if changed:
        print("命中状态变化的题目:")
        for q, br, cr in sorted(changed, key=lambda x: x[0]):
            print("  [%s] %s: %s → %s" % ("修复" if br == 0 else ("回退" if cr == 0 else "变化"), q, br or "未命中", cr or "未命中"))
    else:
        print("逐题命中状态无变化")


if __name__ == "__main__":
    main()

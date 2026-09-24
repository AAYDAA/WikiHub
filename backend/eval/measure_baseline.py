# -*- coding: utf-8 -*-
"""
成本/延迟基线测量脚本（改 agent 前必跑，钉死普通 RAG 的对照数据）

用法:
    python eval/measure_baseline.py              # 默认存 eval/cost_rag_baseline.json
    python eval/measure_baseline.py agent_v1     # 改 agent 后跑同一批题, 存 cost_agent_v1.json

思路: 同一批代表题 + 同一脚本 + 同一服务, 在改 agent 前后各跑一次,
对比「平均耗时 / token 估算 / 拒答率 / 平均来源数」的相对变化, 判断 agent 改造值不值。
token 为估算值(中文≈0.7 token/字), 两版口径一致, 相对对比有效;
agent 版额外关注「调用轮次」(届时本脚本需增测 LLM 调用次数)。

服务需先启动: uvicorn main:app --host 127.0.0.1 --port 8000
"""
import json
import os
import sys
import time
import urllib.request

API = "http://127.0.0.1:8000"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))

# 代表题: 覆盖各题型(详情/枚举各类型)
QUESTIONS = [
    {"q": "硫磺火有什么效果？", "type": "详情-道具"},
    {"q": "圣心怎么解锁？", "type": "详情-解锁"},
    {"q": "该隐初始带什么道具？", "type": "详情-人物"},
    {"q": "天使房和恶魔房有什么区别？", "type": "详情-机制"},
    {"q": "以撒的结合有哪些角色", "type": "枚举-人物"},
    {"q": "有哪些饰品", "type": "枚举-饰品"},
    {"q": "胶囊一共有多少种", "type": "枚举-胶囊"},
]

REFUSE_KW = ("不太清楚", "暂时", "建议查看", "不知道", "无法", "我无法")


def post(path, payload, timeout=240):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API + path, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data, time.time() - t0


def est_tokens(text):
    """中文粗略估算: 1 字 ≈ 0.7 token(口径两版一致, 相对对比有效)。"""
    return int(len(text or "") * 0.7)


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "rag_baseline"
    print("成本/延迟基线 [tag=%s]  %d 题" % (tag, len(QUESTIONS)))
    rows = []
    for i, item in enumerate(QUESTIONS, 1):
        try:
            data, dt = post("/api/ask", {"question": item["q"]})
        except Exception as e:
            print("[%2d] %s  请求失败: %s" % (i, item["q"], e))
            rows.append({**item, "error": str(e)})
            continue

        ans = data.get("answer", "")
        refused = any(k in ans for k in REFUSE_KW)
        sources = data.get("sources", [])

        # 检索输入估算: 单独调 search 拿 top5 文本长度(本地检索, 不花钱)
        sdata, _sdt = post("/api/search", {"query": item["q"], "top_k": 5}, timeout=120)
        input_text = "".join(r.get("text", "") for r in sdata.get("results", []))
        in_tok = est_tokens(input_text)
        out_tok = est_tokens(ans)

        rows.append({
            **item,
            "latency_s": round(dt, 2),
            "refused": refused,
            "n_sources": len(sources),
            "est_in_tokens": in_tok,
            "est_out_tokens": out_tok,
            "est_total_tokens": in_tok + out_tok,
        })
        print("[%2d] %-18s(%s)  %.1fs 拒答=%s 来源%d  ~%d tok" % (
            i, item["q"][:16], item["type"], dt, refused, len(sources), in_tok + out_tok))

    ok = [r for r in rows if not r.get("error")]
    n = len(ok)
    summary = {
        "tag": tag,
        "n": n,
        "avg_latency_s": round(sum(r["latency_s"] for r in ok) / n, 2) if n else 0,
        "refuse_rate": round(sum(1 for r in ok if r["refused"]) * 100.0 / n, 1) if n else 0,
        "avg_est_tokens": round(sum(r["est_total_tokens"] for r in ok) / n, 0) if n else 0,
        "avg_sources": round(sum(r["n_sources"] for r in ok) / n, 2) if n else 0,
    }
    print("\n=== 汇总 [%s] ===" % tag)
    print("平均耗时 %.2fs | 拒答率 %.1f%% | 平均 ~%d token/题 | 平均来源 %.1f" % (
        summary["avg_latency_s"], summary["refuse_rate"], summary["avg_est_tokens"], summary["avg_sources"]))

    out = {"summary": summary, "rows": rows}
    path = os.path.join(OUT_DIR, "cost_%s.json" % tag)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("结果已保存: %s" % path)


if __name__ == "__main__":
    main()

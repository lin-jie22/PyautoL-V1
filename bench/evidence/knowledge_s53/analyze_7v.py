#!/usr/bin/env python3
"""★7v-a 分析（零模型）：墙钟耦合 ⇒ 通道分叉？两配置对照。

读数里**没有按题通道标记**（kernel_closeout 是整轮汇总，7v-a 前置 2 已确认），
所以通道只能从 run log 按题段落人工判读 —— 本脚本把它自动化，判据照
DESIGN §19quinquies 登记写死。
"""
import json
import re
import sys

D = "/tmp/v1_7v"
V1 = "/Users/linshaojie/V1"
RUNS = [("W120", "r1"), ("W120", "r2"), ("W120", "r3"),
        ("W600", "r1"), ("W600", "r2"), ("W600", "r3")]


def channel_of(block: str) -> str:
    """从一题的日志段落里判通道。优先级按"最后决定交付的那条路"。"""
    if "📦 收尾交付" in block:
        if "预算耗尽 [wall_s]" in block:
            return "收尾/wall"
        if "全部候选被拦" in block or "否决" in block:
            return "收尾/被拦"
        if "预算耗尽 [steps" in block:
            return "收尾/steps"
        return "收尾/其他"
    if "↳ finish ok=True" in block:
        return "finish"
    return "?"


def channels(path: str) -> dict:
    """切分 run log 为每题段落 ⇒ {题序: 通道}。"""
    lines = open(path, encoding="utf-8").read().splitlines()
    # ⛔ 第一段必须**从第一个「▶ 任务开始」开始**：首版把文件头部日志也算成
    #    第 0 题 ⇒ 段落整体偏移一位、且多出一个尾部残余段（'?' 假通道）。
    #    （同类自踩：解析器必须先打印中间结果核对段数 == 题数。）
    out, cur, seen, started = {}, [], 0, False
    for ln in lines:
        if "▶ 任务开始:" in ln:
            if started and cur:
                out[seen] = channel_of("\n".join(cur))
                seen += 1
            cur, started = [ln], True
            continue
        if not started:
            continue
        cur.append(ln)
        if "■ " in ln and ("done" in ln or "closed" in ln):
            out[seen] = channel_of("\n".join(cur))
            seen += 1
            cur = []
    assert len(out) == 4, f"段落数 {len(out)} != 题数 4（{path}）"
    return out


rows = [json.loads(l) for l in open(f"{V1}/bench_out/runs.jsonl", encoding="utf-8")
        if l.strip()]
if len(rows) < len(RUNS):
    print(f"!! runs.jsonl 行数不足 {len(rows)}/{len(RUNS)}")
    sys.exit(2)
rows = rows[-len(RUNS):]

data = {}
for (cfg, rep), row in zip(RUNS, rows):
    per = {t["id"]: t["score"] for t in (row.get("report") or {}).get("per_task", [])}
    ch = channels(f"{D}/run_{cfg}_{rep}.log")
    data[(cfg, rep)] = (per, ch)

ids = sorted(next(iter(data.values()))[0])
print("== 逐题：通道 / 分数（3 rep）==")
for tid in ids:
    line = f"{tid:<22}"
    for cfg in ("W120", "W600"):
        chs = [data[(cfg, r)][1].get(i) for i, r in enumerate(("r1", "r2", "r3"))]
        scs = [data[(cfg, r)][0].get(tid) for r in ("r1", "r2", "r3")]
        spread = max(scs) - min(scs)
        n_ch = len(set(c for c in chs if c))
        line += (f" | {cfg}: 通道{n_ch}种{chs} 分{scs} 极差{spread:.2f}")
    print(line)

print("\n== 判据（照登记）==")
g = "g7e2_truncate_tail"


def seq(cfg, key):
    out = []
    for i, r in enumerate(("r1", "r2", "r3")):
        per, ch = data[(cfg, r)]
        out.append(ch.get(i) if key == "ch" else per.get(g))
    return out


c1 = len(set(seq("W120", "ch"))) >= 2
s120, s600 = seq("W120", "sc"), seq("W600", "sc")
c2 = len(set(seq("W600", "ch"))) == 1 and len(set(s600)) == 1
print(f"  1. 对照成立（W120 下 g7e2 ≥2 种通道）: {c1}  通道={seq('W120','ch')}")
print(f"  2. 主判据（W600 下 g7e2 通道一致 + 分数逐位一致）: {c2}")
print(f"     W120 分={s120} 极差={max(s120)-min(s120):.2f} | "
      f"W600 分={s600} 极差={max(s600)-min(s600):.2f} | W600 通道={seq('W600','ch')}")

# 全局：两配置下所有题的极差合计（噪声总量对比）
def total_spread(cfg):
    return sum(max(data[(cfg, r)][0][t] for r in ("r1", "r2", "r3"))
               - min(data[(cfg, r)][0][t] for r in ("r1", "r2", "r3"))
               for t in ids)


print(f"\n  噪声总量（4 题极差之和）: W120={total_spread('W120'):.2f}  "
      f"W600={total_spread('W600'):.2f}")
print("★7v-a: ", "PASS —— 墙钟耦合成立：放大墙钟后通道收敛、极差归零" if (c1 and c2)
      else "不成立/部分成立 —— 见上，如实登记（病源可能不在 wall）")

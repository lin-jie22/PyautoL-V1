#!/usr/bin/env python3
"""请求健康度探针（第 109 轮 §79）：判断一次跑批的**装置**能不能用来做读数。

## 为什么需要这个探针

§78 第一次跑批（`../v5_s108/attempt1_device_failure/`）的 `A` 臂 18 次尝试里
**9 次交付是垃圾**：3 份是 12 字符的 `(语义记忆中无相关条目)`，6 份是 **0 字符**。
只看 `run.log` 会以为这是"模型不会做"；看代理台账才知道是**通道掉到 ≈1 token/s**，
单次调用 266~300 秒，而 `V1_MAX_WALL_S=240` ⇒ 一次调用就把整次尝试的预算吃光。

## 两个层次的闸（⛔ 不许合并，§78.6.3）

| 层次 | 来源 | 回答什么 |
|---|---|---|
| **H0 总量闸**（`usable()`） | `proxy.jsonl` + `answers.jsonl` | 这批交付整体是不是垃圾？通道能不能用？ |
| **H0′ 逐次判据**（`--log`，本轮新增 V14） | `run.log` | **哪一次尝试**的 0 分是装置给的？ |

§78 的 B 臂 12 次尝试里有 1 次 `■ error | steps=0`（首次调用 195s 超时），
而 H0 **总量仍判"可用"**（1/18 远不过半）⇒ 那一格 0 分是装置给的，却被算成了能力。
⇒ 本轮把逐次计数**单列**（V14）。它**不改变** H0 的判决（H0 仍是总量闸）。

## 读数

| 读数 | 定义 | 判读 |
|---|---|---|
| `slow` | `elapsed_s > 120` 的请求数 | 这些请求**必然**吃掉一次尝试的墙钟 |
| `truncated` | `finish_reason == "length"` | 模型在**打转**（写满上限还没写完） |
| `tps` | `completion_tokens / elapsed_s` | 通道吞吐；中位 `< 3` ⇒ 这个时段不适合跑批 |
| `zero_answers` / `episodic_fallbacks` | 直接数交付物 | 垃圾交付的两条来源分别计数 |
| ⭐ `bad_attempts` | 块内有 `■ error` / 超时 / `来源=episodic` / 0 字符 的尝试数 | **逐次**装置零（V14） |

⛔ 本探针**只读冻结台账**，不读 `oracle`、不判分。

用法：
    python3 bench/evidence/v5_s109/probe_request_health.py --proxy DIR/proxy.jsonl \\
        --answers DIR/answers.jsonl [--log DIR/run.log]
    python3 bench/evidence/v5_s109/probe_request_health.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SLOW_S = 120.0          #: 超过它 = 必然吃光一次尝试
TPS_MIN = 3.0           #: 低于它 = 通道不适合跑批
EPISODIC_MARK = "语义记忆中无相关条目"
_BLOCK = "▶ 任务开始"
_ERR = "■ error"
_TIMEOUT = "超时"
_EPISODIC_SRC = "来源=episodic"
_ZERO_DELIVERY = re.compile(r"收尾交付\s+0\s*字符")


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def health(proxy: list[dict], answers: list[dict] | None) -> dict:
    slow, trunc, errs = [], [], []
    tps = []
    for r in proxy:
        el = float(r.get("elapsed_s") or 0.0)
        u = r.get("usage") or {}
        ct = u.get("completion_tokens")
        if el > SLOW_S:
            slow.append(round(el, 1))
        if r.get("finish_reason") == "length":
            trunc.append(round(el, 1))
        if r.get("status") != 200:
            errs.append(r.get("status"))
        if isinstance(ct, int) and el > 0:
            tps.append(ct / el)
    tps.sort()
    out = {
        "requests": len(proxy),
        "slow": len(slow), "slow_max_s": max(slow) if slow else 0.0,
        "truncated": len(trunc),
        "non_200": len(errs),
        "tps_median": round(tps[len(tps) // 2], 2) if tps else 0.0,
        "tps_min": round(tps[0], 2) if tps else 0.0,
        "tps_slow_n": sum(1 for x in tps if x < TPS_MIN),
        "zero_answers": None, "episodic_fallbacks": None, "answers": None,
    }
    if answers is not None:
        ans = [a for a in answers if a.get("kind") == "answer"]
        out["answers"] = len(ans)
        out["zero_answers"] = sum(1 for a in ans if not (a.get("answer") or "").strip())
        out["episodic_fallbacks"] = sum(
            1 for a in ans if EPISODIC_MARK in (a.get("answer") or ""))
    return out


def per_attempt(log_path: Path) -> dict:
    """⭐ V14：**逐次**装置零 —— 哪一次尝试的 0 分是装置给的。

    H0 是总量闸（不过半就判"可用"），所以 1/18 个装置零在 H0 里**看不见**。
    这里把它逐条列出来，让判读时能说清"这一格 0 分不是能力的锅"。
    """
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for line in log_path.read_text(errors="replace").splitlines():
        if _BLOCK in line:
            cur = [line]
            blocks.append(cur)
        elif cur is not None:
            cur.append(line)

    bad: list[dict] = []
    for b in blocks:
        head = b[0][:80]
        why: list[str] = []
        blob = "\n".join(b)
        if _ERR in blob:
            why.append("error")
        if _TIMEOUT in blob:
            why.append("timeout")
        if _EPISODIC_SRC in blob:
            why.append("episodic")
        if _ZERO_DELIVERY.search(blob):
            why.append("zero_delivery")
        if why:
            bad.append({"head": head, "why": why})
    return {
        "attempts": len(blocks),
        "bad_attempts": len(bad),
        "bad_detail": bad,
        "by_reason": {k: sum(1 for d in bad if k in d["why"])
                      for k in ("error", "timeout", "episodic", "zero_delivery")},
    }


def usable(h: dict) -> tuple[bool, str]:
    """装置能不能用来判读。⛔ 这是**描述性**的健康闸（总量），不参与统计判决。"""
    if h["answers"] is not None:
        bad = (h["zero_answers"] or 0) + (h["episodic_fallbacks"] or 0)
        # ⚠️ §78 第一版写成 `bad * 2 > answers` —— S4 反证当场抓到：真读数是**恰好一半**
        #    （9/18），严格大于判成"可用"，而一半交付是垃圾显然不能判读。改成 `>=`。
        if bad * 2 >= (h["answers"] or 1):
            return False, f"垃圾交付 {bad}/{h['answers']} 达到或超过一半 ⇒ 装置不可用"
    if h["slow"] * 4 > h["requests"]:
        return False, f"慢请求 {h['slow']}/{h['requests']} 超过 1/4 ⇒ 通道拖垮了墙钟"
    if h["tps_median"] < TPS_MIN:
        return False, f"中位吞吐 {h['tps_median']} tok/s < {TPS_MIN} ⇒ 这个时段不适合跑批"
    return True, "装置可用"


def selftest() -> int:
    def mk(el, ct, fin="stop", status=200):
        return {"elapsed_s": el, "usage": {"completion_tokens": ct},
                "finish_reason": fin, "status": status}

    cases = [
        ("S1 健康装置：快、无截断、答案非空",
         [mk(10, 300) for _ in range(4)],
         [{"kind": "answer", "answer": "```python\nx=1\n```"} for _ in range(4)],
         True),
        ("S2 通道慢：单请求 266s ⇒ 判不可用",
         [mk(266, 260), mk(175, 260), mk(300, 0, status=502), mk(50, 300)],
         [{"kind": "answer", "answer": "code"} for _ in range(4)],
         False),
        ("S3 垃圾交付过半（空 + episodic）⇒ 判不可用",
         [mk(10, 300) for _ in range(4)],
         [{"kind": "answer", "answer": ""},
          {"kind": "answer", "answer": ""},
          {"kind": "answer", "answer": "(语义记忆中无相关条目)"},
          {"kind": "answer", "answer": "code"}],
         False),
        ("S4 §78 第一次跑批的量级（7 慢 / 94 请求、9/18 垃圾）⇒ 必须判不可用",
         [mk(10, 300) for _ in range(94 - 7)] + [mk(266, 260) for _ in range(7)],
         [{"kind": "answer", "answer": ""} for _ in range(6)]
         + [{"kind": "answer", "answer": "(语义记忆中无相关条目)"} for _ in range(3)]
         + [{"kind": "answer", "answer": "code"} for _ in range(9)],
         False),
        ("S5 ⭐ 反证 S2~S4：同样多的答案、但都是真代码 ⇒ 必须判**可用**",
         [mk(12, 300) for _ in range(94)],
         [{"kind": "answer", "answer": "```python\ndef f(): pass\n```"} for _ in range(18)],
         True),
    ]
    bad = 0
    for why, proxy, ans, want in cases:
        h = health(proxy, ans)
        ok, reason = usable(h)
        good = (ok is want)
        print(f"  {'✅' if good else '❌'} {why}  实测={ok}（{reason}）")
        bad += 0 if good else 1

    # S6 截断与慢请求必须**分别**计数（不是同一件事）
    h = health([mk(216, 2048, fin="length"), mk(10, 300)], None)
    ok2 = h["truncated"] == 1 and h["slow"] == 1
    print(f"  {'✅' if ok2 else '❌'} S6 截断与慢请求分开计数（实测 truncated={h['truncated']} "
          f"slow={h['slow']}）")
    bad += 0 if ok2 else 1

    # ── V14 逐次判据（S7~S9）────────────────────────────────────────────
    import tempfile
    dirty = """▶ 任务开始: 下面是一段**已定稿**的框架代码，import 已就位，请你把函数 `clean_lines` 补全。
00:00:01   [1] 通道=深 候选=1
00:03:15   ■ error | steps=0（首次调用超时）
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `group_names` 补全。
00:01:01   [1] 通道=深 候选=1
00:01:40   ✅ 收尾: ok
00:01:41   📦 收尾交付 676 字符
"""
    clean = """▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `clean_lines` 补全。
00:00:01   [1] 通道=深 候选=1
00:01:40   ✅ 收尾: ok
00:01:41   📦 收尾交付 640 字符
▶ 任务开始: 下面是一段**已定稿**的框架代码，请你把函数 `group_names` 补全。
00:02:01   [1] 通道=深 候选=1
00:02:40   ✅ 收尾: ok
00:02:41   📦 收尾交付 676 字符
"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "dirty.log"
        d.write_text(dirty, encoding="utf-8")
        c = Path(td) / "clean.log"
        c.write_text(clean, encoding="utf-8")
        hd, hc = per_attempt(d), per_attempt(c)
    ok7 = hd["attempts"] == 2 and hd["bad_attempts"] == 1 and hd["by_reason"]["error"] == 1
    print(f"  {'✅' if ok7 else '❌'} S7 ⭐ V14：2 次尝试里 1 次 `■ error` ⇒ bad_attempts=1"
          f"（实测 attempts={hd['attempts']} bad={hd['bad_attempts']} "
          f"by_reason={hd['by_reason']}）")
    bad += 0 if ok7 else 1

    ok8 = hc["attempts"] == 2 and hc["bad_attempts"] == 0
    print(f"  {'✅' if ok8 else '❌'} S8 ⭐ 反证 S7：干净日志必须 bad_attempts=0"
          f"（不是恒正）（实测 bad={hc['bad_attempts']}）")
    bad += 0 if ok8 else 1

    # S9 ⭐ 反证「总量闸看不见逐次零」：1/18 垃圾在 H0 里判"可用"
    h_total = health([mk(10, 300) for _ in range(18)],
                     [{"kind": "answer", "answer": ""}] + 
                     [{"kind": "answer", "answer": "code"} for _ in range(17)])
    ok9, reason9 = usable(h_total)
    ok9 = ok9 is True and h_total["zero_answers"] == 1
    print(f"  {'✅' if ok9 else '❌'} S9 ⭐ 「1/18 装置零」在 H0 总量闸里判**可用**"
          f"（这就是 V14 存在的理由）（实测 usable={ok9} zero={h_total['zero_answers']}）")
    bad += 0 if ok9 else 1

    print(f"{'✅ 自证 9/9' if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy")
    ap.add_argument("--answers")
    ap.add_argument("--log", help="run.log —— 给了才出 V14 逐次读数")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.proxy:
        print("缺 --proxy（或 --selftest）", file=sys.stderr)
        return 2
    ans = load(Path(a.answers)) if a.answers else None
    h = health(load(Path(a.proxy)), ans)
    for k, v in h.items():
        print(f"  {k:20s} {v}")
    ok, reason = usable(h)
    print(f"  装置判定（H0 总量闸）：{'✅' if ok else '⛔'} {reason}")

    if a.log:
        pa = per_attempt(Path(a.log))
        print(f"\n  ⭐ H0′ 逐次判据（V14，run.log）")
        print(f"    attempts      {pa['attempts']}")
        print(f"    bad_attempts  {pa['bad_attempts']}（装置给的 0 分，H0 总量闸**看不见**）")
        print(f"    by_reason     {pa['by_reason']}")
        for d in pa["bad_detail"]:
            print(f"      ⚠️ {d['why']}  {d['head'][:70]}")
        if pa["attempts"]:
            print(f"    比例          {pa['bad_attempts']}/{pa['attempts']} = "
                  f"{pa['bad_attempts'] / pa['attempts']:.0%}")
        print("    ⛔ 逐次零**不改 H0 的判决**；它只用来在报告里说清"
              "「哪一格 0 分不是能力的锅」。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

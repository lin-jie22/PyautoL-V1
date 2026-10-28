#!/usr/bin/env python3
"""请求健康度探针（第 108 轮 §78）：判断一次跑批的**装置**能不能用来做读数。

## 为什么需要这个探针

第 108 轮第一次跑批（`attempt1_device_failure/`）的 `A` 臂 18 次尝试里 **9 次交付是垃圾**：

- 3 份是 12 字符的 `(语义记忆中无相关条目)`（收尾失败后从 episodic 记忆里背出来的旧文本）；
- 6 份是 **0 字符**（收尾调用失败 / 预算耗尽）。

只看 `run.log` 会以为这是"模型不会做"。看代理台账才知道是**通道在 00:13–00:30 掉到 ≈1 token/s**：
单次调用 266~300 秒，而 `V1_MAX_WALL_S=240` ⇒ 一次调用就把整次尝试的预算吃光。

## 三个读数

| 读数 | 定义 | 判读 |
|---|---|---|
| `slow` | `elapsed_s > 120` 的请求数 | 这些请求**必然**吃掉一次尝试的墙钟 |
| `truncated` | `finish_reason == "length"` 的请求数 | 模型在**打转**（2048 tokens 还没写完 25 行代码） |
| `tps` | `completion_tokens / elapsed_s` | 通道吞吐；`< 3` ⇒ 这个时段不适合跑批 |

⭐ 另有 `zero_answers` / `episodic_fallbacks`：直接数交付物，不看日志。

⛔ 本探针**只读冻结台账**，不读 `oracle`、不判分。

用法：
    python3 bench/evidence/v5_s108/probe_request_health.py --proxy DIR/proxy.jsonl \
        --answers DIR/answers.jsonl
    python3 bench/evidence/v5_s108/probe_request_health.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SLOW_S = 120.0          #: 超过它 = 必然吃光一次尝试
TPS_MIN = 3.0           #: 低于它 = 通道不适合跑批
EPISODIC_MARK = "语义记忆中无相关条目"


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


def usable(h: dict) -> tuple[bool, str]:
    """装置能不能用来判读。⛔ 这是**描述性**的健康闸，不参与统计判决。"""
    if h["answers"] is not None:
        bad = (h["zero_answers"] or 0) + (h["episodic_fallbacks"] or 0)
        # ⚠️ 第一版写成 `bad * 2 > answers` —— S4 反证当场抓到：真读数是 **恰好一半**（9/18），
        #    严格大于判成"可用"，而一半的交付是垃圾显然不能用来判读。改成 `>=`。
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
        ("S4 A 臂真读数（7 慢 / 94 请求、9/18 垃圾）的量级 ⇒ 必须判不可用",
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

    # ⭐ 截断与慢请求必须**分别**计数（不是同一件事）
    h = health([mk(216, 2048, fin="length"), mk(10, 300)], None)
    ok2 = h["truncated"] == 1 and h["slow"] == 1
    print(f"  {'✅' if ok2 else '❌'} S6 截断与慢请求分开计数（实测 truncated={h['truncated']} "
          f"slow={h['slow']}）")
    bad += 0 if ok2 else 1

    print(f"{'✅ 自证 6/6' if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy")
    ap.add_argument("--answers")
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
    print(f"  装置判定：{'✅' if ok else '⛔'} {reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

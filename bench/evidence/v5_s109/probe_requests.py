#!/usr/bin/env python3
"""请求层读数（V17）：两臂的**请求体集合**对照 + 「同 prompt ⇒ 同答案」核对。

为什么需要它（第 109 轮 §79.7）：交付文本两臂逐字节相同时，必须能回答
"是模型没改"还是"最后一脚根本没带反馈"。证据只在 `bench/llm_proxy.py` 的台账里
—— 它有 `req_sha`（请求体 sha256）、`req_bytes`、`resp_bytes`、`usage.completion_tokens`。
本轮这些数是拿一次性脚本算的（§79.8 的同类病：**手工读数不进量具 ⇒ 下一轮重算**），
所以补成探针。

三条读数：

1. `unique` —— 唯一请求体数。`repeat=3` 时它应当 ≈ `请求数 / 3`
   （⇒ 3 rep 在请求层就是同一件事做三遍，`n=18` 不是独立样本）。
2. `arms` —— 两臂交集 / 各自特异。**特异的那几个就是"闸的反馈进了上下文"的证据**。
3. `same_prompt_same_answer` —— 共有请求体的 `resp_bytes` 是否一致。
   ⛔ 只认 `completion_tokens` **与** `finish_reason` 也一致的行；单看字节数会被
   响应信封里的 id/时间戳骗（本轮实测 ±1 字节，6/24）。

用法：
    python probe_requests.py --arm-a a1.jsonl --arm-b a2.jsonl
    python probe_requests.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


# ===========================================================================
# 读
# ===========================================================================
def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _tok(d: dict) -> int | None:
    u = d.get("usage") or {}
    return u.get("completion_tokens")


def by_prompt(rows: list[dict]) -> dict[str, dict]:
    """req_sha → 该请求体的汇总（出现次数 + 各次响应读数）。"""
    out: dict[str, dict] = {}
    for d in rows:
        sha = d.get("req_sha")
        if not sha:
            continue
        e = out.setdefault(sha, {
            "n": 0,
            "req_bytes": d.get("req_bytes"),
            "resp_bytes": [],
            "tokens": [],
            "finish": [],
        })
        e["n"] += 1
        e["resp_bytes"].append(d.get("resp_bytes"))
        e["tokens"].append(_tok(d))
        e["finish"].append(d.get("finish_reason"))
    return out


# ===========================================================================
# 算
# ===========================================================================
def reps_identical(rows: list[dict], k: int = 3) -> dict:
    """同一个请求体是否恰好出现 `k` 次（`repeat=k` 的结构性证据）。"""
    c = Counter(d["req_sha"] for d in rows if d.get("req_sha"))
    hist = Counter(c.values())
    return {
        "requests": sum(c.values()),
        "unique": len(c),
        "times_hist": dict(sorted(hist.items())),
        "all_k": bool(c) and all(v == k for v in c.values()),
    }


def compare(a: dict[str, dict], b: dict[str, dict]) -> dict:
    sa, sb = set(a), set(b)
    return {
        "unique_a": len(sa),
        "unique_b": len(sb),
        "shared": len(sa & sb),
        "only_a": sorted(sa - sb),
        "only_b": sorted(sb - sa),
        "size_only_a": sorted((a[s]["req_bytes"] or 0) for s in sa - sb),
        "size_only_b": sorted((b[s]["req_bytes"] or 0) for s in sb - sa),
    }


def same_prompt_same_answer(a: dict[str, dict], b: dict[str, dict]) -> dict:
    """共有请求体里，"答案一致"的个数。

    判据（刻意严）：`resp_bytes` **首值**相同 **且** `tokens` 全同 **且** `finish` 全同。
    ⛔ 不把 ±1 字节当"答案不同"—— 响应信封含 id / 时间戳；也不把它当"相同"就完事：
    两个数都报（`exact` 与 `token_level`）。
    """
    shared = sorted(set(a) & set(b))
    exact = tok_level = 0
    diff = []
    for s in shared:
        ca, cb = a[s], b[s]
        if ca["resp_bytes"][0] == cb["resp_bytes"][0]:
            exact += 1
        elif (set(ca["tokens"]) | set(cb["tokens"])) and \
                max(ca["tokens"]) == max(cb["tokens"]) and \
                set(ca["finish"]) == set(cb["finish"]):
            tok_level += 1
        else:
            diff.append((s, ca["resp_bytes"][0], cb["resp_bytes"][0],
                         max(ca["tokens"] or [0]), max(cb["tokens"] or [0])))
        # tokens 全同的也要单独数：这是"同 prompt ⇒ 同答案"的硬证据
    tok_same = sum(1 for s in shared if set(a[s]["tokens"]) == set(b[s]["tokens"]))
    return {
        "shared": len(shared),
        "resp_bytes_exact": exact,
        "resp_bytes_off_by_one": tok_level,
        "tokens_same": tok_same,
        "violations": diff,
    }


# ===========================================================================
# 印
# ===========================================================================
def report(pa: Path, pb: Path | None) -> int:
    A = load(pa)
    ra = reps_identical(A)
    print(f"[A] {pa.name}")
    print(f"  请求数 {ra['requests']}  唯一请求体 {ra['unique']}  "
          f"出现次数分布 {ra['times_hist']}  "
          f"{'✅ 每个恰好 3 次（= 3 rep 同一件事）' if ra['all_k'] else '⚠️ 不是恒 3 次'}")

    if pb is None:
        return 0

    B = load(pb)
    rb = reps_identical(B)
    print(f"[B] {pb.name}")
    print(f"  请求数 {rb['requests']}  唯一请求体 {rb['unique']}  "
          f"出现次数分布 {rb['times_hist']}  "
          f"{'✅' if rb['all_k'] else '⚠️'}")

    ba, bb = by_prompt(A), by_prompt(B)
    cmp_ = compare(ba, bb)
    print("\n[两臂请求体集合]")
    print(f"  交集 {cmp_['shared']}  只在 A {len(cmp_['only_a'])}  只在 B {len(cmp_['only_b'])}")
    print(f"  只在 A 的字节数 {cmp_['size_only_a']}")
    print(f"  只在 B 的字节数 {cmp_['size_only_b']}")
    if cmp_["only_a"] or cmp_["only_b"]:
        print("  ⇒ 这些就是「闸的反馈进了上下文」的证据（⛔ 不是「没送进去」）")
    else:
        print("  ⛔ 两臂请求体完全相同 ⇒ 反馈**没有**进上下文（此时 Δ 必然 0，且不能归因于模型）")

    sp = same_prompt_same_answer(ba, bb)
    print("\n[同 prompt ⇒ 同答案]")
    print(f"  共有 {sp['shared']} 个；resp_bytes 完全相同 {sp['resp_bytes_exact']}；"
          f"差 ±1 字节（信封） {sp['resp_bytes_off_by_one']}；"
          f"completion_tokens 全同 {sp['tokens_same']}")
    if sp["violations"]:
        print(f"  ❌ {len(sp['violations'])} 个请求体答案不一致：")
        for v in sp["violations"]:
            print(f"     {v[0][:8]}  resp A={v[1]} B={v[2]}  tok A={v[3]} B={v[4]}")
    else:
        print("  ✅ 没有不一致：同一请求体在两臂得到同一答案")
    return 0 if not sp["violations"] else 1


# ===========================================================================
# 自证：每条判据都必须能红
# ===========================================================================
def _row(sha: str, req: int, resp: int, tok: int, fin: str = "stop") -> dict:
    return {"req_sha": sha, "req_bytes": req, "resp_bytes": resp,
            "usage": {"completion_tokens": tok}, "finish_reason": fin}


def selftest() -> int:
    bad = []
    print("[自证] probe_requests.py")

    # S1 3 rep 同请求体 ⇒ unique = 总/3，且 all_k
    one = [_row("s1", 100, 200, 10)] * 3 + [_row("s2", 90, 120, 4)] * 3
    r = reps_identical(one)
    ok = r["requests"] == 6 and r["unique"] == 2 and r["all_k"] and r["times_hist"] == {3: 2}
    print(f"  {'✅' if ok else '❌'} S1 3 rep 完全相同 ⇒ unique=2/6 次 实测={r}")
    if not ok:
        bad.append("S1")

    # S2 反证：混进一个只出现 1 次的 ⇒ all_k 必须变 False
    r2 = reps_identical(one + [_row("s3", 80, 90, 3)])
    ok = r2["all_k"] is False and r2["times_hist"] == {1: 1, 3: 2}
    print(f"  {'✅' if ok else '❌'} S2 反证：多一个只出现 1 次 ⇒ all_k=False 实测={r2['times_hist']}")
    if not ok:
        bad.append("S2")

    # S3 两臂交集/特异
    A = by_prompt([_row("x", 100, 10, 1), _row("y", 200, 20, 2)])
    B = by_prompt([_row("x", 100, 10, 1), _row("z", 300, 30, 3)])
    c = compare(A, B)
    ok = (c["shared"] == 1 and c["only_a"] == ["y"] and c["only_b"] == ["z"]
          and c["size_only_a"] == [200] and c["size_only_b"] == [300])
    print(f"  {'✅' if ok else '❌'} S3 交集 1 / 只 A ['y'] / 只 B ['z'] 实测={c}")
    if not ok:
        bad.append("S3")

    # S4 反证：两臂完全相同 ⇒ 特异集合为空（这条保证 S3 不是恒真）
    c2 = compare(A, A)
    ok = c2["only_a"] == [] and c2["only_b"] == []
    print(f"  {'✅' if ok else '❌'} S4 反证：两臂同 ⇒ 特异为空 实测={c2['only_a']},{c2['only_b']}")
    if not ok:
        bad.append("S4")

    # S5 同 prompt 同答案：resp 完全相同 ⇒ exact
    A2 = by_prompt([_row("p", 100, 500, 137), _row("q", 100, 400, 20)])
    B2 = by_prompt([_row("p", 100, 500, 137), _row("q", 100, 400, 20)])
    s = same_prompt_same_answer(A2, B2)
    ok = s["shared"] == 2 and s["resp_bytes_exact"] == 2 and not s["violations"]
    print(f"  {'✅' if ok else '❌'} S5 同 prompt 同答案 ⇒ exact=2 实测={s}")
    if not ok:
        bad.append("S5")

    # S6 ±1 字节（信封）必须归到 off_by_one，不算 violation
    A3 = by_prompt([_row("p", 100, 915, 137)])
    B3 = by_prompt([_row("p", 100, 916, 137)])
    s = same_prompt_same_answer(A3, B3)
    ok = (s["resp_bytes_exact"] == 0 and s["resp_bytes_off_by_one"] == 1
          and not s["violations"] and s["tokens_same"] == 1)
    print(f"  {'✅' if ok else '❌'} S6 ±1 字节且 tokens 同 ⇒ off_by_one=1、不算违例 实测={s}")
    if not ok:
        bad.append("S6")

    # S7 反证：tokens 不同 ⇒ 必须报 violation（否则 S5 恒真）
    B4 = by_prompt([_row("p", 100, 990, 199)])
    s = same_prompt_same_answer(A3, B4)
    ok = len(s["violations"]) == 1 and s["tokens_same"] == 0
    print(f"  {'✅' if ok else '❌'} S7 反证：同 prompt 答案不同 ⇒ violation=1 实测={s['violations']}")
    if not ok:
        bad.append("S7")

    print(f"{'✅ 自证 7/7' if not bad else f'❌ {bad} 条失败'}")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-a")
    ap.add_argument("--arm-b")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.arm_a:
        print("缺 --arm-a（或 --selftest）", file=sys.stderr)
        return 2
    return report(Path(a.arm_a), Path(a.arm_b) if a.arm_b else None)


if __name__ == "__main__":
    sys.exit(main())

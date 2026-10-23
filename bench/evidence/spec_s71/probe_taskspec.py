#!/usr/bin/env python3
"""「题面与 oracle 同源」的准入器自检 —— 每条检查都自带**反证**。

## 这个探针量什么、不量什么

量的是**准入器有牙**：

    P1 结构禁令（转小写 + 去重 ⇒ 比较是否忽略大小写永远不可判定）
    P2 A3 反证：题面写 n、实现给 n+1 ⇒ 必须红（**7y 的实形**）
    P3 A4 反证：生成源码与独立解释器不一致 ⇒ 必须红
    P4 A5 反证（题面漏写参数）⇒ 必须红
    P5 A5 反证（代码不读参数，后置条件仍成立）⇒ 必须红
    P6 六道题全过准入，且**常识实现严格居中**
    P7 截断的常识写法「长度合格、内容不合格」⇒ 后置条件挡不住它
    P8 按种子铸题：确定性 + 新题过同一条准入
    P9 落盘产物与现算逐字一致（防手改 jsonl）

⛔ **不**量「这套题能分辨模型强弱」。那要真模型读数，本轮没有（见 DESIGN §34）。

## 为什么每条都要反证

本项目的纪律 ⑩：没有反证的检测器是死代码。一个"从来只输出 ✅"的准入器
和没有准入器一模一样，而它看起来更让人放心 —— 那更糟。

退出码：0 = 全部成立；1 = 有不成立项。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1 / "bench"))

import gen_spec_tasks as G                                        # noqa: E402
import taskspec as T                                              # noqa: E402

_CHECKS: list[tuple[str, bool]] = []


def ck(name: str, cond: bool) -> bool:
    _CHECKS.append((name, bool(cond)))
    return bool(cond)


def _fails(spec: T.Spec) -> list[str]:
    return T.admit(spec, G.CASES)


def main() -> int:
    specs = {s["id"]: s for s in G.SPECS}

    # ── P1–P5 四条证据各自的坏样本（实现就在 taskspec.reverse_proofs，只此一份）──
    rp = T.reverse_proofs(specs["sg05"], G.CASES)
    ck("P1 结构禁令：转小写 + 去重 ⇒ A0 拒绝",
       any("A0" in f and "lower" in f for f in rp["A0"]))
    ck("P2 A3 反证：题面写 n、源码给 n+1 ⇒ 「truncate 长度上限」必须红",
       any("A3" in f and "truncate" in f for f in rp["A3"]))
    ck("P3 A4 反证：源码与解释器不一致 ⇒ 必须红，且 A3 不替它背锅",
       any("A4" in f for f in rp["A4"]) and not any("A3" in f for f in rp["A4"]))
    ck("P4 A5 反证（题面漏写参数）⇒ 「题面不变」必须红",
       any("A5" in f and "题面不变" in f for f in rp["A5题面"]))
    ck("P5 A5 反证（代码不读参数，后置条件仍成立）⇒ 「行为不变」必须红",
       any("A5" in f and "行为不变" in f for f in rp["A5代码"]))

    # ── P6 六道题全过 + 常识实现严格居中 ────────────────────────────────
    all_pass = True
    readings: list[str] = []
    for spec in G.SPECS:
        f = _fails(spec)
        hit, n = T.naive_hits(spec, G.CASES)
        # 测试必须真的能跑通 oracle（否则"这题合格"只是一张纸）
        ns: dict = {}
        exec(compile(T.compile_oracle(spec), "<o>", "exec"), ns)   # noqa: S102
        ok = 0
        for a in T.tests_of(spec, G.CASES):
            try:
                exec(compile(a, "<a>", "exec"), dict(ns))          # noqa: S102
                ok += 1
            except Exception:                                      # noqa: BLE001
                pass
        good = (not f) and 0 < hit < n and ok == n
        all_pass = all_pass and good
        readings.append(f"{spec['id']}={hit}/{n}测试{ok}/{n}")
        if f:
            print(f"    {spec['id']} 准入失败：{f[:2]}")
    ck("P6 六道题全过准入，且常识实现严格居中（不是 0 也不是满分）", all_pass)
    print(f"    常识分（严格居中即合格）：{' ｜ '.join(readings)}")

    # ── P7 「长度合格、内容不合格」────────────────────────────────────────
    # 这是 A3 挡不住的那一类：省略号版本长度也 ≤ n，后置条件全过，
    # 但内容是错的。⇒ 只有"与 oracle 逐条比对"才发现得了它。
    leak = []
    for spec in G.SPECS:
        tr = [r for r in spec["rules"] if r["op"] == "truncate"]
        if not tr:
            continue
        n = tr[0]["n"]
        nsrc = T.compile_oracle(spec, naive=True)
        for case in G.CASES:
            out = T.run_source(nsrc, spec["fn"], case)
            if all(len(r) <= n for r in out) and \
                    out != T.run_source(T.compile_oracle(spec), spec["fn"], case):
                leak.append(spec["id"])
                break
    ck("P7 常识截断「加省略号」长度合格但内容错 ⇒ 后置条件挡不住，必须靠比对",
       bool(leak))
    print(f"    长度合格却答错的题：{sorted(set(leak))}")

    # ── P8 按种子铸题的确定性与准入 ──────────────────────────────────────
    a = [T.mint(seed, f"s{seed:02d}") for seed in range(1, 9)]
    b = [T.mint(seed, f"s{seed:02d}") for seed in range(1, 9)]
    ck("P8a 铸题确定：同 seed 两次铸出逐字相同",
       json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
    ck("P8b 铸出的 8 道新题全部过同一条准入",
       all(not _fails(s) for s in a))

    # ── P9 落盘产物与现算逐字一致 ────────────────────────────────────────
    path = V1 / "bench" / "specgen" / "tasks_v1.jsonl"
    want = "\n".join(G._line(s) for s in G.SPECS) + "\n"
    have = path.read_text(encoding="utf-8") if path.exists() else ""
    ck("P9 落盘产物 == 现算结果（手改 jsonl 会当场红）", have == want)

    # ── 汇总 ────────────────────────────────────────────────────────────
    print()
    for name, ok in _CHECKS:
        print(f"  {'✅' if ok else '❌'} {name}")
    bad = [n for n, ok in _CHECKS if not ok]
    print()
    print(f"  {'✅ 准入器有牙：' + str(len(_CHECKS)) + ' 项全部成立' if not bad else '❌ 不成立：' + str(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())

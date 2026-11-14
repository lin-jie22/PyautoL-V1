#!/usr/bin/env python3
"""V114：从 diff 读出的动作，自动合成成代码后能不能真修好。

零模型、零网络。16 个非满分实现 × 1 次判分 ≈ 8 秒。

装置全自动：动作来自 V113 的 `failures.json`（A_sub 格），位置 = 「套在每条 return 上」。
"""
from __future__ import annotations

import collections
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
ROOT = EV.parent.parent
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
sys.path.insert(0, str(EV / "v6_s199"))
sys.path.insert(0, str(EV / "v6_s198"))
import delete_idiom as D          # noqa: E402  inject_returns / compiles
import idiom_transfer as I        # noqa: E402  harvest / task_of


def score_of(tid: str, code: str) -> float | None:
    t = I.task_of(tid)
    pt = {"id": t["id"], "prompt": t.get("prompt", ""), "tests": t["tests"]}
    payload = json.dumps({"task": pt, "answer": code}, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                       capture_output=True, text=True, timeout=120)
    if not p.stdout.strip():
        return None
    return json.loads(p.stdout.strip().splitlines()[-1]).get("score")


def main() -> None:
    fails = json.loads((EV / "v6_s202" / "failures.json").read_text(encoding="utf-8"))["rows"]
    corpus = I.harvest()

    acts = collections.defaultdict(set)
    for r in fails:
        if r["kind"] == "A_sub":
            acts[(r["tid"], r["h"])].add(tuple(r["err"][0][1:]))

    print("## §0 从 diff 读出的动作（全自动，A_sub 格）")
    print(f"  有 A_sub 动作的（题, 实现）= {len(acts)} 个")
    for (tid, h), s in sorted(acts.items()):
        print(f"    {tid.split('_')[0]:6s} {h}　动作 {sorted(s)}")
    print(f"  动作的**去重集合**：{sorted({a for s in acts.values() for a in s})}")

    print("\n## §1 逐个合成 + 判分（分母 = 非满分实现 16）")
    rows = []
    for tid in sorted(corpus):
        for h in sorted(corpus[tid]):
            code = corpus[tid][h]
            s0 = score_of(tid, code)
            if s0 is None or s0 == 1.0:
                continue
            my = acts.get((tid, h), set())
            if not my:
                rows.append(dict(tid=tid, h=h, s0=s0, s1=None, n=0, note="无 A_sub 动作"))
                print(f"  {tid.split('_')[0]:6s} {h} {s0:<4} → ⛔ 无 A_sub 动作（diff 给不出）")
                continue
            mut = code
            n = 0
            for a, b in sorted(my):
                mut, k = D.inject_returns(mut, f".replace({a!r}, {b!r})")
                n += k
            s1 = score_of(tid, mut) if D.compiles(mut) else None
            rows.append(dict(tid=tid, h=h, s0=s0, s1=s1, n=n, note=""))
            print(f"  {tid.split('_')[0]:6s} {h} {s0:<4} → {s1}　"
                  f"（{len(my)} 个动作 × {n} 处 return）")

    n_total = len(rows)
    fixed = [r for r in rows if r["s1"] == 1.0]
    with_act = [r for r in rows if r["n"]]
    print(f"\n## §2 判决")
    print(f"\n  **W1 至少修好一个**（分母 {n_total}）")
    print(f"    修到 1.0 的 {len(fixed)} 个：{[r['h'] for r in fixed]}")
    print(f"    ⇒ {'✅ 押中' if fixed else '⛔ 破'}")
    print(f"\n  **W2 转化率 ≤ 20%**（分母 {n_total}）")
    print(f"    {len(fixed)}/{n_total} = {len(fixed) / n_total:.1%}")
    print(f"    ⇒ {'✅ 押中' if len(fixed) / n_total <= 0.2 else '⛔ 破'}")
    print(f"\n  ⚠️ 分题报（PREREG §4.2）：")
    for tid in sorted({r["tid"] for r in rows}):
        sub = [r for r in rows if r["tid"] == tid]
        f = [r for r in sub if r["s1"] == 1.0]
        a = [r for r in sub if r["n"]]
        print(f"    {tid.split('_')[0]:6s} 非满分 {len(sub)}　有动作 {len(a)}　修好 {len(f)}")
    print(f"\n  ⚠️ 有动作的 {len(with_act)} 个里修好 {len(fixed)} 个"
          f"（这是「通道通不通」，不是「搜索能力」）")

    (HERE / "auto_fix.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

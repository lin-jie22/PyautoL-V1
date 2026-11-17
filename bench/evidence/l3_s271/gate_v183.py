"""V183 准入闸读数：两类判据在 `mw.metacrit.admit()` 下的拒绝率。"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import relations as RL  # noqa: E402
import substrate as S  # noqa: E402
from mw import metacrit as MC  # noqa: E402

A = S.v79_asserts()
ref = 0
for tid, asserts in A.items():
    ok, why = MC.admit("\n".join(asserts))
    ref += not ok
    print(f"V79  {tid:24s} admit={ok!s:5s} {why[:70]}")
print(f"V79 断言被拒 {ref}/{len(A)}")

ref = 0
for r in RL.RELATIONS:
    ok, why = MC.admit(r["src"])
    ref += not ok
    if not ok:
        print(f"REL  被拒 {r['task']}/{r['name']}: {why[:80]}")
print(f"关系库被拒 {ref}/{len(RL.RELATIONS)}（放行 {len(RL.RELATIONS) - ref}）")

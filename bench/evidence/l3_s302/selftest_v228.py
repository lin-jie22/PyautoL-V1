#!/usr/bin/env python3
"""V228 落盘自测：`freeze_best` 的三个分支都要被**真的执行到**，且**正例路径必须通**。

为什么不用离线断言糊弄（V225 的假绿教训）：
    上一轮首版自测用 `'pass'` vs `'pass'` 判「冻结生效」，两臂**不可区分** ⇒ 恒真。
    这里每条臂的 `why`/`answer` 都写成**不同字符串**，判据是「账本里到底存了哪一段」。

⛔ 纪律 62：fail-closed 的闸必须有**正例路径** —— 只验「低分不覆盖」，
    那么「`add()` 永远不写」也能让断言全绿（死锁冒充冻结）。所以 C2 必须验证高分**能**覆盖。

跑法：`python3 bench/evidence/l3_s302/selftest_v228.py`
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.critique import Critique, CritiqueLedger  # noqa: E402

SHA_S, SHA_U, TID = "scorerAAA", "suiteBBB", "t01_parse_duration"


def mk(score: float, why: str, answer: str) -> Critique:
    return Critique(scorer_sha=SHA_S, suite_sha=SHA_U, task_id=TID,
                    family="fam", score=score, why=why, answer=answer)


def fresh(**kw) -> CritiqueLedger:
    led = CritiqueLedger(None, **kw)
    led.add(mk(0.6667, "OLD-WHY-6667", "OLD-CODE-6667"))
    return led


fails: list[str] = []


def check(name: str, cond: bool, detail: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + "   " + detail)
    if not cond:
        fails.append(name)


# ── C1 低分不覆盖 ──────────────────────────────────────────────────────────
led = fresh(freeze_best=True)
led.add(mk(0.5, "NEW-WHY-5000", "NEW-CODE-5000"))
got = led.by_key[(SHA_S, SHA_U, TID)]
check("C1 低分被拦（freeze_best=on）",
      got.score == 0.6667 and got.why == "OLD-WHY-6667" and got.answer == "OLD-CODE-6667",
      f"score={got.score} why={got.why!r} best_kept={led.best_kept}")
check("C1b 计数分开（frozen 不动、best_kept=1）",
      led.best_kept == 1 and led.frozen == 0 and led.superseded == 1,
      f"best_kept={led.best_kept} frozen={led.frozen} superseded={led.superseded}")

# ── C2 正例：高分**必须**能覆盖（否则就是死锁冒充冻结）─────────────────────
led = fresh(freeze_best=True)
led.add(mk(0.8333, "NEW-WHY-8333", "NEW-CODE-8333"))
got = led.by_key[(SHA_S, SHA_U, TID)]
check("C2 高分照常覆盖（正例路径）",
      got.score == 0.8333 and got.why == "NEW-WHY-8333" and got.answer == "NEW-CODE-8333",
      f"score={got.score} why={got.why!r} best_kept={led.best_kept}")
check("C2b 正例路径没被计数成拦截",
      led.best_kept == 0 and led.added == 2, f"best_kept={led.best_kept} added={led.added}")

# ── C3 同分不覆盖（`<=` 而不是 `<`）───────────────────────────────────────
led = fresh(freeze_best=True)
led.add(mk(0.6667, "NEW-WHY-6667", "NEW-CODE-6667"))
got = led.by_key[(SHA_S, SHA_U, TID)]
check("C3 同分不覆盖",
      got.why == "OLD-WHY-6667" and led.best_kept == 1,
      f"why={got.why!r} best_kept={led.best_kept}")

# ── C4 默认 off：旧行为一字不改 ────────────────────────────────────────────
led = fresh()
led.add(mk(0.5, "NEW-WHY-5000", "NEW-CODE-5000"))
got = led.by_key[(SHA_S, SHA_U, TID)]
check("C4 off 照旧按键覆盖",
      got.score == 0.5 and got.why == "NEW-WHY-5000" and led.best_kept == 0,
      f"score={got.score} why={got.why!r} best_kept={led.best_kept}")

# ── C5 与 freeze_pass 叠加时**互不吞并**（各记各的数）─────────────────────
led = fresh(freeze_pass=True, freeze_best=True)
led.add(mk(1.0, "NEW-WHY-1000", "NEW-CODE-1000"))     # 满分 ⇒ freeze_pass 先拦
led.add(mk(0.5, "NEW-WHY-5000", "NEW-CODE-5000"))     # 低分 ⇒ freeze_best 拦
got = led.by_key[(SHA_S, SHA_U, TID)]
check("C5 两闸各记各的（frozen=1 / best_kept=1）",
      led.frozen == 1 and led.best_kept == 1 and got.why == "OLD-WHY-6667",
      f"frozen={led.frozen} best_kept={led.best_kept} why={got.why!r}")

# ── C6 真实数据回放：V227 的两行（0.5 / 0.3333）落回 0.6667 的起点 ─────────
D = ROOT / "bench/evidence/l3_s302"
before = D / "crit_v227.jsonl"
after = D / "crit_v227_after.jsonl"
if before.exists() and after.exists():
    tmp = Path(tempfile.mkdtemp()) / "replay.jsonl"
    shutil.copy(before, tmp)
    led = CritiqueLedger(tmp, freeze_best=True)
    n = led.load()
    before_bytes = tmp.read_bytes()
    for ln in after.read_text(encoding="utf-8").splitlines():
        d = json.loads(ln)
        led.add(Critique(scorer_sha=d["scorer_sha"], suite_sha=d["suite_sha"],
                         task_id=d["task_id"], family=d.get("family", ""),
                         score=float(d["score"]), why=d.get("why", ""),
                         answer=d.get("answer", "")))
    saved = led.save()
    replay_bytes = tmp.read_bytes()
    check("C6 真行回放：账本字节不变（单调不降）",
          replay_bytes == before_bytes,
          f"载入={n} 保存={saved} best_kept={led.best_kept} "
          f"before_sha={__import__('hashlib').sha256(before_bytes).hexdigest()[:12]} "
          f"after_sha={__import__('hashlib').sha256(replay_bytes).hexdigest()[:12]}")
    check("C6b 两行都被拦（best_kept=2）", led.best_kept == 2,
          f"best_kept={led.best_kept}")
else:
    print("  SKIP  C6（缺 crit_v227*.jsonl）")

# ── C7 保存顺序无关：两次 save 的字节相同（对照，防「回放恰好同序」）────────
led = fresh(freeze_best=True)
led.add(mk(0.5, "NEW-WHY-5000", "NEW-CODE-5000"))
tmp = Path(tempfile.mkdtemp()) / "x.jsonl"
led.path = tmp
a = led.save(); b1 = tmp.read_bytes()
a2 = led.save(); b2 = tmp.read_bytes()
check("C7 同一状态两次落盘逐字节同", b1 == b2 and a == a2, f"rows={a}")

print()
print(f"FAIL={len(fails)}" + ("" if not fails else f"  → {fails}"))
sys.exit(1 if fails else 0)

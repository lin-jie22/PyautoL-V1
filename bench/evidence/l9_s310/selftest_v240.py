"""V240（§312）装置自检：换路预算参数化。

⛔ 零模型调用：`build_llm` 被打桩，sink 只收 prompt 与 max_tokens。
每条都对应 PREREG_v240 §3 的判据，跑完打 `T1..Tn`。
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1))

import run as R                                                   # noqa: E402
from mw.bench import Suite as _Suite                              # noqa: E402
from mw.config import LLMConfig                                   # noqa: E402
from mw.reflect import sha_of                                     # noqa: E402

OK: list[str] = []
BAD: list[str] = []


def t(name: str, cond: bool, detail: str = "") -> None:
    (OK if cond else BAD).append(name)
    print(f"  {'✅' if cond else '❌'} {name}{('  ' + detail) if detail else ''}")


BARE = (V1 / "bench" / "evidence" / "l6_s307" / "start_answer.py").read_text(encoding="utf-8")
T = _Suite.load(V1 / "bench" / "evidence" / "l5_s266" / "suite_one" / "t01.jsonl").tasks[0]

SINK: list[tuple[str, int]] = []


class _LLM:
    def __init__(self, out: str) -> None:
        self.out = out

    def complete(self, msgs, max_tokens=900):                      # noqa: ARG002
        SINK.append((msgs[-1].content, max_tokens))
        return "```python\n" + self.out + "\n```"


class _Sc:
    def score(self, td, cand):                                     # noqa: ARG002
        class _R:
            score = 0.8333
            vec = [True] * 6
        return _R()


class _Core:
    class s:                                                       # noqa: N801
        llm = LLMConfig(model="m", offline=True, api_key="k")


def _run_swap(**kw) -> list[tuple[str, int]]:
    import mw.llm as L
    SINK.clear()
    old = L.build_llm
    L.build_llm = lambda _c: _LLM(BARE)                            # noqa: ARG005
    try:
        R._swap_candidates(_Core(), _Sc(), T, BARE, 0.8333, 1, **kw)
    finally:
        L.build_llm = old
    return list(SINK)


print("== V240 自检 ==")

# T1 默认值仍是 900
d = inspect.signature(R._swap_candidates).parameters["max_tokens"].default
t("T1 默认 max_tokens == 900（V235~V239 同一口径）", d == 900 and R.SWAP_MAX_TOKENS_DEFAULT == 900, f"default={d}")

# T2 显式 1600 真的传到模型调用
got = _run_swap(max_tokens=1600)
t("T2 显式 max_tokens=1600 送达 complete()", bool(got) and got[0][1] == 1600,
  f"收到={got[0][1] if got else 'EMPTY'}")

# T3 不传 ⇒ 900
got0 = _run_swap()
t("T3 不传 ⇒ complete() 收到 900", bool(got0) and got0[0][1] == 900,
  f"收到={got0[0][1] if got0 else 'EMPTY'}")

# T4 非法值构造即抛（⛔ 不静默回落）
raises = []
for bad in (0, -1, "1600", 1.5):
    try:
        R._swap_candidates(_Core(), _Sc(), T, BARE, 0.8333, 1, max_tokens=bad)
        raises.append(False)
    except ValueError:
        raises.append(True)
t("T4 0/-1/'1600'/1.5 ⇒ ValueError（不回落 900）", all(raises), str(raises))

# T5 预算是**唯一**变量：改预算不改 prompt
sha900 = sha_of(got0[0][0]) if got0 else "EMPTY"
sha1600 = sha_of(got[0][0]) if got else "EMPTY"
t("T5 改预算**不动** prompt（只有预算一个自变量）", sha900 == sha1600 and sha900 != "EMPTY",
  f"{sha900[:16]} == {sha1600[:16]}")

# T6 跨轮锚：默认档 prompt 仍是 V236/V238/V239 那一份
t("T6 默认档 prompt sha 仍是 3b33bcb8b2276580", sha900 == "3b33bcb8b2276580", sha900)

# T7 CLI 存在且是 int；非法值在 argparse 层就失败
PY = sys.executable
q = subprocess.run([PY, str(V1 / "run.py"), "--goals", "--goals-swap", "1",
                    "--goals-swap-max-tokens", "1600", "--help"],
                   capture_output=True, text=True, cwd=V1)
q2 = subprocess.run([PY, str(V1 / "run.py"), "--goals", "--goals-swap-max-tokens", "abc"],
                    capture_output=True, text=True, cwd=V1)
t("T7 --goals-swap-max-tokens 已注册；非法字符串 ⇒ 非 0 退出",
  "--goals-swap-max-tokens" in q.stdout and q2.returncode != 0 and "invalid int" in (q2.stderr or ""),
  f"rc={q2.returncode}")

# T8 CLI 层 0 也不静默回落（--goals-swap 0 时也要抛）
q3 = subprocess.run([PY, str(V1 / "run.py"), "--goals", "--goals-swap", "0",
                     "--goals-swap-max-tokens", "0"],
                    capture_output=True, text=True, cwd=V1)
t("T8 --goals-swap 0 配 --goals-swap-max-tokens 0 ⇒ 仍然拒绝",
  q3.returncode != 0 and "必须是正整数" in (q3.stderr or ""),
  f"rc={q3.returncode}")

print(f"\n== {len(OK)}/{len(OK) + len(BAD)} ==")
if BAD:
    print("失败: " + ", ".join(BAD))
raise SystemExit(0 if not BAD else 1)

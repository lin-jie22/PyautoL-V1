#!/usr/bin/env python3
"""V241 装置自检 —— 跑之前必须全绿。

重点不是「参数能传」，而是**预算真的出现在请求体里**，以及**三臂的提示逐字节相同**
（否则「预算」与「提示」两个自变量缠在一起，读数不能归因）。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from types import SimpleNamespace

V1 = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1))

D = pathlib.Path(__file__).resolve().parent
FAILS: list[str] = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def main() -> int:
    import run as R
    from mw import llm as mwllm
    from mw.config import LLMConfig

    # ---- T1 默认值仍是 900（⛔ 参数化不许顺手改默认） ----
    ck("T1 默认预算仍是 900", R.SWAP_MAX_TOKENS_DEFAULT == 900,
       f"= {R.SWAP_MAX_TOKENS_DEFAULT}")

    # ---- T2 非法值构造即抛（含 bool，bool 是 int 的子类） ----
    bad = []
    for v in (0, -1, True, "900", 900.0):
        try:
            R._swap_candidates(None, None, None, "", 0.0, 1, max_tokens=v)
            bad.append(repr(v))
        except ValueError:
            pass
    ck("T2 0/-1/True/'900'/900.0 全部 ValueError", not bad, f"漏={bad}")

    # ---- T3 预算真的进了 `complete(max_tokens=...)` ----
    task = json.loads((V1 / "bench/evidence/l5_s266/suite_one/t01.jsonl")
                      .read_text(encoding="utf-8").splitlines()[0])
    code = (D / "cand" / "90579ecebe633352.py").read_text(encoding="utf-8")
    seen: list[tuple[int, str]] = []

    class _Sink:
        def __init__(self, budget: int) -> None:
            self.budget = budget

        def complete(self, msgs, *, max_tokens=0, **_kw):
            seen.append((max_tokens, msgs[0].content))
            raise RuntimeError("captured")

    orig = mwllm.build_llm
    try:
        for budget in (900, 1600, 2400):
            mwllm.build_llm = lambda *a, b=budget, **k: _Sink(b)   # noqa: ARG005
            core = SimpleNamespace(s=SimpleNamespace(llm=LLMConfig()))
            try:
                R._swap_candidates(core, None,
                                   SimpleNamespace(id=task["id"], prompt=task["prompt"],
                                                   tests=task["tests"]),
                                   code, 0.8333, 1, max_tokens=budget,
                                   models=("qwen2.5:7b",))
            except Exception:                                    # noqa: BLE001
                pass
    finally:
        mwllm.build_llm = orig
    got = [b for b, _ in seen]
    ck("T3 三档预算逐档到达 complete()", got == [900, 1600, 2400], f"got={got}")

    # ---- T4 三臂的提示逐字节相同 ----
    shas = {sha(p) for _, p in seen}
    ck("T4 三臂提示逐字节相同（预算不是提示的自变量）", len(shas) == 1,
       f"sha={sorted(shas)}")

    # ---- T5 提示与跨轮锚一致（默认档不许被这轮改动） ----
    ck("T5 默认档提示仍等于 3b33bcb8b2276580", shas == {"3b33bcb8b2276580"},
       f"sha={sorted(shas)}")

    # ---- T6 原生分支把预算写进 options.num_predict（不是 max_tokens） ----
    from mw.llm import Message
    cfg = LLMConfig(base_url="http://localhost:11434/v1", model="Qwen3.5-4B",
                    provider="ollama", thinking="disabled", api_key="ollama")
    eng = mwllm.build_llm(cfg)
    url, body, _h = eng._ollama_request([Message(role="user", content="x")],
                                        max_tokens=1600, temperature=1.0, t=30)
    bj = json.loads(body)
    ck("T6 原生体 num_predict=1600 且无 max_tokens 键",
       bj.get("options", {}).get("num_predict") == 1600 and "max_tokens" not in bj,
       f"url={url}")
    ck("T6b think 是布尔 False（不是 {'type':...}）", bj.get("think") is False,
       f"think={bj.get('think')!r}")

    # ---- T7 截断会抛错（否则「截断」与「空答案」同形） ----
    try:
        eng._ollama_content({"message": {"content": "abc"}, "done_reason": "length",
                             "eval_count": 1600})
        ok7 = False
    except mwllm.LLMError:
        ok7 = True
    ck("T7 done_reason=length ⇒ LLMError（不静默返回半截）", ok7)

    # ---- T8 CLI 非法值：非 0 退出，⛔ 不回落 900 ----
    import subprocess
    p = subprocess.run([sys.executable, str(V1 / "run.py"), "--goals",
                        "--goals-swap-max-tokens", "0"],
                       capture_output=True, text=True, cwd=str(V1))
    ck("T8 CLI --goals-swap-max-tokens 0 ⇒ 非 0 退出", p.returncode != 0,
       f"rc={p.returncode}")
    p2 = subprocess.run([sys.executable, str(V1 / "run.py"), "--goals", "--help"],
                        capture_output=True, text=True, cwd=str(V1))
    ck("T9 --help 列出该参数", "--goals-swap-max-tokens" in (p2.stdout or ""))

    print(f"\n{'全绿' if not FAILS else '红: ' + str(FAILS)}  {len(FAILS)} 处失败")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())

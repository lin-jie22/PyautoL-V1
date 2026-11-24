#!/usr/bin/env python3
"""V241 速度闸：用**真实换路提示**量 tok/s，且只认服务端 eval_count/eval_duration。

为什么要先量：纪律 116 —— 跑批量前写死门槛（<15 tok/s 不启动、单次 >300 s 停）。
V238 的教训：拿 `字符数//4` 估 token 会低估近 2 倍（13.21 vs 真实 25.05）。

口径：
- 提示 = 产品代码真正发出去的那一份（用 `_swap_candidates` + sink 捞，不手搓）。
- 每个 (模型, 预算) 只打一次；4B 关思考（`@ollama:disabled`）。
- tok/s = eval_count / (eval_duration / 1e9)。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.request

V1 = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1))

from mw import llm as mwllm                      # noqa: E402
from mw.config import parse_model_spec          # noqa: E402

D = pathlib.Path(__file__).resolve().parent
OLLAMA = "http://localhost:11434"


def real_prompt() -> str:
    """捞产品真正发出去的换路提示（不手搓、不复述）。

    ⚠️ `_swap_candidates` 在**函数体内** import `mw.llm.build_llm`（不是提前绑定的名），
    所以必须 patch `mw.llm.build_llm` 本身；patch `run._bllm` 会 AttributeError
    （V238 踩过）。
    """
    import run as R
    got: list[str] = []

    class _Sink:
        def complete(self, msgs, *, max_tokens=0, **_kw):
            got.append(msgs[0].content if isinstance(msgs, list) else str(msgs))
            raise RuntimeError("stop-after-capture")

    orig = mwllm.build_llm
    mwllm.build_llm = lambda *a, **k: _Sink()          # noqa: ARG005
    try:
        from types import SimpleNamespace
        from mw.config import LLMConfig
        cand = D / "cand" / "90579ecebe633352.py"
        code = cand.read_text(encoding="utf-8")
        # `models` 非空 ⇒ 走 `_dc_replace(core.s.llm, ...)` ⇒ core.s.llm 必须是真的
        # `LLMConfig`（传 None 会在捞到提示之前 AttributeError，V241 踩过）。
        _core = SimpleNamespace(s=SimpleNamespace(llm=LLMConfig()))
        R._swap_candidates(_core, _scorer(), _task(), code, 0.8333, 1,
                           max_tokens=900, models=("qwen2.5:7b",))
    except Exception as e:                       # noqa: BLE001
        print(f"（捞提示时抛了 {type(e).__name__}：{str(e)[:60]} —— 只要 got 非空即可）")
    finally:
        mwllm.build_llm = orig
    if not got:
        raise SystemExit("⛔ 没捞到提示 —— 换路装置变了，速度闸不能继续")
    return got[0]


def _scorer():
    class _S:
        def score(self, code, task=None):
            return (0.0, [])
    return _S()


def _task():
    """用**套件里的真题面**（不是我手写的），否则提示长度与产品不一致，速度被低估。"""
    import json
    from types import SimpleNamespace
    row = json.loads((V1 / "bench/evidence/l5_s266/suite_one/t01.jsonl")
                     .read_text(encoding="utf-8").splitlines()[0])
    # ⚠️ 产品读的是**属性**（`task.tests`），不是下标 ⇒ 必须给对象，给 dict 会在
    #    捞到提示之前 AttributeError（V241 踩过第二次）。
    return SimpleNamespace(id=row.get("id") or "t01_parse_duration",
                           prompt=row["prompt"], tests=row["tests"])


def native(model: str, prompt: str, num_predict: int, think: bool) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": think,
        "options": {"num_predict": num_predict, "temperature": 1.0},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        raw = json.loads(r.read().decode())
    wall = time.time() - t0
    ec = raw.get("eval_count") or 0
    ed = raw.get("eval_duration") or 0
    return {"wall_s": round(wall, 1), "eval_count": ec,
            "eval_duration_s": round(ed / 1e9, 2),
            "tok_s": round(ec / (ed / 1e9), 2) if ed else None,
            "done_reason": raw.get("done_reason"),
            "content_chars": len((raw.get("message") or {}).get("content") or ""),
            "thinking_chars": len((raw.get("message") or {}).get("thinking") or "")}


def main() -> int:
    prompt = real_prompt()
    print(f"真实提示 {len(prompt)} 字符")
    combos = [
        ("qwen2.5:7b", "", "", 900),
        ("Qwen3.5-4B", "ollama", "disabled", 900),
        ("Qwen3.5-4B", "ollama", "disabled", 1600),
        ("Qwen3.5-4B", "ollama", "disabled", 2400),
    ]
    rows = []
    for model, prov, think, npred in combos:
        t = {"disabled": False, "enabled": True}.get(think, False)
        tag = f"{model}@{prov or '-'}:{think or 'default'} n={npred}"
        r = native(model, prompt, npred, t)
        r.update(model=model, provider=prov, thinking=think, num_predict=npred)
        rows.append(r)
        print(f"{tag:52s} wall={r['wall_s']:6.1f}s eval={r['eval_count']:5d} "
              f"tok/s={r['tok_s']} done={r['done_reason']} "
              f"content={r['content_chars']}B think={r['thinking_chars']}B")
    (D / "speedgate_v241.json").write_text(
        json.dumps({"prompt_chars": len(prompt), "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    ok = all((r["tok_s"] or 0) >= 15 for r in rows)
    print(f"\n闸：全部 >=15 tok/s ? {ok}")
    worst = max(r["wall_s"] for r in rows)
    print(f"最长单次 {worst}s（门槛 >300 s 停）⇒ {'通过' if worst <= 300 else '⛔ 停'}")
    return 0 if (ok and worst <= 300) else 1


if __name__ == "__main__":
    raise SystemExit(main())

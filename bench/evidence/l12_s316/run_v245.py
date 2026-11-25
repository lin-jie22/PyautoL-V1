"""V245 装置：反馈保真度阶梯（A=现状 / B=+轨迹 / C=+强制自问）。

⛔ 三臂**轮转**跑（i 外层、臂内层），不是一臂跑完再跑下一臂：
   机器升温、显存抖动、模型缓存都会随时间漂移，串行跑会把时间效应算进臂间差异。
⛔ 判分只用 `mw.reflect.verify`（生产同一个函数、同一份 6 条断言）。
⛔ 不打新尺子、不造新题：题面与断言取自 `bench/evidence/l5_s266/suite_one/t01.jsonl`。

用法：python3 run_v245.py [--n 16] [--model qwen2.5:7b]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.reflect import (build_proposal_prompt, failed_actuals,  # noqa: E402
                        parse_proposal, trace_failure, verify)

D = pathlib.Path(__file__).resolve().parent
START_CODE = ROOT / "bench/evidence/l9_s310/cand/90579ecebe633352.py"
SUITE = ROOT / "bench/evidence/l5_s266/suite_one/t01.jsonl"
API = "http://127.0.0.1:11434/v1/chat/completions"

#: C 臂的追加指令。⛔ 只加在 C 上；A/B 的 prompt 必须与生产逐字节同。
C_TAIL = ("\n先写一行【根因】：第几行、在什么输入下错、为什么错（不超过 40 字）。"
          "然后再给完整代码。\n")

_ROOT_CAUSE = re.compile(r"^\s*【根因】.*$", re.M)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _ratio(a: str, b: str) -> float:
    """粗糙相似度：逐行贪心匹配的比例。只用于分「照抄 / 重写」，不进判据。"""
    la = [x.strip() for x in a.splitlines() if x.strip()]
    lb = [x.strip() for x in b.splitlines() if x.strip()]
    if not la or not lb:
        return 0.0
    pool = list(lb)
    hit = 0
    for line in la:
        if line in pool:
            pool.remove(line)
            hit += 1
    return hit / max(len(la), len(lb))


def _ask(model: str, prompt: str, max_tokens: int) -> tuple[str, dict]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1.0,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode()
    req = urllib.request.Request(API, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    dt = time.time() - t0
    txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    usage = d.get("usage") or {}
    return txt, {"wall_s": round(dt, 1), "tokens": usage.get("completion_tokens")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--out", default=str(D / "readings_v245.jsonl"))
    a = ap.parse_args()

    task = json.loads([x for x in SUITE.read_text(encoding="utf-8").splitlines()
                       if x.strip()][0])
    goal, tests = task["prompt"], task["tests"]
    code = START_CODE.read_text(encoding="utf-8")
    base = verify(code, tests)

    why = (f"通过 {base}/{len(tests)}；失败: assert parse_duration('10m') == 600"
           f" ⇒ AssertionError")
    actuals = failed_actuals(code, tests)
    trace = trace_failure(code, tests)

    print(f"起点 sha={_sha(code)} verify={base}/{len(tests)} 断言={len(tests)}")
    print(f"actuals={actuals}")
    print(f"trace 段数={len(trace)}")
    if base != len(tests) - 1:
        print(f"⚠️ 起点不是 5/6（实际 {base}）⇒ 按 PREREG §5-2 全局 UNKNOWN")
        return 2

    prompts = {
        "A": build_proposal_prompt(goal, code, why, actuals),
        "B": build_proposal_prompt(goal, code, why, actuals, trace=trace),
        "C": build_proposal_prompt(goal, code, why, actuals, trace=trace) + C_TAIL,
    }
    for k, v in prompts.items():
        print(f"  臂 {k}: {len(v.encode())} bytes sha={_sha(v)}")

    out = pathlib.Path(a.out)
    out.write_text("", encoding="utf-8")
    n_fail: dict[str, int] = {"A": 0, "B": 0, "C": 0}
    for i in range(a.n):
        for arm in ("A", "B", "C"):                   # 轮转，见模块 docstring
            row = {"arm": arm, "i": i}
            try:
                txt, meta = _ask(a.model, prompts[arm], a.max_tokens)
            except Exception as e:                    # noqa: BLE001
                row.update({"err": f"{type(e).__name__}: {e}"[:160]})
                n_fail[arm] += 1
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"  {arm}#{i:02d} ❌ {row['err'][:60]}", flush=True)
                continue
            # C 臂的【根因】行先摘掉，再走与 A/B 完全相同的 parse_proposal。
            # ⛔ 对 A/B 是 no-op（它们不会被要求写这一行）。
            stripped = _ROOT_CAUSE.sub("", txt)
            cand, fenced = parse_proposal(stripped if stripped != txt else txt)
            got = verify(cand, tests) if cand.strip() else 0
            row.update({
                "sha": _sha(cand) if cand.strip() else None,
                "score": got,
                "fenced": bool(fenced),
                "same_as_start": (cand == code),
                "sim": round(_ratio(cand, code), 3),
                "m_empty_part_guard": bool(re.search(
                    r"if\s+not\s+parts\[1\]|if\s+parts\[1\]|len\(parts\[1\]\)|"
                    r"if\s+parts\[1\]\s*!=|parts\[0\]\s*if", cand)),
                "m_int_empty": bool(re.search(r"int\(parts\[1\]", cand)),
                "has_root_cause": "【根因】" in txt,
                **meta,
            })
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"  {arm}#{i:02d} {got}/{len(tests)} sim={row['sim']:.2f}"
                  f" 照抄={row['same_as_start']} 守卫={row['m_empty_part_guard']}"
                  f" {meta['wall_s']}s {meta.get('tokens')}tok", flush=True)

    print(f"\n失败次数 {n_fail}")
    print(f"读数 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""V248 装置：三臂 A（对照）/ N（带行号，整函数）/ E（带行号，只两行 + 装置替换）。

RUN 前必读：
- 三臂提示的字节与 sha 钉在 PREREG_v248.md §3，构造完先断言，不等就退出。
- 顺序 i 外层、臂内层 A -> N -> E：同一 i 的三次生成时间相邻，防时间漂移被算进臂间差异。
- E 臂的「替换」由装置做（apply_edit），模型只给 LINE / EXPR。缩进由装置补。
- ⛔ 逐条 flush 打印（纪律 216）；⛔ 必须落 cand 全文（纪律 227，已踩两次）。
- 判分只用 mw.reflect.verify。

用法：python3 run_v248.py [--n 16] [--model qwen2.5:7b]
落盘：readings_v248.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.reflect import (build_proposal_prompt, failed_actuals,  # noqa: E402
                        parse_proposal, trace_failure, verify)

D = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(D))
from preflight_v248 import apply_edit, build, parse_edit  # noqa: E402

START = ROOT / "bench/evidence/l9_s310/cand/90579ecebe633352.py"
SUITE = ROOT / "bench/evidence/l5_s266/suite_one/t01.jsonl"
API = "http://127.0.0.1:11434/v1/chat/completions"

PIN = {  # PREREG §3
    "A": (2307, "1bdea4fc92d67817"),
    "N": (2437, "8dc7c93d24b6d955"),
    "E": (2615, "60f2cc5e63c54255"),
}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _ratio(a: str, b: str) -> float:
    la = [x.strip() for x in a.splitlines() if x.strip()]
    lb = [x.strip() for x in b.splitlines() if x.strip()]
    if not la or not lb:
        return 0.0
    pool, hit = list(lb), 0
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
    ntok = usage.get("completion_tokens") or 0
    return txt, {"wall_s": round(dt, 1), "tokens": ntok,
                 "tok_s": round(ntok / dt, 1) if dt > 0 and ntok else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--out", default=str(D / "readings_v248.jsonl"))
    ap.add_argument("--speed-gate", type=float, default=15.0)
    a = ap.parse_args()

    task = json.loads([x for x in SUITE.read_text(encoding="utf-8").splitlines()
                       if x.strip()][0])
    goal, tests = task["prompt"], task["tests"]
    code = START.read_text(encoding="utf-8")
    base = verify(code, tests)
    why = (f"通过 {base}/{len(tests)}；失败: assert parse_duration('10m') == 600"
           f" ⇒ AssertionError")

    p = build(goal, code, why, tests)
    prompts = {"A": p["A"], "N": p["N"], "E": p["E"]}

    print(f"起点 verify={base}/{len(tests)} sha={_sha(code)}")
    for k in ("A", "N", "E"):
        b, s = len(prompts[k].encode()), _sha(prompts[k])
        print(f"  {k} {b}B/{s}  pin={PIN[k][0]}/{PIN[k][1]}", flush=True)
        if (b, s) != PIN[k]:
            print(f"⚠️ {k} 臂与 PREREG 钉的不等 ⇒ 装置漂移，停")
            return 3
    if base != len(tests) - 1:
        print(f"⚠️ 起点不是 5/6（实际 {base}）⇒ UNKNOWN")
        return 2
    # 正例路径再验一次（纪律 62）
    _ok, _err = apply_edit(code, 16, "minutes = int(parts[0])")
    if _err or verify(_ok, tests) != 6:
        print("⚠️ 装置正例路径失效 ⇒ 停")
        return 5

    out = pathlib.Path(a.out)
    out.write_text("", encoding="utf-8")
    speeds: list[float] = []
    nfail = {"A": 0, "N": 0, "E": 0}
    hit = {"A": 0, "N": 0, "E": 0}
    line_hist: dict[int, int] = {}

    for i in range(a.n):
        # ---------------- A ----------------
        try:
            txt, meta = _ask(a.model, prompts["A"], a.max_tokens)
            cand, fenced = parse_proposal(txt)
            got = verify(cand, tests) if cand.strip() else 0
            row = {"arm": "A", "i": i, "score": got, "fenced": fenced,
                   "sha": _sha(cand) if cand.strip() else None,
                   "same_as_start": bool(cand == code),
                   "sim": round(_ratio(cand, code), 3),
                   "cand": cand or None, **meta}
        except Exception as e:                                   # noqa: BLE001
            row = {"arm": "A", "i": i, "err": f"{type(e).__name__}: {e}"[:160]}
            nfail["A"] += 1
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if "err" in row:
            print(f"  A#{i:02d} ERR {row['err'][:70]}", flush=True)
        else:
            hit["A"] += (row["score"] == len(tests))
            if row.get("tok_s"):
                speeds.append(row["tok_s"])
            print(f"  A#{i:02d} {row['score']}/{len(tests)} sim={row['sim']:.2f}"
                  f" copy={row['same_as_start']} {row['wall_s']}s"
                  f" {row['tokens']}tok {row.get('tok_s')}t/s", flush=True)

        # ---------------- N ----------------
        try:
            txt, meta = _ask(a.model, prompts["N"], a.max_tokens)
            cand, fenced = parse_proposal(txt)
            got = verify(cand, tests) if cand.strip() else 0
            row = {"arm": "N", "i": i, "score": got, "fenced": fenced,
                   "sha": _sha(cand) if cand.strip() else None,
                   "same_as_start": bool(cand == code),
                   "sim": round(_ratio(cand, code), 3),
                   "cand": cand or None, **meta}
        except Exception as e:                                   # noqa: BLE001
            row = {"arm": "N", "i": i, "err": f"{type(e).__name__}: {e}"[:160]}
            nfail["N"] += 1
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if "err" in row:
            print(f"  N#{i:02d} ERR {row['err'][:70]}", flush=True)
        else:
            hit["N"] += (row["score"] == len(tests))
            if row.get("tok_s"):
                speeds.append(row["tok_s"])
            print(f"  N#{i:02d} {row['score']}/{len(tests)} sim={row['sim']:.2f}"
                  f" copy={row['same_as_start']} {row['wall_s']}s"
                  f" {row['tokens']}tok {row.get('tok_s')}t/s", flush=True)

        # ---------------- E ----------------
        try:
            txt, meta = _ask(a.model, prompts["E"], a.max_tokens)
            line, expr = parse_edit(txt)
            new, err = apply_edit(code, line, expr)
            got = verify(new, tests) if (new and not err) else 0
            row = {"arm": "E", "i": i, "score": got, "e_text": txt,
                   "line": line, "expr": expr, "edit_err": err or None,
                   "sha": _sha(new) if new else None,
                   "cand": new, **meta}
        except Exception as e:                                   # noqa: BLE001
            row = {"arm": "E", "i": i, "err": f"{type(e).__name__}: {e}"[:160]}
            nfail["E"] += 1
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if "err" in row:
            print(f"  E#{i:02d} ERR {row['err'][:70]}", flush=True)
        else:
            hit["E"] += (row["score"] == len(tests))
            if row.get("tok_s"):
                speeds.append(row["tok_s"])
            if isinstance(row.get("line"), int):
                line_hist[row["line"]] = line_hist.get(row["line"], 0) + 1
            print(f"  E#{i:02d} {row['score']}/{len(tests)} LINE={row['line']}"
                  f" err={row['edit_err']} EXPR={str(row['expr'])[:52]!r}"
                  f" {row['wall_s']}s {row['tokens']}tok", flush=True)

    med = statistics.median(speeds) if speeds else None
    print(f"\n完成 n={a.n} 失败={nfail} 命中6/6={hit}")
    print(f"速度：样本 {len(speeds)} 中位 {med} tok/s （闸 {a.speed_gate}）")
    if med is not None and med < a.speed_gate:
        print(f"⚠️ 速度中位低于闸 {a.speed_gate} ⇒ 读数降级为「压线」（纪律 116）")
    print(f"E 的 LINE 分布：{dict(sorted(line_hist.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

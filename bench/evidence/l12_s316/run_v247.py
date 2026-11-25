"""V247 装置：换动作（A 对照 / Q 语义问答 / S 自述回喂）。

RUN 前必读：
- 逐字节口径写在 PREREG_v247.md §3，本文件构造完会断言 sha，不等就退出。
- 顺序是 i 外层、臂内层 A -> Q -> S：同一 i 的三次生成时间相邻，
  防时间漂移被算进臂间差异；S 必须紧跟它自己那条 Q。
- ⛔ 逐条 flush 打印。长跑被杀时没有过程读数 = 白跑（纪律 216）。
- ⛔ 判分只用 mw.reflect.verify（生产同一函数、同一份 6 条断言）。

用法：python3 run_v247.py [--n 16] [--model qwen2.5:7b]
落盘：readings_v247.jsonl（A/Q/S 三分支，含 Q 原文）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import statistics
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.reflect import (build_proposal_prompt, failed_actuals,  # noqa: E402
                        parse_proposal, trace_failure, verify)

D = pathlib.Path(__file__).resolve().parent
START = ROOT / "bench/evidence/l9_s310/cand/90579ecebe633352.py"
SUITE = ROOT / "bench/evidence/l5_s266/suite_one/t01.jsonl"
API = "http://127.0.0.1:11434/v1/chat/completions"

A_BYTES, A_SHA = 2307, "1bdea4fc92d67817"
Q_BYTES, Q_SHA = 1450, "5ff1b23167549896"

Q_TPL = (
    "下面这个函数没有通过全部测试。请**只回答两个问题**，不要写代码、不要改代码。\n"
    "【题目】\n{goal}\n"
    "【当前代码】\n{code}\n"
    "【问题】输入是 '10m'。按这份代码，s 会先按 'h' 切、再按 'm' 切。\n"
    "  1) 第二次切完之后，parts 的值是什么？（把列表原样写出来）\n"
    "  2) parts[0] 与 parts[1] 当中，哪一个是「分钟数」？\n"
    "格式：两行。第一行 `1) parts=…`，第二行 `2) 分钟数是 parts[…]。`"
)
ANCHOR = "请顺着轨迹找到**第一处**"
INS = ("【你上一轮的自述】你刚才被问到「第 16 行里哪一个 parts 元素是分钟数」，"
       "你的回答原文如下：\n<<<SELF_START>>>\n{answer}\n<<<SELF_END>>>\n"
       "请让上面的代码与你这段自述**一致**。\n\n")
SEM = re.compile(r"int\(\s*parts\[0\]\s*\)\s*\*\s*60")
BUG = re.compile(r"int\(\s*parts\[1\]")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _ratio(a: str, b: str) -> float:
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
    ntok = usage.get("completion_tokens") or 0
    return txt, {"wall_s": round(dt, 1), "tokens": ntok,
                 "tok_s": round(ntok / dt, 1) if dt > 0 and ntok else None}


def _is_whole_code(t: str) -> bool:
    s = t.strip()
    return s.startswith("```") or ("def parse_duration" in s and len(s) > 200)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--out", default=str(D / "readings_v247.jsonl"))
    ap.add_argument("--speed-gate", type=float, default=15.0)
    a = ap.parse_args()

    task = json.loads([x for x in SUITE.read_text(encoding="utf-8").splitlines()
                       if x.strip()][0])
    goal, tests = task["prompt"], task["tests"]
    code = START.read_text(encoding="utf-8")

    base = verify(code, tests)
    actuals = failed_actuals(code, tests)
    trace = trace_failure(code, tests)
    why = (f"通过 {base}/{len(tests)}；失败: assert parse_duration('10m') == 600"
           f" ⇒ AssertionError")

    a_prompt = build_proposal_prompt(goal, code, why, actuals, trace=trace)
    q_prompt = Q_TPL.format(goal=goal, code=code)

    print(f"起点 verify={base}/{len(tests)} sha={_sha(code)}")
    print(f"A {len(a_prompt.encode())}B/{_sha(a_prompt)}  "
          f"Q {len(q_prompt.encode())}B/{_sha(q_prompt)}", flush=True)
    if base != len(tests) - 1:
        print(f"⚠️ 起点不是 5/6（实际 {base}）⇒ PREREG §6-2 全局 UNKNOWN")
        return 2
    if (len(a_prompt.encode()), _sha(a_prompt)) != (A_BYTES, A_SHA):
        print("⚠️ A 臂与 PREREG 钉的字节不等 ⇒ 装置漂移，停")
        return 3
    if (len(q_prompt.encode()), _sha(q_prompt)) != (Q_BYTES, Q_SHA):
        print("⚠️ Q 臂与 PREREG 钉的字节不等 ⇒ 装置漂移，停")
        return 4
    if re.search(r"(?<!\d)600(?!\d)", q_prompt):
        print("⚠️ Q 含独立词 600 ⇒ 泄露答案，停")
        return 5

    out = pathlib.Path(a.out)
    out.write_text("", encoding="utf-8")
    speeds: list[float] = []
    n_fail = {"A": 0, "Q": 0, "S": 0}
    hit = {"A": 0, "Q": 0, "S": 0}

    for i in range(a.n):
        # ---- A ----
        try:
            txt, meta = _ask(a.model, a_prompt, a.max_tokens)
            cand, fenced = parse_proposal(txt)
            got = verify(cand, tests) if cand.strip() else 0
            row = {"arm": "A", "i": i, "score": got, "fenced": fenced,
                   "sha": _sha(cand) if cand.strip() else None,
                   "same_as_start": bool(cand == code),
                   "sim": round(_ratio(cand, code), 3), **meta}
        except Exception as e:                                   # noqa: BLE001
            row = {"arm": "A", "i": i, "err": f"{type(e).__name__}: {e}"[:160]}
            n_fail["A"] += 1
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if "err" in row:
            print(f"  A#{i:02d} ERR {row['err'][:70]}", flush=True)
        else:
            hit["A"] += (row["score"] == len(tests))
            if row.get("tok_s"):
                speeds.append(row["tok_s"])
            print(f"  A#{i:02d} {row['score']}/{len(tests)} sim={row['sim']:.2f}"
                  f" copy={row['same_as_start']} {row['wall_s']}s {row['tokens']}tok"
                  f" {row.get('tok_s')}t/s", flush=True)

        # ---- Q ----
        q_text = ""
        try:
            q_txt, q_meta = _ask(a.model, q_prompt, a.max_tokens)
            q_valid = bool(q_txt.strip()) and not _is_whole_code(q_txt)
            row = {"arm": "Q", "i": i, "valid": q_valid,
                   "q_len": len(q_txt), "q_text": q_txt, **q_meta}
            q_text = q_txt
        except Exception as e:                                   # noqa: BLE001
            row = {"arm": "Q", "i": i, "err": f"{type(e).__name__}: {e}"[:160],
                   "valid": False}
            n_fail["Q"] += 1
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if "err" in row:
            print(f"  Q#{i:02d} ERR {row['err'][:70]}", flush=True)
        else:
            hit["Q"] += row["valid"]
            print(f"  Q#{i:02d} valid={row['valid']} len={row['q_len']}"
                  f" {row['wall_s']}s | {q_text.strip().replace(chr(10), ' / ')[:90]}",
                  flush=True)

        # ---- S（用本条 Q 原文回喂）----
        s_prompt = a_prompt.replace(ANCHOR, INS.replace("{answer}", q_text) + ANCHOR, 1)
        try:
            txt, meta = _ask(a.model, s_prompt, a.max_tokens)
            cand, fenced = parse_proposal(txt)
            got = verify(cand, tests) if cand.strip() else 0
            row = {"arm": "S", "i": i, "score": got, "fenced": fenced,
                   "sha": _sha(cand) if cand.strip() else None,
                   "same_as_start": bool(cand == code),
                   "sim": round(_ratio(cand, code), 3),
                   "sem_in_code": bool(SEM.search(cand)),
                   "kept_bug": bool(BUG.search(cand)),
                   "s_bytes": len(s_prompt.encode()), **meta}
        except Exception as e:                                   # noqa: BLE001
            row = {"arm": "S", "i": i, "err": f"{type(e).__name__}: {e}"[:160]}
            n_fail["S"] += 1
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if "err" in row:
            print(f"  S#{i:02d} ERR {row['err'][:70]}", flush=True)
        else:
            hit["S"] += (row["score"] == len(tests))
            print(f"  S#{i:02d} {row['score']}/{len(tests)} sim={row['sim']:.2f}"
                  f" copy={row['same_as_start']} sem={row['sem_in_code']}"
                  f" bug={row['kept_bug']} {row['wall_s']}s", flush=True)

        # ---- 速度闸：第 3 个 i 之后按中位数判 ----
        if i == 2 and speeds:
            med = statistics.median(speeds)
            print(f"  [速度闸] 中位 {med:.1f} tok/s（门槛 {a.speed_gate}）", flush=True)
            if med < a.speed_gate:
                print("⚠️ 速度不达标 ⇒ 停（纪律 116）", flush=True)
                break

    print(f"\n6/6 命中 A={hit['A']} S={hit['S']}/{a.n}  失败 {n_fail}", flush=True)
    print(f"读数 → {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""V80 前置量测：**差分自检**到底有没有信号。

问题：同一题独立采两份实现，行为分歧在**错**的对上是不是比在**对**的对上更常见？

⛔ 这不是跑批，也不是改内核。它是 §148.5-C 的**开闸前置**：
   若「两份都过测」的实现也经常分歧，那么「分歧处打回」在 61 道没有期望值的题上
   只会误伤 —— C 方案就不该开一整轮。

装置（全部现成件）：
  · 题            = `bench/heldout/*.jsonl`（11 题）
  · 采样通道      = `Planner.answer(goal, clean=True)`（就是 V77 通电的那条边）
  · 过没过测      = `mw/verify.py: run_example(code, tests)`（与评分器同一把尺）
  · 输入电池      = 题面 `tests` 里的**调用表达式**（ast 抽出，**丢掉期望值**）
  · 分歧判定      = 两份实现在同一输入上的返回值/异常类型是否相等（行为比较）

⚠️ 输入电池口径：用的是题集自带的判分用例（内核部署时看不见）。
   ⇒ 本轮只回答「分歧里有没有信号」，**不**回答「不借助判分输入能不能拿到」。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
from mw.config import LLMConfig                     # noqa: E402
from mw.llm import HTTPLLM                          # noqa: E402
from mw.planner import Planner                      # noqa: E402
from mw.verify import run_example                   # noqa: E402

EV = Path("bench/evidence/v6_s169")
#: ⚠️ 自踩（本轮第一版）：写成 ```` ```(?:python)?\s* ```` 时，```` ```c ```` 这个围栏
#:    会把语言标记 `c` **当成代码第一行**留下来（`(?:python)?` 空匹配 + `\s*` 不吃字母）。
#:    后果：即便模型写的是 Python，带 ```` ```py ```` 也会变成语法错。
#:    ⇒ 语言标记一律吞掉，不猜它是哪种语言；猜错就是量具替被测者改答案。
FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.S)
DEF = re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", re.M)

CASES = [
    ("struct", "bench/heldout/struct_convert_heldout.jsonl"),
    ("g7j_n4", "bench/heldout/g7j_n4_heldout.jsonl"),
    ("numeric", "bench/heldout/numeric_logic_heldout.jsonl"),
]

#: 在**子进程**里跑的取样器：把 code 装上，逐条 eval 调用表达式，落 canon 结果。
#: 独立进程的理由：答案里一个死循环就能把量具拖死，而那是**装置**挂掉。
RUNNER = r'''
import json, sys
sys.setrecursionlimit(4000)
payload = json.load(open(sys.argv[1], encoding="utf-8"))
ns = {"__name__": "__v1_probe__"}
try:
    exec(compile(payload["code"], "<answer>", "exec"), ns)
except BaseException as e:
    print(json.dumps({"fatal": type(e).__name__ + ": " + str(e)[:200]}))
    raise SystemExit(0)
fn = payload["fn"]
if not callable(ns.get(fn)):
    print(json.dumps({"fatal": "missing_fn"}))
    raise SystemExit(0)

def canon(v):
    if isinstance(v, dict):
        return ["d", sorted([[canon(k), canon(x)] for k, x in v.items()], key=repr)]
    if isinstance(v, (list, tuple)):
        return ["l", [canon(x) for x in v]]
    if isinstance(v, (set, frozenset)):
        return ["s", sorted([canon(x) for x in v], key=repr)]
    if isinstance(v, float):
        return ["f", repr(v)]
    if v is None or isinstance(v, (bool, int, str)):
        return ["v", repr(v)]
    return ["o", type(v).__name__, repr(v)[:200]]

out = []
for call in payload["calls"]:
    try:
        out.append({"ok": True, "v": canon(eval(call, dict(ns)))})
    except BaseException as e:
        out.append({"ok": False, "e": type(e).__name__})
print(json.dumps({"fatal": "", "outs": out}, ensure_ascii=False))
'''


def load_tasks(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def fn_of(code: str) -> str:
    m = DEF.search(code or "")
    return m.group(1) if m else ""


def extract_code(text: str) -> str:
    m = FENCE.search(text or "")
    return (m.group(1) if m else (text or "")).strip()


def calls_in(node: ast.AST, fn: str) -> list[str]:
    """从一条断言里抽出 `fn(...)` 的**调用表达式**，丢掉期望值那一侧。"""
    out: list[str] = []

    def walk(n: ast.AST) -> None:
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == fn:
            try:
                out.append(ast.unparse(n))
            except Exception:
                pass
            return          # 不再下钻：内层同名调用不单独算一个输入点
        for c in ast.iter_child_nodes(n):
            walk(c)

    walk(node)
    return out


def battery(tests: list[str], fn: str) -> list[str]:
    """题面 `tests` → 去重后的调用表达式列表（不含期望值）。"""
    seen: list[str] = []
    for line in tests:
        body = line.strip()
        if body.startswith("assert "):
            body = body[len("assert "):].strip()
        body = body.split(" ⇒ ")[0].strip()          # 评分器反馈格式的尾巴
        try:
            tree = ast.parse(body, mode="eval")
        except SyntaxError:
            continue
        for c in calls_in(tree, fn):
            if c not in seen:
                seen.append(c)
    return seen


def run_sample(code: str, fn: str, calls: list[str], timeout: float = 15.0) -> list[dict] | str:
    """返回每个输入点的结果，或一个故障字符串。"""
    tmp = tempfile.mkdtemp(prefix="v1diff-")
    try:
        rp = Path(tmp) / "_run.py"
        pp = Path(tmp) / "payload.json"
        rp.write_text(RUNNER, encoding="utf-8")
        pp.write_text(json.dumps({"code": code, "fn": fn, "calls": calls},
                                 ensure_ascii=False), encoding="utf-8")
        try:
            cp = subprocess.run([sys.executable, "-I", str(rp), str(pp)],
                                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "TIMEOUT"
        line = (cp.stdout or "").strip().splitlines()
        if not line:
            return f"NORESULT({(cp.stderr or '')[:120]})"
        data = json.loads(line[-1])
        if data.get("fatal"):
            return f"FATAL({data['fatal'][:120]})"
        return data["outs"]
    finally:
        for p in Path(tmp).iterdir():
            p.unlink(missing_ok=True)
        Path(tmp).rmdir()


def pair_diverges(a: list[dict], b: list[dict]) -> tuple[bool, list[int]]:
    bad = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    return bool(bad), bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=4)
    ap.add_argument("--only", default="", help="只跑某个 task id（pilot 用）")
    ap.add_argument("--tag", default="")
    ap.add_argument("--out", default="probe_diffself")
    args = ap.parse_args()

    cfg = LLMConfig()
    print(f"模型={cfg.model} 温度={getattr(cfg, 'temperature', '?')} "
          f"每题采样={args.samples}")
    llm = HTTPLLM(cfg)
    planner = Planner(llm, None, max_tokens=2048, state_chars=0, salvage=0)

    rows: list[dict] = []
    t0 = time.time()
    for tag, path in CASES:
        for task in load_tasks(path):
            if args.only and task["id"] != args.only:
                continue
            fn = fn_of(task["oracle"])
            calls = battery(task["tests"], fn)
            codes, labels, texts = [], [], []
            for k in range(args.samples):
                t1 = time.time()
                raw = planner.answer(task["prompt"], clean=True)
                code = extract_code(raw)
                codes.append(code)
                texts.append(hashlib.sha256(code.encode()).hexdigest()[:8])
                if not code:
                    labels.append("empty")
                else:
                    why = run_example(code, "\n".join(task["tests"]))
                    labels.append("pass" if why == "" else "fail")
                print(f"  {task['id']:22s} #{k+1} {labels[-1]:5s} "
                      f"{len(code):4d}字符 {time.time()-t1:5.1f}s", flush=True)

            outs = [run_sample(c, fn, calls) if c else "EMPTY" for c in codes]
            #: `run_example` 之外的状态：代码根本装不上（语法错/函数名不对）
            for i, o in enumerate(outs):
                if isinstance(o, str) and labels[i] == "fail":
                    labels[i] = "invalid"
            pairs = []
            for i in range(len(outs)):
                for j in range(i + 1, len(outs)):
                    if labels[i] == "invalid" or labels[j] == "invalid":
                        continue
                    if isinstance(outs[i], str) or isinstance(outs[j], str):
                        continue
                    g = ("PP" if labels[i] == labels[j] == "pass"
                         else "FF" if labels[i] == labels[j] == "fail" else "MIX")
                    div, bad = pair_diverges(outs[i], outs[j])
                    pairs.append({"i": i, "j": j, "group": g,
                                  "diverge": div, "bad_idx": bad})
            rows.append({
                "suite": tag, "id": task["id"], "fn": fn,
                "n_inputs": len(calls), "calls": calls,
                "labels": labels, "text_sha": texts,
                "text_unique": len(set(texts)),
                "codes": codes,
                "pairs": pairs,
                "faults": [o if isinstance(o, str) else "" for o in outs],
            })
            print(f"→ {task['id']:22s} 输入点={len(calls)} labels={labels} "
                  f"对={len(pairs)} 分歧={sum(1 for p in pairs if p['diverge'])}",
                  flush=True)

    payload = {"meta": {"model": cfg.model,
                        "temp": getattr(cfg, "temperature", None),
                        "samples": args.samples,
                        "elapsed_s": round(time.time() - t0, 1)},
               "tasks": rows}
    name = f"{args.out}{('_' + args.tag) if args.tag else ''}.json"
    (EV / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
    print(f"\n落盘 {EV / name}  用时 {payload['meta']['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

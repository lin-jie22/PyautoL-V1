#!/usr/bin/env python3
"""§74 的机制量具：把 `at04` 的每一份**唯一**交付物离线跑一遍，逐条断言复算。

为什么需要它：`runs.jsonl` 只给「6/16 + AssertionError」这种汇总，
说不出「为什么重写后反而错」。本脚本给三样东西：

  1. **逐条断言**的通过/失败（16 条全跑，不是在真值上调参）
  2. **写出的字节**与题面期望 `b'alpha\ndelta\n'` 的直接比对（尾换行是失分点）
  3. 交付物之间的**逐行 diff**（报名字的重写 vs 盲报的重写，改动半径差多少）

⛔ 本脚本只读台账，不重跑模型；它证明的是「这些答案各自会得几分」，
   不证明「模型为什么会那样写」——后者只能说日志里看得见的那部分。
⚠️ 内置反证：`--selftest` 会往期望里塞一份**故意写错尾换行**的程序，
   它必须被判 6/16；若它也满分，说明这套复算与真判分器不同源。

用法：
  python3 bench/evidence/v1b_s104/probe_at04.py
  python3 bench/evidence/v1b_s104/probe_at04.py --selftest
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import mw.verify as V  # noqa: E402

HERE = Path(__file__).resolve().parent
ARMS = ("a0_v4", "a2_v4", "a1_v4")


def load_spec(suite: Path, tid: str = "at04") -> dict:
    for line in open(suite, encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line)
            if r["id"] == tid:
                return r
    raise SystemExit(f"题集里没有 {tid}")


def collect(at04: str = "at04") -> dict[str, dict]:
    """按 sha 去重收集三臂的交付物（顺序 = 首次出现顺序）。"""
    out: dict[str, dict] = {}
    for arm in ARMS:
        p = HERE / arm / "answers.jsonl"
        for line in open(p, encoding="utf-8"):
            r = json.loads(line)
            if r.get("kind") != "answer" or r.get("task_id") != at04:
                continue
            out.setdefault(r["sha256"][:8], {"answer": r["answer"], "arms": []})
            if arm not in out[r["sha256"][:8]]["arms"]:
                out[r["sha256"][:8]]["arms"].append(arm)
    return out


def run_asserts(code: str, tests: list[str]) -> tuple[int, list[tuple]]:
    """在临时目录里跑一遍 16 条断言，返回 (通过数, 失败列表)。"""
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    try:
        ns: dict = {}
        exec(compile(code, "<answer>", "exec"), ns)
        ok, fails = 0, []
        for i, t in enumerate(tests, 1):
            shutil.rmtree("out", ignore_errors=True)
            try:
                exec(compile(t, f"<assert{i}>", "exec"), ns)
                ok += 1
            except Exception as e:  # noqa: BLE001 —— 判分器也是这么吞的
                fails.append((i, type(e).__name__, str(e)[:50]))
        return ok, fails
    finally:
        os.chdir(old)
        shutil.rmtree(d, ignore_errors=True)


def written_bytes(code: str) -> bytes | None:
    """直接看「写出来的字节」，这是 `at04` 真正的判分面（规则 3 要求尾换行）。"""
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    try:
        ns: dict = {}
        exec(compile(code, "<answer>", "exec"), ns)
        ns["write_or_fail"](["alpha", "delta"], "out/o.txt")
        return Path("out/o.txt").read_bytes()
    except Exception:  # noqa: BLE001
        return None
    finally:
        os.chdir(old)
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default=str(ROOT / "bench/specgen/tasks_v4.jsonl"))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    spec = load_spec(Path(args.suite))
    tests = spec["tests"]
    want = b"alpha\ndelta\n"
    print(f"at04 断言数 = {len(tests)}；规则 3 期望字节 = {want!r}")

    if args.selftest:
        print("\n=== A4-反证：把正确程序的行尾换行删掉，必须掉到 6/16 ===")
        good = ("def write_or_fail(rows, path):\n"
                "    import os\n"
                "    if any('#' in r for r in rows):\n"
                "        raise ValueError('x')\n"
                "    os.makedirs(os.path.dirname(path), exist_ok=True)\n"
                "    with open(path, 'w', encoding='utf-8') as f:\n"
                "        for r in rows:\n"
                "            c = r.strip()\n"
                "            if c:\n"
                "                f.write(c + '\\n')\n")
        bad = good.replace("f.write(c + '\\n')", "f.write(c)")
        for nm, src, expect in (("正确版", good, 16), ("删尾换行版", bad, 6)):
            ok, fails = run_asserts(src, tests)
            got = written_bytes(src)
            flag = "PASS" if ok == expect else "**FAIL**"
            print(f"  {flag:8s} {nm:10s} {ok}/16 期望={expect} "
                  f"写出={got!r} 首败={fails[0] if fails else '-'}")
        return 0

    answers = collect()
    print(f"\n=== A1 唯一交付物 {len(answers)} 份（三臂按 sha 去重）===")
    print(f"{'sha':10s} {'字数':>5s} {'臂':16s} {'断言':>6s} {'静态判据':8s} 写出字节 / 首败")
    rows = []
    for h, d in answers.items():
        code = V.extract_code(d["answer"])
        ok, fails = run_asserts(code, tests)
        bad = V.undefined_names(code)
        gate = "拦:" + ",".join(n for _f, n in bad) if bad else "放行"
        got = written_bytes(code)
        rows.append((h, len(d["answer"]), d["arms"], ok, gate, got, fails, code))
        print(f"{h:10s} {len(d['answer']):5d} {'+'.join(d['arms']):16s} {ok:3d}/16 "
              f"{gate:8s} {got!r} {'' if not fails else fails[0][1]}")

    print("\n=== A2 尾换行是不是失分点（唯一一份放行却不满分的答案）===")
    for h, n, arms, ok, gate, got, fails, _c in rows:
        if gate == "放行" and ok < 16:
            print(f"  {h}（{'+'.join(arms)}）：放行、{ok}/16、写 {got!r} ≠ 期望 {want!r} ⇒ "
                  f"差的就是尾换行（{'是' if got == want[:-1] else '否'}）")
    print("  ⛔ 这一条推翻「绑定 os 就能满分」：它绑定了，仍 6/16。")

    print("\n=== A3 改动半径（两份重写相对「未重写」的原程序改了多少行）===")
    base = next((r for r in rows if r[0] == "1b5f6119"), None)
    if base is None:
        print("  ⚠️ 本轮没有 1b5f6119，跳过")
    else:
        b = base[7].splitlines()
        for h, n, arms, ok, gate, got, fails, code in rows:
            if h == "1b5f6119":
                continue
            d = list(difflib.unified_diff(b, code.splitlines(), lineterm="", n=0))
            add = sum(1 for l in d if l.startswith("+") and not l.startswith("+++"))
            rem = sum(1 for l in d if l.startswith("-") and not l.startswith("---"))
            print(f"  {h}（{'+'.join(arms)}，{ok}/16）：相对原程序 +{add} / −{rem} 行，"
                  f"静态判据={gate}，写出={got!r}")
        print("  ⇒ 两份重写都补上了 os。⚠️ 行数差不代表实质：拿 16/16 的那份**实质只多一行** "
              "`import os`（其余 +/− 行是单双引号互换，语义相同）；"
              "6/16 的那份把校验、建目录、写循环**三处都换了写法**，写循环丢了尾换行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

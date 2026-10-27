#!/usr/bin/env python3
"""§71 的三因归因（**预注册在跑批之前**，见同目录 PREREG.md 第四节）。

PREREG 说：若 24 次全 0，必须先把三种**可区分**的原因量出来，才能说「模型不会写文件」：

  a 交付层  答案里有没有 `def <题面点名的函数>`
  b 内容层  答案里有没有**写副作用**的调用（`.write(` / `write_text` / `write_bytes` /
            `json.dump` / `makedirs` / `mkdir` / `os.replace` / `os.rename`）
  c 接线层  `failed_scorer` / `skipped_no_answer` / oracle 是否也 0

⭐ 本脚本的自检不是装饰：§70.4 ③ 的教训是「检测器不对自己先动手，它给出的数和编的数同形」。
   自检不过 ⇒ 直接退出，不打印任何归因结论。

用法：
  python3 bench/evidence/artifact_run_s101/analyze_v4.py [--bench /tmp/v1_s101_bench_v4_s101]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# ── 检测器 ──────────────────────────────────────────────────────────────────
WRITE_TOKENS = (
    ".write(", "write_text", "write_bytes", "json.dump", "makedirs", "mkdir",
    "os.replace", "os.rename", "touch(", "shutil.copy",
)
#: `open(` 单独不算 —— 读文件也是 open(。只有带写模式才算。
WRITE_MODE = re.compile(r"""open\(\s*[^)]*?['"][rwxbt+]*[wax][rwxbt+]*['"]""")


def has_write(src: str) -> tuple[bool, list[str]]:
    hit = [t for t in WRITE_TOKENS if t in src]
    if WRITE_MODE.search(src):
        hit.append("open(mode=w)")
    return bool(hit), hit


def expected_fn(prompt: str) -> str | None:
    for pat in (r"函数名必须是\s*([A-Za-z_]\w*)",
                r"写一个函数\s*([A-Za-z_]\w*)",
                r"实现一个函数\s*([A-Za-z_]\w*)"):
        m = re.search(pat, prompt)
        if m:
            return m.group(1)
    return None


def has_def(src: str, fn: str | None) -> bool:
    if not fn:
        return bool(re.search(r"^\s*def\s+\w+", src, re.M))
    return bool(re.search(rf"^\s*def\s+{re.escape(fn)}\s*\(", src, re.M))


# ── 自检（反证）：每条都必须给出预期，否则不许出结论 ────────────────────────
SELFTEST = [
    ("pure 形态（有 def、无写调用）",
     "def write_lines(rows, path):\n    return ''.join(r + '\\n' for r in rows)\n", True, False),
    ("真写（fh.write）",
     "def write_lines(rows, path):\n    with open(path, 'w') as fh:\n        fh.write('x')\n", True, True),
    ("write_text",
     "from pathlib import Path\ndef f(p):\n    Path(p).write_text('x')\n", True, True),
    ("只读 open（不算写）",
     "def f(p):\n    with open(p) as fh:\n        return fh.read()\n", True, False),
    ("open 带 w 模式但无 .write",
     "def f(p):\n    open(p, 'w').close()\n", True, True),
    ("无 def 的散文",
     "这个函数应该把行写进文件。\n", False, False),
]


def selftest() -> bool:
    ok = True
    print("=== 检测器自检（6 例）===")
    for name, src, want_def, want_write in SELFTEST:
        d = has_def(src, "f") if "def f(" in src else has_def(src, None)
        w, _ = has_write(src)
        good = (d == want_def) and (w == want_write)
        ok &= good
        print(f"  {'PASS' if good else '**FAIL**':8s} {name:26s} def={d} write={w} "
              f"(exp {want_def}/{want_write})")
    print("自检全通过" if ok else "⛔ 自检失败 ⇒ 不打印归因结论")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default="/tmp/v1_s101_bench_v4_s101")
    args = ap.parse_args()

    if not selftest():
        return 2

    bdir = Path(args.bench)
    runs = [json.loads(l) for l in open(bdir / "runs.jsonl")]
    assert runs, "runs.jsonl 为空"
    run = runs[-1]
    rep = run["report"]
    per = {(t["id"], t["rep"]): t for t in rep["per_task"]}

    # 题面（拿期望函数名）
    suite = [json.loads(l) for l in open(ROOT / "bench/specgen/tasks_v4.jsonl")]
    pmap = {r["id"]: r["prompt"] for r in suite}
    grids = {r["id"]: len(r["tests"]) for r in suite}

    answers = [json.loads(l) for l in open(bdir / "answers.jsonl")
               if json.loads(l).get("kind") == "answer"]

    print(f"\n=== 接线层（c）===")
    print(f"  attempted={rep['attempted']} recorded={rep['recorded']} "
          f"skipped_no_answer={rep['skipped_no_answer']} "
          f"failed_scorer={rep['failed_scorer']}")
    print(f"  calibration ok={run['calibration']['ok']} "
          f"margin={run['calibration']['margin']} middle_n={run['calibration']['middle_n']}")
    print(f"  caliber_sig={run['caliber_sig']} "
          f"suite_sha={run['caliber']['suite_sha'][:12]} "
          f"scorer_sha={run['caliber']['scorer_sha'][:12]}")

    print(f"\n=== 分数 ===")
    m = run["measurement"]
    print(f"  mean={m['mean']:.4f} std={m['std']:.4f} n={m['n']} "
          f"lo={m['lo']:.4f} hi={m['hi']:.4f}")

    print(f"\n=== 逐题 × 逐 rep（格数按 {sum(grids.values())} 计）===")
    ids = [r["id"] for r in suite]
    hdr = "  任务  格数  " + "  ".join(f"r{r}" for r in range(1, run["repeat"] + 1))
    print(hdr)
    for tid in ids:
        cells = []
        for r in range(1, run["repeat"] + 1):
            t = per.get((tid, r))
            cells.append(f"{t['score']:.4f}" if t else "  —  ")
        print(f"  {tid:5s} {grids[tid]:4d}  " + "  ".join(cells))

    print(f"\n=== 三因归因（a 交付层 × b 内容层）===")
    # ⚠️ 已知欠账：`answers.jsonl` 的 `rep` **恒为 1**（只落末轮）。
    #    所以逐 rep 的答案在文件里是同一份被写 3 次 —— 按 sha256 去重才是真读数。
    #    分数仍用 `runs.jsonl` 的 per_task（那份 rep 是对的），两者分开统计。
    uniq: dict[tuple[str, str], dict] = {}
    for a in answers:
        uniq.setdefault((a["task_id"], a.get("sha256") or a["answer"][:200]), a)
    print(f"  answers 行 {len(answers)} 条，按 (题, sha256) 去重后 "
          f"**{len(uniq)}** 条唯一答案")

    tab: dict[tuple[bool, bool], list[float]] = {}
    rows = []
    for (tid, _sha), a in uniq.items():
        src = a["answer"]
        fn = expected_fn(pmap[tid])
        d = has_def(src, fn)
        w, tok = has_write(src)
        # 该题的平均分（跨 rep，来自 runs.jsonl）
        scs = [per[(tid, r)]["score"] for r in range(1, run["repeat"] + 1)
               if (tid, r) in per]
        sc = sum(scs) / len(scs) if scs else float("nan")
        why = per[(tid, 1)]["why"] if (tid, 1) in per else "?"
        tab.setdefault((d, w), []).append(sc)
        rows.append((tid, a["rep"], sc, d, w, tok, why))

    print("  ┌─────────┬─────────┬──────┬────────────┐")
    print("  │ 有 def  │ 有写调用│ 次数 │ 平均分     │")
    print("  ├─────────┼─────────┼──────┼────────────┤")
    for d in (True, False):
        for w in (True, False):
            v = tab.get((d, w), [])
            mean = f"{sum(v)/len(v):.4f}" if v else "—"
            print(f"  │ {str(d):7s} │ {str(w):7s} │ {len(v):4d} │ {mean:10s} │")
    print("  └─────────┴─────────┴──────┴────────────┘")

    print(f"\n=== 逐条明细（每题一条唯一答案，分数取跨 rep 均值）===")
    for tid, r, sc, d, w, tok, why in sorted(rows):
        mark = "⭐pure 形态" if (d and not w) else ""
        print(f"  {tid:5s} r{r} score={sc:.4f} def={str(d):5s} write={str(w):5s} "
              f"{','.join(tok)[:34]:34s} {why[:18]:18s} {mark}")

    # 预注册里点名的那条判据
    pure_n = sum(1 for r in rows if r[3] and not r[4])
    allz = all(r[2] == 0.0 for r in rows if r[2] == r[2])
    print(f"\n=== 预注册判据 ===")
    print(f"  全 0？{'是' if allz else '否'}")
    print(f"  「有 def 但无写调用」= {pure_n}/{len(rows)} 条")
    if allz and pure_n:
        print("  ⇒ 归因落在 (a 有 / b 无)：**不是没交代码，是交的代码不写文件**")
        print("    这正好是 `pure` 变体的形态 —— v1~v3 那类题的满分答案。")
    elif allz:
        print("  ⇒ 全 0 但无 pure 形态 ⇒ 归因未落地，必须回看逐条明细（不许写「模型不会」）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""V112：V106 的「596 个邻居 0 上升」里，有多少是**行为等价**的。

零模型、零网络。判据 = 复用 V111 的行为指纹（分数由指纹重算）。

§0 复现校验：合法邻居数 596、分数分布 0.4x57 / 0.2x204 / 0.0x335
§1 行为等价比例（Y1）
§2 0.4 档里的互异行为（Y2）
§3 判决（Y3）
"""
from __future__ import annotations

import ast
import collections
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
ROOT = EV.parent.parent
sys.path.insert(0, str(EV / "v6_s195"))
sys.path.insert(0, str(EV / "v6_s198"))
sys.path.insert(0, str(EV / "v6_s200"))
import landscape as L          # noqa: E402  同一份 char_mutants / compiles
import idiom_transfer as I     # noqa: E402  harvest
import behaviour as B          # noqa: E402  parse_test

TIMEOUT = 8.0
_ASSERT = re.compile(r"^\s*assert\s+(.+?)\s*==\s*(.+?)\s*$", re.S)
_EXC = re.compile(r"^(\w*(?:Error|Exception))\b", re.M)
TID = "g7d3_fmt_amount"
BASE_HASH = "075b4099"   # 可由 argv[1] 覆盖（§7 事后扩展用）


def run_one(code: str, tmpl: str) -> tuple[int, str, str]:
    wd = tempfile.mkdtemp(prefix="v112_")
    try:
        p = subprocess.run([sys.executable, "-I", "-c", f"{code}\n\n{tmpl}\n"],
                           capture_output=True, text=True, timeout=TIMEOUT, cwd=wd)
        exc = ""
        if p.returncode != 0:
            hits = _EXC.findall(p.stderr or "")
            exc = hits[-1] if hits else f"rc={p.returncode}"
        return p.returncode, (p.stdout or "").strip(), exc
    except subprocess.TimeoutExpired:
        return -9, "", "Timeout"
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def fingerprint(code: str, tests: list) -> list:
    out = []
    for test in tests:
        expr, _ = B.parse_test(str(test))
        if expr is None:
            out.append("UNPARSED")
            continue
        rc, so, exc = run_one(code, f"print(repr({expr}))")
        out.append(so if rc == 0 else f"ERROR:{exc}")
    return out


def main() -> None:
    global BASE_HASH
    if len(sys.argv) > 1:
        BASE_HASH = sys.argv[1]
    t0 = time.time()
    task = I.task_of(TID)
    tests = [str(x) for x in task["tests"]]
    exps = [B.parse_test(x)[1] for x in tests]

    def score_of_fp(fp: list) -> float:
        passed = 0
        for got, exp in zip(fp, exps):
            if got.startswith("ERROR:") or got == "UNPARSED":
                continue
            try:
                if ast.literal_eval(got) == exp:
                    passed += 1
            except Exception:
                pass
        return round(passed / len(exps), 4)

    impls = {h: L.code_of(v) for h, v in L.harvest().items()}
    base = impls[BASE_HASH]
    base_fp = fingerprint(base, tests)
    base_score = score_of_fp(base_fp)
    print("## §0 起点与校准")
    print(f"  起点 {BASE_HASH}　指纹 {base_fp}")
    print(f"  由指纹重算的分数 = {base_score}")

    cands = [(lab, m) for lab, m in L.char_mutants(base) if L.compiles(m)]
    ref_n = 596 if BASE_HASH == "075b4099" else 722
    print(f"  合法 1-编辑邻居 = {len(cands)}（V106 报 {ref_n}）"
          f"　{'✅ 复现' if len(cands) == ref_n else '⛔ 未复现'}")
    if len(cands) != ref_n:
        print("  ⇒ ⛔ 装置失效：邻居数对不上，后续读数作废")
        return

    # ---------- §1a 契约检查（AST，零子进程）----------
    def entry(code: str):
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None
        for n in tree.body:
            if isinstance(n, ast.FunctionDef):
                return (n.name, len(n.args.args))
        return None

    cc = collections.Counter()
    for _lab, m in cands:
        e = entry(m)
        if e is None:
            cc["no_func"] += 1
        elif e[0] != "fmt_amount":
            cc[f"wrong_name"] += 1
        elif e[1] != 1:
            cc["wrong_arity"] += 1
        else:
            cc["ok"] += 1
    print("\n## §1a 契约检查：596 个「合法」邻居里有多少还定义了 fmt_amount(x)")
    print(f"  {dict(cc)}")
    print(f"  ⇒ 契约破坏 {len(cands) - cc['ok']} / {len(cands)} = "
          f"{(len(cands) - cc['ok']) / len(cands):.1%}")
    print("  （`compile()` 通过 ≠ 还是同一个函数：改名只改绑定，语法照样合法）")

    print("\n## §1b 逐个邻居算行为指纹（2980 次子进程，约 8 分钟）")
    rows = []
    for i, (lab, mut) in enumerate(cands):
        fp = fingerprint(mut, tests)
        rows.append(dict(lab=lab, code=mut, fp=fp, score=score_of_fp(fp),
                         same=(fp == base_fp)))
        if (i + 1) % 100 == 0:
            el = time.time() - t0
            print(f"    … {i + 1}/{len(cands)}　已用 {el / 60:.1f} 分钟"
                  f"　预计剩 {el / (i + 1) * (len(cands) - i - 1) / 60:.1f} 分钟", flush=True)

    dist = collections.Counter(r["score"] for r in rows)
    print(f"\n  分数分布（分母 {len(rows)}）：{dict(sorted(dist.items()))}")
    ref = ({0.4: 57, 0.2: 204, 0.0: 335} if BASE_HASH == "075b4099"
           else {1.0: 62, 0.6: 199, 0.4: 46, 0.2: 7, 0.0: 408})
    ok = all(dist.get(k, 0) == v for k, v in ref.items())
    print(f"  V106 报 {ref}　⇒ {'✅ 复现' if ok else '⛔ 未复现，装置失效'}")

    same = [r for r in rows if r["same"]]
    print(f"\n## §2 判决 Y1：行为等价比例")
    print(f"  等价 {len(same)}/{len(rows)} = {len(same) / len(rows):.1%}（押 ≤20%）")
    print(f"  ⇒ {'✅ 押中（V106 站得住：邻域不是退化的）' if len(same) / len(rows) <= 0.2 else '⛔ 押破（邻域退化）'}")
    print(f"  等价邻居的编辑动作（前 20）：{[r['lab'] for r in same][:20]}")
    print(f"  等价邻居的分数分布：{dict(collections.Counter(r['score'] for r in same))}")

    print(f"\n## §3 判决 Y3：等价邻居数量 ≥ 4")
    print(f"  {len(same)} 个　⇒ {'✅ 押中' if len(same) >= 4 else '⛔ 破'}")

    hi = [r for r in rows if abs(r["score"] - 0.4) < 1e-9]
    fps = collections.Counter(tuple(r["fp"]) for r in hi)
    print(f"\n## §4 判决 Y2：0.4 档里有多少种不同行为")
    print(f"  0.4 档邻居 {len(hi)} 个　互异行为 **{len(fps)}** 种（押 ≥20）")
    print(f"  ⇒ {'✅ 押中（0.4 是宽台地）' if len(fps) >= 20 else '⛔ 破（0.4 是一个点）'}")
    print("  行为 → 人数（按人数降序，前 12）：")
    for fp, n in fps.most_common(12):
        print(f"    {n:3d} 人　{fp}")
    if len(fps) > 12:
        print(f"    …… 其余 {len(fps) - 12} 种各 1 人")

    all_fps = collections.Counter(tuple(r["fp"]) for r in rows)
    print(f"\n## §5 新行为的绝对数")
    print(f"  596 个邻居 → **{len(all_fps)} 种行为**（含原实现的 1 种）")
    print(f"  即 **{len(all_fps) - 1} 个新行为**；分数只把它们分成 {len(dist)} 档")
    print(f"  行为/分数 = {len(all_fps)}/{len(dist)} = {len(all_fps) / len(dist):.1f}")

    print(f"\n## §6 ⭐ 每个分数档里有多少种**行为**（不是多少个邻居）")
    print(f"  {'档':6s} {'邻居数':>6s} {'行为数':>6s} {'原行为占':>8s}  说明")
    for sc in sorted(dist, reverse=True):
        grp = [r for r in rows if abs(r["score"] - sc) < 1e-9]
        fpc = collections.Counter(tuple(r["fp"]) for r in grp)
        same_n = fpc.get(tuple(base_fp), 0)
        print(f"  {sc:<6} {len(grp):6d} {len(fpc):6d} {same_n:8d}  "
              f"{'全同原行为' if len(fpc) == 1 and same_n else ''}")
    print("  ⭐ 「邻居数」是字节距离；「行为数」才是候选空间的宽度。")

    out = dict(n=len(rows), dist={str(k): v for k, v in dist.items()},
               rows=[dict(lab=r["lab"], fp=list(r["fp"]), score=r["score"], same=r["same"])
                     for r in rows],
               n_same=len(same), same_labels=[r["lab"] for r in same],
               hi_n=len(hi), hi_fps=len(fps), n_fps=len(all_fps))
    (HERE / f"equiv_{BASE_HASH}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  用时 {(time.time() - t0) / 60:.1f} 分钟")


if __name__ == "__main__":
    main()

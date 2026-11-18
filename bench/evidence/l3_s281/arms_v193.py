"""V193：`--verify-impl` 的牙齿 —— 8 题 × 4 实现。

⛔ 不跑模型。只用题集自带的 oracle / partial + 本轮写的两种桩。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
SUITES = ("bench/heldout/struct_convert_heldout.jsonl",
          "bench/heldout/g7j_n4_heldout.jsonl")


def tasks() -> list[dict]:
    out = []
    for p in SUITES:
        for line in (ROOT / p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                t = json.loads(line)
                t["suite"] = pathlib.Path(p).stem
                out.append(t)
    return out


def fn_of(code: str) -> str:
    import ast
    for n in ast.parse(code).body:
        if isinstance(n, ast.FunctionDef):
            return n.name
    return ""


def make_impls(t: dict) -> dict[str, str]:
    fn = fn_of(t["oracle"])
    sig = _sig_line(t["oracle"], fn)
    return {
        "oracle": t["oracle"],
        "partial": t["partial"],
        "stub_none": f"{sig}\n    return None\n",
        "stub_star": f"def {fn}(*a, **k):\n    return None\n",
    }


def _sig_line(code: str, fn: str) -> str:
    """照抄题面实现的签名行（不含函数体）。"""
    for ln in code.splitlines():
        if ln.startswith(f"def {fn}"):
            return ln.rstrip()
    return f"def {fn}()"


def run_one(path: pathlib.Path, fn: str) -> dict:
    t0 = time.time()
    r = subprocess.run([sys.executable, "-u", str(ROOT / "run.py"),
                        "--verify-impl", str(path), "--fn", fn, "--json"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=600)
    dt = time.time() - t0
    out = r.stdout or ""
    tally, rows = None, None
    i = out.find("{")
    if i >= 0:
        try:
            d = json.loads(out[i:])
            tally, rows = d.get("tally"), d.get("rows")
        except json.JSONDecodeError:
            pass
    return {"rc": r.returncode, "tally": tally, "rows": rows, "wall": round(dt, 2),
            "tail": out.strip().splitlines()[-2:] if out.strip() else [],
            "err": (r.stderr or "").strip().splitlines()[-1:] }


def main() -> int:
    R: dict = {"prereg": "PREREG_v193.md", "tasks": []}
    for t in tasks():
        fn = fn_of(t["oracle"])
        impls = make_impls(t)
        row = {"id": t["id"], "suite": t["suite"], "fn": fn, "impls": {}}
        with tempfile.TemporaryDirectory(prefix="v193-") as d:
            for name, code in impls.items():
                p = pathlib.Path(d) / f"{name}.py"
                p.write_text(code, encoding="utf-8")
                row["impls"][name] = run_one(p, fn)
        R["tasks"].append(row)
        print(f"{t['id']:<22}", " ".join(
            f"{k}={'ERR' if v['tally'] is None else v['tally']}" for k, v in row["impls"].items()))
        sys.stdout.flush()

    # ── 判据 ────────────────────────────────────────────────────────────
    # ⚠️ 初版写成 `(tall(...) or -1) != 0` —— `0 or -1` 得 **-1**（0 是假值），
    #    于是「桩一格 VARIES 都没有」这个**正确读数**被自己的判据代码判成 ❌。
    #    ⛔ 判「等于零」时不许用 `or` 兜底（纪律 63 同型：判数值的原语也是判据）。
    def tall(r, impl, key="VARIES"):
        t = r["impls"][impl]["tally"]
        if t is None:
            return None
        return t.get(key, 0)

    P1 = [r["id"] for r in R["tasks"] if not (tall(r, "oracle") or 0) > 0]
    P2 = [r["id"] for r in R["tasks"] if tall(r, "stub_none") != 0]
    P3 = [r["id"] for r in R["tasks"] if r["impls"]["oracle"]["tally"] != r["impls"]["partial"]["tally"]]
    P4 = [r["id"] for r in R["tasks"]
          if not (r["impls"]["stub_star"]["rc"] == 2
                  and any("一格也没跑" in ln for ln in r["impls"]["stub_star"]["tail"]))]
    wall = sum(v["wall"] for r in R["tasks"] for v in r["impls"].values())

    R["P1_bad"] = P1
    R["P2_bad"] = P2
    R["P3_diff"] = P3
    R["P4_bad"] = P4
    R["wall_total"] = round(wall, 1)
    R["P1_ok"] = not P1
    R["P2_ok"] = not P2
    R["P3_ok"] = not P3
    R["P4_ok"] = not P4
    R["P5_ok"] = wall <= 180.0

    print("=" * 74)
    print(f"[P1] oracle VARIES>0：坏格 {P1} ⇒ {'✅' if R['P1_ok'] else '❌'}")
    print(f"[P2] stub_none VARIES==0：坏格 {P2} ⇒ {'✅' if R['P2_ok'] else '❌'}")
    print(f"[P3] oracle ≡ partial 的 tally：不等 {P3} ⇒ {'✅ 判别力 0（押中）' if R['P3_ok'] else '❌ 居然分得开'}")
    print(f"[P4] stub_star 一律 rc=2 且报「一格也没跑」：坏格 {P4} ⇒ {'✅' if R['P4_ok'] else '❌'}")
    print(f"[P5] 32 次调用总耗时 {R['wall_total']}s ≤180 ⇒ {'✅' if R['P5_ok'] else '❌'}")
    print("=" * 74)
    print("逐题 tally：")
    for r in R["tasks"]:
        print(f"  {r['id']:<22}", {k: (v['tally'] or 'ERR') for k, v in r["impls"].items()})

    (ROOT / "bench/evidence/l3_s281/readings_v193.json").write_text(
        json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nreadings 已写。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

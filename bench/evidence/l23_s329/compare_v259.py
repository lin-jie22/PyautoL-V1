#!/usr/bin/env python3
"""V259: diagnose why 4 zero-score cells never got rejected by the gate.

Three layers, all local (0 model calls, 0 production changes):

  L1  Was the information ever given?  Compare the delivered code under
      three conditions: V254 arm A (rewrite, no new info), V254 arm B
      (rewrite + first failing assertion verbatim), V258 (fresh single round).
      If B == fresh, the assertion did not act as a correction signal.

  L2  Could the gate have seen it?  Call the production _derive() on each
      prompt with static=1, exec_=1 and print the dispatched checks plus the
      example assertions extracted from the prompt.

  L3  What is the first divergence?  Run the delivered code on the suite's
      first failing assertion and print the actual value next to the expected.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
CELLS = ["g7t2_parse_pairs", "s02_limiter", "m02_vfs", "m03_tiered"]

FENCE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*$", re.M)


def strip_fence(s: str) -> str:
    lines = s.splitlines()
    out, i = [], 0
    while i < len(lines):
        if FENCE.match(lines[i]):
            i += 1
            while i < len(lines) and not FENCE.match(lines[i]):
                out.append(lines[i])
                i += 1
            i += 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def load_jsonl(p):
    out = []
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("{"):
            out.append(json.loads(line))
    return out


def diff_lines(a: str, b: str):
    import difflib
    al, bl = a.splitlines(), b.splitlines()
    n = 0
    for ln in difflib.unified_diff(al, bl, lineterm="", n=0):
        if ln.startswith(("+++", "---", "@@")):
            continue
        n += 1
    return n


def run_code_get(code: str, expr: str, setup: str = ""):
    """Exec code in a subprocess and eval expr; return repr or error type."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "probe.py"
        p.write_text(
            "import json,sys\n"
            + code
            + "\n\n"
            + setup
            + "\n\ntry:\n"
            + f"    _v = ({expr})\n"
            + "    print(json.dumps({'ok':True,'v':repr(_v)}))\n"
            + "except BaseException as e:\n"
            + "    print(json.dumps({'ok':False,'e':type(e).__name__,'m':str(e)[:200]}))\n",
            encoding="utf-8",
        )
        r = subprocess.run([sys.executable, str(p)], capture_output=True,
                           text=True, timeout=60)
        txt = (r.stdout or "").strip().splitlines()
        if not txt:
            return {"ok": False, "e": "NoOutput", "m": (r.stderr or "")[-200:]}
        return json.loads(txt[-1])


def main():
    v254 = json.loads((ROOT / "bench/evidence/l18_s324/readings_v254.json")
                      .read_text(encoding="utf-8"))
    v258_ans = {r["task_id"]: r for r in load_jsonl("/tmp/v258_b/bench/answers.jsonl")
                if r.get("task_id")}
    suite = {r["id"]: r for r in load_jsonl("/tmp/v253_suite/low.jsonl")}

    from mw.verify import derive, example_asserts

    report = {"l1_code_identity": {}, "l2_gate": {}, "l3_divergence": {}}

    print("=" * 78)
    print("L1  was the info given? (sha + diff lines across three conditions)")
    for cell in CELLS:
        a = [x for x in v254["rows"] if x["cell"] == cell and x["arm"] == "A"][0]
        b = [x for x in v254["rows"] if x["cell"] == cell and x["arm"] == "B"][0]
        f = v258_ans[cell]
        ca, cb, cf = strip_fence(a["answer"]), strip_fence(b["answer"]), strip_fence(f["answer"])
        rec = {
            "sha_a": a["sha256"][:12], "sha_b": b["sha256"][:12],
            "sha_fresh": str(f["sha256"])[:12],
            "chars_a": len(ca), "chars_b": len(cb), "chars_fresh": len(cf),
            "B_eq_A": ca == cb,
            "B_eq_fresh": cb == cf,
            "diff_lines_B_vs_A": diff_lines(ca, cb),
            "diff_lines_B_vs_fresh": diff_lines(cb, cf),
        }
        report["l1_code_identity"][cell] = rec
        print(f"  {cell:20s} A={rec['sha_a']} B={rec['sha_b']} fresh={rec['sha_fresh']} "
              f"| B==A:{rec['B_eq_A']} B==fresh:{rec['B_eq_fresh']} "
              f"| d(B,A)={rec['diff_lines_B_vs_A']} d(B,fresh)={rec['diff_lines_B_vs_fresh']}")

    print("=" * 78)
    print("L2  what did the gate dispatch? (production _derive on the prompt)")
    for cell in CELLS:
        g = suite[cell]["prompt"]
        ch = derive(g, static=1, exec_=1)
        ex = example_asserts(g)
        names = [f"{c.name}<{c.kind}>" for c in ch]
        report["l2_gate"][cell] = {
            "checks": names,
            "n_example_asserts": len(ex),
            "example_asserts": list(ex),
            "prompt_has_eq": "==" in g,
        }
        print(f"  {cell:20s} checks={names} n_ex={len(ex)} prompt_has_eq={'==' in g}")
        for e in ex:
            print(f"      ex: {e[:100]}")

    print("=" * 78)
    print("L3  first divergence (actual vs expected)")
    probes = {
        "g7t2_parse_pairs": [
            ("parse_pairs('a=1;b=2')", "parse_pairs('a=1;b=2')"),
            ("parse_pairs(\"a=1;b='x;y'\")", "parse_pairs(\"a=1;b='x;y'\")"),
            ("parse_pairs('a=')", "parse_pairs('a=')"),
        ],
        "s02_limiter": [
            ("hits", "[L.hit('a',0), L.hit('a',1), L.hit('a',2), L.hit('a',3)]"),
            ("count_at_3", "L.count('a',3)"),
        ],
    }
    setup = {"s02_limiter": "L = Limiter(3, 10)"}
    expect = {
        "g7t2_parse_pairs": ["{'a': '1', 'b': '2'}", "{'a': '1', 'b': 'x;y'}", "{'a': ''}"],
        "s02_limiter": ["[True, True, True, False]", "3"],
    }
    for cell, items in probes.items():
        code = strip_fence((OUT / "answers_b" / f"{cell}.py").read_text(encoding="utf-8")) \
            if (OUT / "answers_b" / f"{cell}.py").exists() else \
            strip_fence(v258_ans[cell]["answer"])
        rows = []
        for i, (label, expr) in enumerate(items):
            got = run_code_get(code, expr, setup.get(cell, ""))
            want = expect[cell][i]
            rows.append({"label": label, "got": got, "want": want})
            print(f"  {cell:20s} {label:36s} got={got.get('v', got.get('e'))}  want={want}")
        report["l3_divergence"][cell] = rows

    (OUT / "diag_v259.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT / "diag_v259.json")


if __name__ == "__main__":
    main()

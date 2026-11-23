#!/usr/bin/env python3
"""系统完整度审计（V236 附属）：**只读**，不写任何产品文件、不改任何读数。

要回答的不是「能不能跑」，而是三类「声明与事实的差」：

A. **文档副本漂移** —— README / ASSESSMENT / site / quickstart 里的门禁数字
   是不是同一个数（同一个事实多份声明 ⇒ 迟早漂移）。
B. **开关使用率**（纪律 72/127）—— `run.py` 里默认 off 的开关有多少个，
   在真实证据日志里各被触发过几次。⛔ `na=0` 与「进了 if 被拒」同形，
   所以这里数的是**日志里出现过的显式标记**，不是「代码里有 if」。
C. **孤儿模块** —— `mw/*.py` 里没有被任何地方 import 的（声明了但没接线）。
D. **账本健康** —— `runs.jsonl` 行数、`score_ledger` 行数、
   以及「score 与 answer 不同源」的行（纪律 160 的实形）。
E. **立案未改清单** —— 从 DESIGN 里抓「立案」「未改」「不夹在本轮」的句子。

用法：python3 audit_v236.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
if not (ROOT / "run.py").exists():
    raise SystemExit(f"仓库根目录找不到: {ROOT}")

DOCS = ["README.md", "ASSESSMENT.md", "site/index.html", "demo/README.md", "quickstart.sh"]


def norm_int(s: str) -> str:
    return s.replace(",", "").replace(" ", "")


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def audit_docs() -> dict:
    """A：同一批门禁数字在各文档里的出现。"""
    pats = {"contract": r"契约[^\d]{0,12}(\d{2,4})\s*/\s*(\d+)",
            "anchor": r"锚层[^\d]{0,12}(\d{2,4})\s*/\s*(\d+)",
            "withdraw": r"撤回态[^\d]{0,12}(\d{2,4})"}
    found: dict[str, dict[str, list[str]]] = {}
    for p in DOCS:
        t = read(ROOT / p)
        if not t:
            continue
        row: dict[str, list[str]] = {}
        for k, pat in pats.items():
            vals = sorted({m.group(1) + ("/" + m.group(2) if m.lastindex == 2 else "")
                           for m in re.finditer(pat, t)})
            if vals:
                row[k] = vals
        if row:
            found[p] = row
    # 一致性：每个 key 在所有文档里应只有一个取值
    drift = {}
    for k in pats:
        vals = {v for row in found.values() for v in row.get(k, [])}
        if len(vals) > 1:
            drift[k] = sorted(vals)
    return {"per_doc": found, "drift": drift}


def audit_switches() -> dict:
    """B：默认 off 的开关 × 证据日志里的显式触发次数。"""
    src = read(ROOT / "run.py")
    # 抓 `p.add_argument("--x", ... default=False/0/"" ...)`
    out = []
    for m in re.finditer(r'p\.add_argument\(\s*"(--[a-z0-9\-]+)"(.*?)\)\n', src, re.S):
        flag, rest = m.group(1), m.group(2)
        if "action=" in rest and "store_true" in rest:
            out.append(flag)
        elif re.search(r'default\s*=\s*(False|0|""|\'\')', rest):
            out.append(flag)
    logs = list((ROOT / "bench" / "evidence").rglob("*.log"))
    blobs = []
    for p in logs:
        try:
            blobs.append(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    joined = "\n".join(blobs)
    rows = []
    for f in out:
        # 日志里一般打印开关名（含 `--`）或其去掉前缀的自定义标记
        bare = f.lstrip("-").replace("-", "_")
        rows.append({"flag": f,
                     "in_logs": joined.count(f),
                     "bare_in_logs": joined.count(bare),
                     "logs_scanned": len(logs)})
    return {"n_off_switches": len(out), "rows": rows}


def audit_orphans() -> dict:
    """C：`mw/*.py` 有没有被 import。"""
    mw = ROOT / "mw"
    mods = sorted(p.stem for p in mw.glob("*.py")
                  if p.stem not in ("__init__",))
    hay = []
    for p in list(ROOT.glob("*.py")) + list(mw.glob("*.py")) + \
            list((ROOT / "tests").glob("*.py")) + list((ROOT / "bench").glob("*.py")):
        hay.append(p.name + "\n" + read(p))
    joined = "\n".join(hay)
    rows = []
    for m in mods:
        # 出现次数（含 `from .m import` / `import mw.m`）
        c = len(re.findall(rf"\b{re.escape(m)}\b", joined))
        rows.append({"module": m, "refs": c})
    init = read(mw / "__init__.py")
    core_list = sorted(re.findall(r'"([a-z_]+)"', init))
    return {"modules": rows,
            "n_modules": len(mods),
            "in_init_list": core_list}


def audit_ledgers() -> dict:
    p = ROOT / "bench_out" / "runs.jsonl"
    rows = 0
    if p.exists():
        with p.open(encoding="utf-8") as f:
            rows = sum(1 for _ in f)
    sl = ROOT / "state" / "score_ledger.jsonl"
    sl_rows = 0
    mism = []
    if sl.exists():
        with sl.open(encoding="utf-8") as f:
            for ln in f:
                sl_rows += 1
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                # 「score 与 answer 不同源」的嫌疑：同 task 里 answer 非空但 score 与
                # 该 answer 的 vec 均值对不上 —— 记账粒度只到 task，不做逐题复算，
                # 只报**键缺失**这种结构性缺口。
                if d.get("answer") and "vec" not in d:
                    mism.append(d.get("task_id"))
    return {"runs_jsonl_rows": rows, "score_ledger_rows": sl_rows,
            "ledger_rows_with_answer_but_no_vec": len(mism),
            "examples": mism[:5]}


def audit_open_issues() -> dict:
    d = read(ROOT / "DESIGN.md")
    keys = ["立案", "未改", "不夹在本轮", "本轮不改", "只立案", "TODO", "⛔ 未修"]
    hits = []
    for i, ln in enumerate(d.splitlines(), 1):
        if any(k in ln for k in keys):
            hits.append({"line": i, "text": ln.strip()[:160]})
    return {"n_hits": len(hits), "hits": hits[-25:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    rep = {"root": str(ROOT),
           "docs": audit_docs(),
           "switches": audit_switches(),
           "orphans": audit_orphans(),
           "ledgers": audit_ledgers(),
           "open_issues": audit_open_issues()}

    print("=" * 72)
    print("A. 文档副本一致性")
    for p, row in rep["docs"]["per_doc"].items():
        print(f"  {p:22s} {row}")
    print(f"  漂移: {rep['docs']['drift'] or '无'}")
    print()
    print("B. 默认 off 的开关（证据日志里的触发次数）")
    sw = rep["switches"]
    print(f"  共 {sw['n_off_switches']} 个；扫描 {sw['rows'][0]['logs_scanned'] if sw['rows'] else 0} 份日志")
    zero = [r["flag"] for r in sw["rows"] if r["in_logs"] == 0 and r["bare_in_logs"] == 0]
    used = [r for r in sw["rows"] if r["in_logs"] or r["bare_in_logs"]]
    print(f"  日志里出现过: {len(used)}/{sw['n_off_switches']}")
    for r in sorted(used, key=lambda x: -(x["in_logs"] + x["bare_in_logs"]))[:12]:
        print(f"    {r['flag']:32s} {r['in_logs']:4d} / bare {r['bare_in_logs']:4d}")
    print(f"  从未出现: {len(zero)} 个 → {zero}")
    print()
    print("C. 孤儿模块（引用次数为 0 或只在 `__init__` 的清单里）")
    for r in rep["orphans"]["modules"]:
        mark = "  ⚠️" if r["refs"] <= 1 else ""
        print(f"    {r['module']:22s} refs={r['refs']}{mark}")
    print(f"  mw 模块数={rep['orphans']['n_modules']}  清单={rep['orphans']['in_init_list']}")
    print()
    print("D. 账本健康")
    print(f"  runs.jsonl={rep['ledgers']['runs_jsonl_rows']} 行；"
          f"score_ledger={rep['ledgers']['score_ledger_rows']} 行")
    print(f"  有 answer 但无 vec 的行: {rep['ledgers']['ledger_rows_with_answer_but_no_vec']}"
          f"  例: {rep['ledgers']['examples']}")
    print()
    print("E. 立案未改（最近 25 条）")
    for h in rep["open_issues"]["hits"]:
        print(f"    L{h['line']}: {h['text']}")

    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"\nJSON → {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""V246 读数汇总：自验回路的命中、自锁、以及「改了但没对」的实物。"""
import collections
import difflib
import json
from pathlib import Path

D = Path(__file__).resolve().parent
rows = [json.loads(x) for x in
        (D / "readings_v246.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
start = (D.parent / "l9_s310/cand/90579ecebe633352.py").read_text(encoding="utf-8")

print(f"n = {len(rows)}")
print("首轮分布", collections.Counter(r["scores"][0] for r in rows if r["scores"]))
print("最终分布", collections.Counter(r["final"] for r in rows))
print("轮数分布", collections.Counter(r["rounds"] for r in rows))
print("自锁", sum(1 for r in rows if r["stalled"]), "/", len(rows))
print()
print("主判据：D 臂最终 6/6 ≥ 3 ⇒",
      "成立" if sum(1 for r in rows if r["final"] == 6) >= 3 else "不成立",
      f"（实测 {sum(1 for r in rows if r['final'] == 6)}）")
print("P2：首轮 ≤ 1 ⇒",
      "成立" if sum(1 for r in rows if r["scores"] and r["scores"][0] == 6) <= 1 else "不成立")
print()

print("=== 真正迭代过的条目（轮数≥2 且逐轮不是同一个分） ===")
for r in rows:
    if r["rounds"] < 2:
        continue
    print(f"D#{r['i']:02d} 逐轮={r['scores']} 自锁={r['stalled']} sha={r['sha']}")
    d = list(difflib.unified_diff(start.splitlines(), r["code"].splitlines(),
                                  "start", "final", n=0, lineterm=""))
    print("  " + "\n  ".join(d[:14]) if d else "  （与起点逐字节同）")
    print()

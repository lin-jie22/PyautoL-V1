"""V245 读数汇总：三臂的 6/6 命中、照抄率、相似度。

⛔ 只读 `readings_v245.jsonl`，不重跑、不改判据。
⚠️ 本脚本里的 `守卫` 一列作废：`run_v245.py` 的正则把原代码自带的
   `if len(parts[1]) == 0:` 也匹配了（原代码本来就有这个判空，只是在错误的
   分支上）。⇒ 该列恒为 True 时**不代表改对了位置**。判「改没改」只用
   `same_as_start` 与 `sim`，这两列是字节级的。
"""
import collections
import json
import statistics
from pathlib import Path

D = Path(__file__).resolve().parent
rows = [json.loads(x) for x in
        (D / "readings_v245.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]

head = "臂  n   6/6  分布(0..6)                       照抄  sim均  tok均"
print(head)
print("-" * len(head))
for arm in ("A", "B", "C"):
    rs = [r for r in rows if r["arm"] == arm]
    sc = [r.get("score", 0) for r in rs]
    dist = collections.Counter(sc)
    d = "{" + ", ".join(f"{k}:{dist[k]}" for k in sorted(dist)) + "}"
    print(f"{arm:2s} {len(rs):3d} {dist.get(6, 0):4d}  {d:<32s} "
          f"{sum(1 for r in rs if r.get('same_as_start')):4d}  "
          f"{statistics.mean(r['sim'] for r in rs):.3f}  "
          f"{statistics.mean(r.get('tokens') or 0 for r in rs):5.0f}")

print()
for arm in ("A", "B", "C"):
    rs = [r for r in rows if r["arm"] == arm]
    uniq = {r.get("sha") for r in rs}
    print(f"{arm} 臂：唯一产出 sha {len(uniq)}/{len(rs)}，"
          f"非照抄 {sum(1 for r in rs if not r.get('same_as_start'))} 条，"
          f"6/6 {sum(1 for r in rs if r.get('score') == 6)} 条")

print()
print("主判据：B 臂 6/6 命中数 ≥ 3（n=16） ⇒ 执行轨迹有牙")
b66 = sum(1 for r in rows if r["arm"] == "B" and r.get("score") == 6)
print(f"  实测 B 臂 6/6 = {b66} ⇒ {'成立' if b66 >= 3 else '不成立'}")

print()
print("P1：A 臂 6/6 ≤ 1 ⇒",
      "成立" if sum(1 for r in rows if r["arm"] == "A" and r.get("score") == 6) <= 1
      else "不成立")
print("P3：C 臂 6/6 ≥ 3 ⇒",
      "成立" if sum(1 for r in rows if r["arm"] == "C" and r.get("score") == 6) >= 3
      else "不成立")
print()
print("失败/空产出：", sum(1 for r in rows if r.get("err")))
print("全部产出都是 5/6：", all(r.get("score") == 5 for r in rows))

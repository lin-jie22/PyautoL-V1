"""V253 Stage A（纯离线）：把已有 T=0 / qwen2.5:7b 读数按格汇总。

只答一个问题，逐格报三个数：读数次数 n、最高分 best、最低分 min。
⚠️ 纪律 251（V252 新增）：不记口径的行不进分布统计 —— 本脚本只收
`report.provenance.model=='qwen2.5:7b'` 且 `temperature==0.0` 的行。

输出 stdout + `existing_t0_v253.json`
"""
import json
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
RUNS = ROOT / "bench_out" / "runs.jsonl"
OUT = ROOT / "bench/evidence/l17_s323" / "existing_t0_v253.json"
MODEL, TEMP = "qwen2.5:7b", 0.0


def main():
    per = {}
    for line in open(RUNS, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        rep = d.get("report") or {}
        p = rep.get("provenance") or {}
        if p.get("model") != MODEL or p.get("temperature") != TEMP:
            continue
        for t in rep.get("per_task") or []:
            cid, sc = t.get("id"), t.get("score")
            if not cid or not isinstance(sc, (int, float)):
                continue
            e = per.setdefault(cid, {"n": 0, "best": -1.0, "min": 2.0, "scores": []})
            e["n"] += 1
            e["best"] = max(e["best"], float(sc))
            e["min"] = min(e["min"], float(sc))
            e["scores"].append(round(float(sc), 4))

    # 目标 39 格（spec-inline）
    task_dir = ROOT / "bench" / "tasks"
    target = {}
    for p in sorted(task_dir.glob("*.jsonl")):
        if p.name == "g7d_spec_incomplete.jsonl":
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line:
                r = json.loads(line)
                target[r["id"]] = p.stem

    print(f"口径 model={MODEL} T={TEMP} | 已有读数的格={len(per)} | 目标={len(target)}\n")
    print(f"{'cell':<26}{'suite':<18}{'n':>3}{'best':>7}{'min':>7}  scores")
    low, full, unmeasured = [], [], []
    for cid in sorted(target):
        e = per.get(cid)
        if not e:
            unmeasured.append(cid)
            print(f"{cid:<26}{target[cid]:<18}{0:>3}{'-':>7}{'-':>7}  （未测）")
            continue
        print(f"{cid:<26}{target[cid]:<18}{e['n']:>3}{e['best']:>7.3f}{e['min']:>7.3f}  {e['scores']}")
        (low if e["best"] < 1.0 else full).append(cid)

    print(f"\n<1.0 的格 {len(low)}：{low}")
    print(f"=1.0 的格 {len(full)}：{full}")
    print(f"未测的格 {len(unmeasured)}：{unmeasured}")
    print(f"\n⇒ 已有可用的空洞格 {len(low)} 个；若 ≥3 则不必扫全部 39 题")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"caliber": {"model": MODEL, "temperature": TEMP},
                   "per_cell": per, "low": low, "full": full,
                   "unmeasured": unmeasured}, fh, ensure_ascii=False, indent=2)
    print(f"落盘 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

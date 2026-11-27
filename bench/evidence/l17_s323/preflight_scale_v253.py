"""V253 preflight（纯离线，0 模型）：39 题 spec-inline 的尺子校准。

`--bench` 的 `calibrate_or_raise` 是 fail-closed：逐条要求
`score(oracle) > score(null)` 且（有 partial 时）`null < partial < oracle` 严格。
任何一条不过 ⇒ 整轮 CalibrationError ⇒ 零读数。

所以先离线跑一遍，报出**哪一条**不过，以及 margin 分布。
另外顺便建 39 题合并套件（bench 一次跑完，避免 8 次进程各自校准）。

输出：`/tmp/v253_suite/all39.jsonl` + `preflight_scale_v253.json`
"""
import json
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench" / "scorers"))

OUTDIR = ROOT / "bench/evidence/l17_s323"
SKIP = {"g7d_spec_incomplete.jsonl"}
ALL39 = Path("/tmp/v253_suite/all39.jsonl")


def main():
    from mw.bench import calibrate, ExternalScorer, Suite  # noqa: E402

    scorer_path = ROOT / "bench" / "scorers" / "code_exec.py"
    print(f"scorer={scorer_path}")

    tasks, per_suite = [], {}
    for p in sorted((ROOT / "bench" / "tasks").glob("*.jsonl")):
        if p.name in SKIP:
            continue
        rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        per_suite[p.stem] = len(rows)
        tasks.extend(rows)
    print(f"套件 {len(per_suite)} 个 / 题 {len(tasks)}：{per_suite}")

    ALL39.parent.mkdir(parents=True, exist_ok=True)
    with open(ALL39, "w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"合并套件落盘 {ALL39}")

    # ⛔ 不动文件模式：本轮是只读审计，`chmod +x` 会让 `git status` 出现无意义的 mode 变更

    sc = ExternalScorer([sys.executable, str(scorer_path)], source=scorer_path)
    suite = Suite.load(ALL39)

    cal = calibrate(sc, suite, min_middle=1)
    print(f"\ncalibrate ok={cal.ok} reason={cal.reason}")
    print(cal.as_lines()[:4000])

    # 逐条 margin（oracle - null）
    rows = []
    bad = []
    for t in suite.tasks:
        so = sc.score(t.as_dict(), t.oracle).score
        sn = sc.score(t.as_dict(), t.null).score
        sp = sc.score(t.as_dict(), t.partial).score if t.partial.strip() else None
        rows.append({"id": t.id, "oracle": so, "null": sn, "partial": sp,
                     "margin": round(so - sn, 4),
                     "mid_strict": (sn < sp < so) if sp is not None else None})
        if not (so > sn) or (sp is not None and not (sn < sp < so)):
            bad.append(t.id)

    rows.sort(key=lambda r: r["margin"])
    print(f"\n{'cell':<26}{'oracle':>8}{'null':>7}{'partial':>9}{'margin':>8}  mid")
    for r in rows:
        print(f"{r['id']:<26}{r['oracle']:>8.3f}{r['null']:>7.3f}"
              f"{(r['partial'] if r['partial'] is not None else -1):>9.3f}"
              f"{r['margin']:>8.3f}  {r['mid_strict']}")
    print(f"\n校准不过的题 {len(bad)}：{bad}")

    res = {"n": len(rows), "cal_ok": cal.ok, "reason": cal.reason,
           "bad": bad, "rows": rows, "suite_file": str(ALL39)}
    (OUTDIR / "preflight_scale_v253.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"落盘 {OUTDIR/'preflight_scale_v253.json'}")
    return 0 if cal.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

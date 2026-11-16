"""V171 STAGE0b —— 量「RAISE 在真装置上存在吗」（纪律 72：先证条件被触到过）。

⛔ 不是零判分探针：它真跑 scorer。但它量的不是判据，是**前提**：
   40 道题里有多少道 mean<1.0 —— 如果 0 道，那 RAISE 就是死机制，
   V171 只能报「可达性 0」，不许拿构造数据冒充部署有效。

⚠️ `--bench --offline` 是 fail-closed（llm_of_record=unknown ⇒ 拒绝出分），
   所以这里走 V170 覆盖跑同一条路：Suite.load → calibrate → run_suite → per_task。
   两者用的是同一个 scorer、同一个 core，区别只是不经过「写基线」那道闸。
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

import mw.goals as G                       # noqa: E402
from mw.bench import ExternalScorer, Suite, calibrate, run_suite   # noqa: E402

TASK_DIR = ROOT / "bench" / "tasks"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "readings_scores.json"))
    a = ap.parse_args()

    args = argparse.Namespace(
        offline=True, memory_mode="raw", allow_shell=False, no_external=True,
        solver="auto", repeat=1, goal="",
    )
    sys.argv = [sys.argv[0]]        # `_build` 之外还有人读 argv 的话，别被 harness 参数干扰
    import run as R                          # noqa: E402

    scorer = ExternalScorer([sys.executable, str(SCORER)], source=SCORER)
    core = R._build("", args, allow_shell=False)

    scores: dict[str, float] = {}
    per_suite: dict[str, dict] = {}
    for p in sorted(TASK_DIR.glob("*.jsonl")):
        suite = Suite.load(p)
        cal = calibrate(scorer, suite)
        if not cal.ok:
            print(f"⛔ {p.stem}: 尺子自证失败 ⇒ 不测（fail-closed）")
            per_suite[p.stem] = {"ok": False}
            continue
        _m, rep = run_suite(lambda t: core.run(t.prompt, task_id=t.id, family=p.stem).answer,
                            scorer, suite, repeat_n=1, budget_tag="1x",
                            candidate="v171-stage0")
        got = 0
        for r in rep.per_task:
            if r.get("id") is not None:
                scores[str(r["id"])] = float(r["score"])
                got += 1
        per_suite[p.stem] = {"ok": True, "n": len(suite.tasks), "scored": got,
                             "errors": rep.errors[:2]}
        print(f"  {p.stem:<24} n={len(suite.tasks):<3} 出分 {got:<3} "
              f"mean={G._mean([scores[str(t.id)] for t in suite.tasks if str(t.id) in scores]) if got else 0:.4f}")

    ids = sorted(scores)
    m = G._mean([scores[i] for i in ids]) if ids else 0.0
    below = [i for i in ids if scores[i] < 1.0]
    print(f"\n## 40 题分数")
    print(f"  出分 {len(ids)} 题｜总 mean {m:.4f}｜**mean<1.0 的 {len(below)} 题**")
    for i in ids:
        flag = "  ← RAISE 种子" if scores[i] < 1.0 else ""
        print(f"  {i:<30} {scores[i]:.4f}{flag}")

    # 分档：RAISE 需要 n_obs>=3 且 mean<1.0
    out = {"scores": scores, "per_suite": per_suite,
           "n_scored": len(ids), "n_below_1": len(below), "below": below,
           "mean": m}
    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    print(f"\n落盘 {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

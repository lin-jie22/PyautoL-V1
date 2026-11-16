"""V171 STAGE0c —— 活性对照：**不启用 ESCALATE 时，同一格会被重复提几次**。

为什么这一步不能省（纪律 88）：如果「重复」本来就不发生，
那 V171 的「归零」就是把一个从没发生过的事做成从没发生过 —— 假绿。

做法：单题套件（`t06_rle`，离线桩上恒 0.0）连续跑 5 轮 `--goals-act 1`，
每轮读 proposer 的输出，数「同一格上出现的目标条数与类型」。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

CELL = "t06_rle"
ROUNDS = 8


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="v171_loop_"))
    sd = tmp / "tasks"
    sd.mkdir()
    src = ROOT / "bench" / "tasks" / "coding_small.jsonl"
    line = next(l for l in src.read_text(encoding="utf-8").splitlines()
                if json.loads(l)["id"] == CELL)
    (sd / "one.jsonl").write_text(line + "\n", encoding="utf-8")
    # 空白账本起跑：这一格从 0 次观测开始
    (tmp / "score_ledger.jsonl").write_text("", encoding="utf-8")

    env = dict(os.environ, V1_STATE_DIR=str(tmp))
    print("=" * 74)
    print(f"V171 两臂对照 —— {CELL} 连跑 {ROUNDS} 轮（tag 见文件名）")
    print("=" * 74)
    rows = []
    for i in range(ROUNDS):
        cmd = [sys.executable, str(ROOT / "run.py"), "--goals", "--goals-act", "1",
               "--offline", "--goals-suite-dir", str(sd),
               "--goals-json", str(tmp / f"g{i}.json"),
               "--goals-log", str(tmp / "goal_ledger.jsonl"),
               "--goals-obs", str(tmp / "goal_obs.jsonl")]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
        if r.returncode != 0:
            print(f"  第 {i+1} 轮 失败 rc={r.returncode}\n{r.stderr[-500:]}")
            return 1
        d = json.loads((tmp / f"g{i}.json").read_text(encoding="utf-8"))
        before = d.get("goals") or []
        acted = d.get("acted") or []
        after = d.get("reproposed") or []
        a = acted[0] if acted else {}
        # 「同一格的 RAISE 提议数」= 执行后重新提议里该格的 RAISE 条数
        n_raise_after = sum(1 for g in after if g.get("cell") == CELL and g.get("kind") == "RAISE")
        rows.append({"round": i + 1,
                     "kinds_before": [g["kind"] for g in before],
                     "acted_kind": a.get("kind"), "verdict": a.get("verdict"),
                     "n_obs": (a.get("reading") or {}).get("n_obs"),
                     "mean": round((a.get("reading") or {}).get("mean_score", 0.0), 6),
                     "kinds_after": [g["kind"] for g in after],
                     "n_raise_after": n_raise_after})
        rr = rows[-1]
        print(f"  第 {i+1} 轮：提议 {rr['kinds_before']} ⇒ 执行 {rr['acted_kind']} "
              f"({rr['verdict']}, n={rr['n_obs']}, mean={rr['mean']:.4f})"
              f"｜之后提议 {rr['kinds_after']}｜**该格 RAISE {rr['n_raise_after']} 条**")

    n_raise_total = sum(r["n_raise_after"] for r in rows)
    means = {r["mean"] for r in rows if r["mean"] is not None}
    print(f"\n  ⭐ {ROUNDS} 轮里该格 RAISE 被提出 **{n_raise_total}** 次")
    print(f"  ⭐ {ROUNDS} 轮读数的 mean 取值集合：{sorted(means)}"
          f"（{len(means)} 个不同值 ⇒ {'逐位相同' if len(means) == 1 else '会变'}）")

    tag = os.environ.get("V171_TAG", "arm")
    out = {"cell": CELL, "rounds": ROUNDS, "arm": tag,
           "rows": rows, "n_raise_total": n_raise_total,
           "distinct_means": sorted(means),
           "tmp": str(tmp)}
    (HERE / f"readings_loop_{tag}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  落盘 {HERE.name}/readings_loop_{tag}.json（临时目录 {tmp}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

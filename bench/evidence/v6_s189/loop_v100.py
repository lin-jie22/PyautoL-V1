#!/usr/bin/env python3
"""V100：**闭环搜索** —— 让脚本自己枚举、自己打分、自己写回。

V97~V99 已经证明：单字符扰动撞到满分盆约 1/5 次，盲目取一个为负、取最优为正。
但那三轮的枚举都是**人**做的。本轮把它交给脚本，跑成一条**轨迹**：

    状态（4 条 ZK 事实）→ 机械生成 K=4 个单字符扰动 → 各自跑一遍 →
    用 4 题总分选最好的 → **写回状态**（只在严格变好时）→ 下一轮

⛔ 扰动**不看结果**：候选表 = 「全部全角标点 → ASCII 对应」按 (事实, 位置) 排序后的固定序列，
指针每轮前进 4 格。**没有挑过、也没有回头。**

用法：python3 bench/evidence/v6_s189/loop_v100.py <root> [轮数]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from mw.memory import MemoryStore  # noqa: E402

SUITE = "bench/heldout/g7j_n4_heldout.jsonl"
TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]

BASE = {
    "ZK1": "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "ZK2": "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "ZK3": "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "ZK4": "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
}
ORDER = ("ZK1", "ZK2", "ZK3", "ZK4")

#: 全角 → ASCII，**固定映射表**
MAP = {"（": "(", "）": ")", "：": ":", "，": ",", "、": ",", "。": "."}


def candidate_list() -> list[tuple[str, int, str, str]]:
    """全部候选：按 (事实, 位置) 排序，返回 [(tag, idx, old, new), ...]。位置在 BASE 上算。"""
    out = []
    for tag in ORDER:
        for i, ch in enumerate(BASE[tag]):
            if ch in MAP:
                out.append((tag, i, ch, MAP[ch]))
    return out


def state_facts(edits: list[tuple[str, int, str, str]]) -> dict[str, str]:
    body = dict(BASE)
    for tag, i, old, new in edits:
        assert body[tag][i] == old, f"状态不一致：{tag}[{i}] 是 {body[tag][i]!r}，期望 {old!r}"
        body[tag] = body[tag][:i] + new + body[tag][i + 1:]
    return body


def write_seed(path: Path, body: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    db = path / "memory.db"
    if db.exists():
        db.unlink()
    m = MemoryStore(db)
    for tag in ORDER:
        m.commit_fact(body[tag], source="abstract", confidence=0.6)
    m.close()


def run_one(tag: str, seed: Path, d: Path) -> dict:
    if (d / "bench" / "runs.jsonl").exists():
        return read_run(d)
    d.mkdir(parents=True, exist_ok=True)
    for sub in ("log", "bench", "mem"):
        (d / sub).mkdir(exist_ok=True)
    (d / "mem" / "memory.db").write_bytes((seed / "memory.db").read_bytes())
    env = dict(os.environ)
    env.update({
        "V1_WORKSPACE": str(d / "ws"), "V1_LOG_DIR": str(d / "log"),
        "V1_BENCH_DIR": str(d / "bench"),
    })
    cmd = [sys.executable, "-u", "run.py", "--bench", "--suite", SUITE,
           "--temperature", "0", "--repeat", "1", "--no-external", "--no-ledger",
           "--allow-shell", "--memory-dir", str(d / "mem"), "--use-reward",
           "--state-dump", str(d / "state_dump.jsonl")]
    with open(d / "out.txt", "w") as fh:
        rc = subprocess.call(cmd, cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT)
    (d / "rc.txt").write_text(str(rc))
    if rc != 0:
        raise SystemExit(f"⛔ {tag} 退出码 {rc} ⇒ 停（不猜、不续）")
    return read_run(d)


def read_run(d: Path) -> dict:
    rec = json.loads((d / "bench" / "runs.jsonl").read_text().splitlines()[0])
    per = (rec.get("report") or {}).get("per_task") or []
    sc = {t["id"]: t.get("score") for t in per}
    mean = round(sum(v for v in sc.values() if v is not None) / len(TASKS), 4)
    return {"score": sc, "mean": mean}


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v100")
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    K = 4
    root.mkdir(parents=True, exist_ok=True)
    cands = candidate_list()
    (root / "candidates.txt").write_text(
        "\n".join(f"{i:2d} {t} 位置{i3:2d} {o}→{n}" for i, (t, i3, o, n) in enumerate(cands)) + "\n",
        encoding="utf-8")

    edits: list[tuple[str, int, str, str]] = []
    ptr = 0
    traj = []

    # 第 0 步：基线
    write_seed(root / "seeds" / "r0", state_facts(edits))
    cur = run_one("r0", root / "seeds" / "r0", root / "r0")
    traj.append({"round": 0, "tag": "r0", "edits": [], "mean": cur["mean"], "score": cur["score"],
                 "accepted": True})
    print(f"[r0] 基线 mean={cur['mean']:.4f}  {cur['score']}")

    for r in range(1, rounds + 1):
        batch = []
        while len(batch) < K and ptr < len(cands):
            c = cands[ptr]
            ptr += 1
            # 跳过已被本轮状态改过的位置（同一位置只改一次）
            if any(e[0] == c[0] and e[1] == c[1] for e in edits):
                continue
            batch.append(c)
        if not batch:
            print(f"[r{r}] 候选耗尽 ⇒ 停")
            break
        results = []
        for j, c in enumerate(batch):
            trial = edits + [c]
            tag = f"r{r}_{j}"
            write_seed(root / "seeds" / tag, state_facts(trial))
            got = run_one(tag, root / "seeds" / tag, root / tag)
            results.append({"tag": tag, "edit": c, "mean": got["mean"], "score": got["score"]})
            print(f"[r{r}] {tag} {c[0]}#{c[1]} {c[2]}→{c[3]}  mean={got['mean']:.4f}  {got['score']}")
        best = max(results, key=lambda x: x["mean"])
        took = best["mean"] > cur["mean"]
        if took:
            edits = edits + [best["edit"]]
            cur = {"mean": best["mean"], "score": best["score"]}
        traj.append({"round": r, "candidates": results, "picked": best["tag"],
                     "accepted": took, "mean": cur["mean"], "score": cur["score"],
                     "edits": [[t, i, o, n] for t, i, o, n in edits]})
        print(f"[r{r}] ⇒ 选 {best['tag']}（{best['mean']:.4f}）｜"
              f"{'**变好，写回**' if took else '不变好，保持'}｜当前 mean={cur['mean']:.4f}")

    (root / "trajectory.json").write_text(json.dumps(traj, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n轨迹：", " → ".join(f"{t['mean']:.2f}" + ("*" if t["accepted"] else "") for t in traj))
    print("最终状态改动：", edits if edits else "（无）")


if __name__ == "__main__":
    main()

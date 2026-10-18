#!/usr/bin/env python3
"""§17-7 三臂实验的**种子构建器**（DESIGN §17-7 ★7a 事前登记 + 装置修正④）。

与 6c（bench/build_6c_seed.py）的三点差别：
  1. 训练相 = **真模型自然失败史**（z01–z05 真实会话），不是桩 —— 登记写明
     「自然失败史」是本题的被测对象；V1_ALLOW_NOTE=0 + V1_ALLOW_LEARN=0
     隔离出「蒸馏」这一条通道（失配仍记情景，不进语义层）。
  2. H 臂种子**只含蒸馏 facts**（source=abstract），不含原始失败 episode
     —— 6c 的 H 臂混着 episode，本题把处理隔离成纯抽象知识。
  3. 训练一条失败都没有 ⇒ distill 无原料 ⇒ rc=1 整组作废（不许换题凑）。

产物：memH/ memP/ memE/（+ seed_frozen 快照由 run_g7.sh 落地）。
R1 送达预检：heldout 每题 goal 走同一条 MemoryStore.recall() ——
    H ≥1 / P ≥1 / E =0，否则 rc=2（处理未送达，跑臂没有意义）。

用法：
    python3 bench/build_g7_seed.py --out-dir /tmp/v1_g7/seed \
        --train-suite bench/tasks/struct_convert.jsonl \
        --heldout bench/heldout/g7_heldout.jsonl
（训练与蒸馏都需要真模型环境变量：V1_LLM_BASE_URL / V1_LLM_API_KEY / V1_LLM_MODEL）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mw import config as cfg                                   # noqa: E402
from mw.abstractor import ABSTRACT_SOURCE, distill             # noqa: E402
from build_6c_seed import (                                   # noqa: E402
    _PLACEBO_POOL, _all_facts, _build_placebo)                # 等长原料池/实现的唯一事实源


def _run_training(train_suite: Path, mem_dir: Path) -> dict:
    """真模型内核跑训练套件（自然失败史）。读数不进判定 —— 只产失败 episode。"""
    import os
    import subprocess

    env = dict(os.environ)
    env.update(
        NO_PROXY="127.0.0.1", no_proxy="127.0.0.1",
        V1_ALLOW_NOTE="0", V1_ALLOW_LEARN="0",
    )
    cmd = [
        sys.executable, str(_ROOT / "run.py"),
        "--bench", "--solver", "llm", "--temperature", "0",
        # 装置修正⑤（DESIGN §17-7 ★7a）：训练相 budget 2x + max_tokens 2048 ——
        # 1x/1024 下重试链吃光墙钟、会话全走收尾、verify 一次不跑 ⇒ 零失败史。
        # 只动训练相；判定三臂仍 1x/1024（与校准同口径）。
        "--max-tokens", "2048", "--budget", "2", "--no-external",
        # 装置修正⑥（DESIGN §17-7 ★7a）：加 --use-reward —— 语义性答错不产生
        # failure episode（verify 只查四项浅判据），自然失败史只能由 §17-5 的
        # 外部奖励只读通道写回（kind=reward 的 episode：得分 + 没过的 assert 原文）。
        "--use-reward",
        "--suite", str(train_suite),
        "--memory-dir", str(mem_dir),
    ]
    r = subprocess.run(cmd, cwd=_ROOT, env=env,
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit(f"❌ 训练会话 rc={r.returncode}")
    return {"rc": r.returncode}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out-dir", required=True)
    p.add_argument("--train-suite", required=True)
    p.add_argument("--heldout", required=True)
    args = p.parse_args(argv)

    out = Path(args.out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    heldout = [json.loads(l) for l in
               Path(args.heldout).read_text().splitlines() if l.strip()]

    # ── 训练相：真模型自然失败史 ──────────────────────────────────────────
    train_dir = out / "memTrain"
    print("== 训练相：真模型内核跑训练套件（自然失败史）==")
    _run_training(Path(args.train_suite), train_dir)

    memTrain_mem = train_dir / "memory.db" if (train_dir / "memory.db").exists() \
        else train_dir
    from mw.memory import MemoryStore
    memTrain = MemoryStore(memTrain_mem)
    stats0 = memTrain.stats()
    print(f"  训练后 episodes={stats0['episodes']} facts={stats0['facts']}")

    fails = memTrain.conn.execute(
        "SELECT COUNT(*) c FROM episodes WHERE kind IN ('failure','reward')"
    ).fetchone()["c"]
    n_reward = memTrain.conn.execute(
        "SELECT COUNT(*) c FROM episodes WHERE kind='reward'").fetchone()["c"]
    n_fail = memTrain.conn.execute(
        "SELECT COUNT(*) c FROM episodes WHERE kind='failure'").fetchone()["c"]
    print(f"  失败史 episode：failure={n_fail} reward={n_reward}")
    if fails == 0:
        print("❌ 训练会话零失败史 ⇒ 蒸馏无原料 ⇒ 整组作废（登记：不许换题凑）")
        return 1

    # ── 蒸馏（真模型）────────────────────────────────────────────────────
    print("== 真模型蒸馏 ==")
    s = cfg.settings()
    from mw.llm import build_llm
    st = distill(memTrain, build_llm(s.llm), max_tokens=160)
    print(f"  {st.as_line()}")
    h_texts = [r.text for r in _all_facts(memTrain)
               if r.source == ABSTRACT_SOURCE]
    for t in h_texts:
        print(f"  · (len={len(t)}) {t}")
    if not h_texts:
        print("❌ 蒸馏一条都没产出 ⇒ H 臂为空，整组作废（不许手补）")
        return 1

    # ── H：只含蒸馏 facts 的干净库（隔离掉 episode）──────────────────────
    memH = MemoryStore(out / "memH" / "memory.db")
    for t in h_texts:
        memH.commit_fact(t, source=ABSTRACT_SOURCE, confidence=0.5)
    stH = memH.stats()
    print(f"== H 臂：纯 facts 库 episodes={stH['episodes']} facts={stH['facts']} ==")
    if stH["episodes"] != 0:
        print("❌ H 臂混入了 episode ⇒ 处理不纯，整组作废")
        return 1

    # ── P：逐条等长安慰剂（与 6c 同一实现，差一字即抛）────────────────────
    memP = MemoryStore(out / "memP" / "memory.db")
    print("== P 臂：等长安慰剂 ==")
    fake = []
    for t in h_texts:
        fake.append(type("F", (), {"text": t})())
    for txt in _build_placebo(fake):
        memP.commit_fact(txt, source=ABSTRACT_SOURCE, confidence=0.5)
        print(f"  · (len={len(txt)}) {txt}")

    # ── E：空表 ──────────────────────────────────────────────────────────
    MemoryStore(out / "memE" / "memory.db").close()
    print("== E 臂：空表已建 ==")

    # ── R1 送达预检：heldout 每题 goal 走同一条 recall() 路径 ─────────────
    print("== R1 送达预检 ==")
    ok = True
    for t in heldout:
        rH = memH.recall(t["prompt"], k=6)
        rP = memP.recall(t["prompt"], k=6)
        rE = MemoryStore(out / "memE" / "memory.db").recall(t["prompt"], k=6)
        good = len(rH) >= 1 and len(rP) >= 1 and len(rE) == 0
        ok &= good
        print(f"  {'✅' if good else '❌'} H={len(rH)} P={len(rP)} E={len(rE)}"
              f"  {t['id']}")
    print("R1 预检：" + ("通过" if ok else "❌ 失败（处理未送达，跑臂没有意义）"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

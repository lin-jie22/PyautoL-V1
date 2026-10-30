#!/usr/bin/env python3
"""V25 前提闸（零模型）：跨轮累积的**载荷**到底是什么 —— 它随轮数变吗？

为什么必须先问这个（不是形式主义）：
  `reward_guidance`（`mw/worldmodel.py:574`）有两条硬规则：
    ① 同一 `task_id` 一律跳过（`rewards_skipped_self`）—— 开卷考同一道题不算学习；
    ② `passed=True` 的行**不产生提示行**（`rewards_skipped_success`）。
  ⇒ 「跨轮累积」在同题上**结构上不可达**，唯一可走的路是**跨题特征迁移**
     （`len(cur & hist) / len(cur | hist) >= REWARD_MIN_SIM` = 0.30）。

  所以 V25 起跑之前必须先量：第 2/3/4 轮某题读到的载荷，是否与第 1 轮**不同**。
  若三档载荷逐字相同 ⇒ 跨轮累积在**内容层**没有增量，Δ 只可能来自
  「哪一次采样点碰巧变了」⇒ 那样起跑 15 轮只是把一次偶然重复 15 次。
  这正是 §76/§77/§78 的老病（机会 × 兑现），也是纪律 25 的教训
  （新机关之前先量现有机关拦住了多少）。

读法：**只用已落盘的真实行**（`/tmp/v24/seed.db` 的 18 条 reward），不调模型、不造数据。
切前 6 / 12 / 18 条 = 模拟「跑完 1 / 2 / 3 轮后的库」。

★★ 首跑被自己的判据骗了（本文件 v1 → v2 的唯一改动）：
   v1 的判据是「载荷 sha 是否变化」。实测三档**全部变化** ⇒ 判「前提成立」。
   打印全文之后才看见：三档之间**唯一差别是计数**（`NameError 出现过 2 次 / 4 次 / 6 次`），
   类别、检查项、行数全同。计数变大不是新信息 ⇒ v1 的「变」是**数字在变**，不是载荷在变。
   ⇒ 现在同时报两个 sha：原文 sha（`raw`）与**数字归一化**后的 sha（`norm`，`\d+` → `N`）。
     判「跨轮有没有新内容」只认 `norm`。

退出码：0 = 前提成立（轮 1 空、轮 2 起非空）
        1 = 前提不成立（轮 2 起仍全空 ⇒ 累积通道在检索层断电）⇒ ⛔ 不许起 V25 主跑
        2 = 装置异常（题集/库/接口不符）
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.features import feature_set            # noqa: E402
from mw.memory import MemoryStore             # noqa: E402
from mw.worldmodel import WorldModel, REWARD_MIN_SIM  # noqa: E402

SUITE = ROOT / "bench/specgen/tasks_v8.jsonl"
SRC_DB = Path("/tmp/v24/seed.db")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def load_tasks() -> list[dict]:
    out = []
    for line in SUITE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def reward_rows(db: Path) -> list[tuple]:
    c = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(episodes)")}
        rows = c.execute(
            "SELECT content, meta FROM episodes WHERE kind='reward' ORDER BY id"
        ).fetchall()
    finally:
        c.close()
    if not rows:
        raise SystemExit(f"❌ {db} 里没有 reward 行（检查装置）")
    return [(r[0], r[1], cols) for r in rows]


def build_db(rows: list[tuple], path: Path) -> None:
    """把前 N 条真实 reward 行灌进一个新库（其余表留空 —— 本探针只看 reward 通道）。"""
    store = MemoryStore(path)
    try:
        for content, meta, _cols in rows:
            m = json.loads(meta) if isinstance(meta, str) else dict(meta or {})
            store.record("reward", content, **m)
    finally:
        store.close()


def guidance_for(db: Path, tasks: list[dict]) -> dict[str, str]:
    store = MemoryStore(db)
    try:
        wm = WorldModel(store, llm=None)          # reward_guidance 不用 llm
        out = {}
        for t in tasks:
            feats = feature_set(t)
            out[t["id"]] = wm.reward_guidance(features=feats, task_id=t["id"])
        return out
    finally:
        store.close()


def main() -> int:
    if not SRC_DB.exists():
        print(f"❌ 缺 {SRC_DB}")
        return 2
    tasks = load_tasks()
    if len(tasks) != 6:
        print(f"❌ 题集条目 {len(tasks)} ≠ 6")
        return 2
    ids = [t["id"] for t in tasks]
    rows = reward_rows(SRC_DB)
    print(f"源库 {SRC_DB}：reward {len(rows)} 行；题集 {ids}")
    if len(rows) < 18:
        print(f"⚠️ reward 行只有 {len(rows)} 条，跨轮切段不足（需要 ≥18 = 3 轮 × 6 题）")
        return 2

    #: 题面特征与跨题相似度（Jaccard）—— 决定「跨题迁移」在结构上通不通
    print("\n--- ① 特征与跨题相似度（阈值 REWARD_MIN_SIM = %.2f）---" % REWARD_MIN_SIM)
    feats = {t["id"]: feature_set(t) for t in tasks}
    min_sim = 1.0
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            A, B = set(feats[a]), set(feats[b])
            j = len(A & B) / len(A | B)
            min_sim = min(min_sim, j)
            flag = "✅" if j >= REWARD_MIN_SIM else "❌"
            print(f"  {flag} {a:5s} vs {b:5s}  J={j:.3f}   |A|={len(A)} |B|={len(B)}")
    print(f"  ⇒ 跨题相似度最小 {min_sim:.3f}（{'全部过阈' if min_sim >= REWARD_MIN_SIM else '有对不过阈'}）")

    #: 逐轮切段：跑完 k 轮后的库 = 前 6k 条 reward 行
    stages = [6, 12, 18]

    def norm(body: str) -> str:
        """数字归一化：判「跨轮有没有新内容」只认它（计数变大不是新信息）。"""
        return re.sub(r"\d+", "N", body)

    print("\n--- ② 逐轮载荷（模拟「跑完 k 轮、第 k+1 轮起跑时」读到的东西）---")
    per_stage: dict[int, dict[str, str]] = {}
    tmp = Path(tempfile.mkdtemp(prefix="v25_probe_"))
    for n in [0] + stages:
        db = tmp / f"m{n}.db"
        build_db(rows[:n], db)
        got = guidance_for(db, tasks)
        per_stage[n] = got
        print(f"\n  库里有 {n} 条 reward（= 跑完 {n // 6} 轮）：")
        for tid in ids:
            body = got[tid]
            if body:
                print(f"    {tid:5s} {len(body):4d} 字  raw={sha(body)}  "
                      f"norm={sha(norm(body))}  {len(body.splitlines()) - 1} 行 | "
                      f"{body.splitlines()[-1][:56]}")
            else:
                print(f"    {tid:5s} 载荷 空（读不到任何可执行提示）")

    print("\n--- ③ 跨轮增量：按**数字归一化**后逐字比较（原文 sha 只作参考）---")
    for tid in ids:
        print(f"  {tid:5s} 空库={'空' if not per_stage[0][tid] else sha(norm(per_stage[0][tid]))}  "
              + "  ".join(f"k={n // 6}:raw={sha(per_stage[n][tid])[:6]}/"
                          f"norm={sha(norm(per_stage[n][tid]))[:6]}" for n in stages))

    #: ④ 归一化之后，轮 2 起是否还有**内容级**增量
    print("\n--- ④ 归一化后：轮 1 → 2 → 3 有没有内容级变化 ---")
    frozen = 0
    for tid in ids:
        a, b, c = (norm(per_stage[n][tid]) for n in stages)
        stable = (a == b == c)
        frozen += 1 if stable else 0
        print(f"  {tid:5s} {'内容恒定（只有计数在涨）' if stable else '内容有变化'}")

    print()
    nonempty = sum(1 for t in ids if per_stage[18][t])
    empty_r1 = sum(1 for t in ids if not per_stage[6][t])
    print(f"汇总：满库(18条)下非空 {nonempty}/6 题；跑完 1 轮后为空 {empty_r1}/6 题；"
          f"轮 2 起内容恒定 {frozen}/6 题")
    if nonempty == 0:
        print("❌ 前提不成立：轮 2 起 6 题仍全空 ⇒ 累积通道在**检索层**断电")
        print("   ⇒ 与 §0 的 facts 通道同形态（机制存在 ≠ 机制通电）。⛔ 不许起 V25 主跑。")
        return 1
    if empty_r1 == 0:
        print("⚠️ 轮 1 就有载荷 ⇒ 起点库不是空的，V25 须用空库起跑，否则「冷启动」格不可辨。")
    print(f"✅ 前提成立：轮 1 空 / 轮 2 起非空（{empty_r1}/6 → 0/6），"
          f"内容级恒定 {frozen}/6 题 ⇒ 装置是**一次跳变 + 平台**，正好能测那一步的净值。")
    print("   ⚠️ 由 ④ 得到一条预注册必须写死的形状预测：若该载荷有效，净值应集中在")
    print("      「轮 1 → 轮 2」这一跳，轮 3/4 与轮 2 **逐格相同**（载荷内容不变 + T=0）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

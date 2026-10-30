#!/usr/bin/env python3
"""V23 的 P0 前提闸（零模型）—— 三档回显到底给出了三段什么样的提示。

V23 的唯一变量是 `V1_REWARD_ECHO ∈ {0, 1, 2}`，**同一个 seed 库**：

    A0  `=0`  奖励段**不回显**                 ⇒ 应当整块消失
    A1  `=1`  真回显（默认）                   ⇒ 现状（V22 的 S1 那一侧）
    A2  `=2`  **逐行等长、内容无关**的安慰剂    ⇒ 应当与 A1 同长度、零真词

这个探针证明四件事（`P0` 全过才允许起跑）：

  `P0-a` `A0` 的 `guidance()` 里 reward 段的标志词一次都不出现；
  `P0-b` `A1` 与 `A2` **逐行等长**（行数相同、每行字符数相同、`- ` 前缀相同）；
  `P0-c` `A2` **零真词泄漏**（类别名 / 检查项 / 通道名一个都不许有）；
  `P0-d` 三档读**同一个库**（`shasum` 相同）⇒ 唯一变量真的是 env 一位。

⛔ 不碰模型：只调真正的 `WorldModel.guidance`，印出来的就是内核会给模型的那段。

用法：
    python3 bench/evidence/v6_s114/probe_v23_echo.py <seed.db> [tasks_v8.jsonl]
退出码：0 = 四条 P0 全过；1 = 不过（并印出反例）
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1))

from mw.features import feature_set          # noqa: E402
from mw.llm import StubLLM                   # noqa: E402
from mw.memory import MemoryStore            # noqa: E402
from mw.worldmodel import WorldModel         # noqa: E402

#: reward 段的标志词（`A0` 里一个都不许有）
REWARD_MARKS = ("历史同类题的外部得分", "同类题里 `", "先检查")
#: 安慰剂里一个都不许有的**真词**（类别名 + 检查项片段 + 通道名）
REAL_WORDS = ("NameError", "AttributeError", "TypeError", "IndexError", "KeyError",
              "AssertionError", "先检查", "import", "同类题", "得分")


def render(db: Path, tasks: list[dict], echo: int) -> dict[str, str]:
    """按**真实调用点**取提示：`guidance(action=题面, features=…, task_id=…)`。"""
    wm = WorldModel(MemoryStore(db), StubLLM(), reward_echo=echo)
    out = {}
    for t in tasks:
        tid = str(t["id"])
        # ⚠️ `action` 必须是**题面原文**：`guidance` 用它去 `memory.recall(action)`，
        #    传错就等于换了一套检索条件（V22 的探针注释里已经踩过这条）。
        out[tid] = wm.guidance(action=str(t["prompt"]), k=3,
                               features=feature_set(t), task_id=tid)
    out["__stats__"] = (f"挡自己={wm.rewards_skipped_self} "
                        f"挡成功={wm.rewards_skipped_success} 读={wm.rewards_seen}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    db = Path(argv[1])
    suite = Path(argv[2] if len(argv) > 2 else V1 / "bench/specgen/tasks_v8.jsonl")
    if not db.exists():
        print(f"❌ seed 不存在：{db} ⇒ P0 不可判")
        return 1
    tasks = [json.loads(x) for x in suite.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    tids = [str(t["id"]) for t in tasks]

    # `P0-d`：三档读同一个文件 ⇒ 唯一变量是 env 一位，不是一个不同的库。
    sha = hashlib.sha256(db.read_bytes()).hexdigest()[:16]
    print(f"seed = {db}  sha256={sha}  题数={len(tasks)}  "
          f"suite={suite.name}")

    a0 = render(db, tasks, 0)
    a1 = render(db, tasks, 1)
    a2 = render(db, tasks, 2)
    print(f"A0(echo=0) {a0.pop('__stats__')}")
    print(f"A1(echo=1) {a1.pop('__stats__')}")
    print(f"A2(echo=2) {a2.pop('__stats__')}")

    ok = True
    print("\n" + "=" * 76)
    for tid in tids:
        s0, s1, s2 = a0[tid], a1[tid], a2[tid]
        # P0-a：A0 里不许有 reward 段标志词
        leak0 = [m for m in REWARD_MARKS if m in s0]
        # P0-b：A1/A2 逐行等长
        l1, l2 = s1.split("\n"), s2.split("\n")
        same_lines = len(l1) == len(l2)
        same_len = same_lines and all(len(x) == len(y) for x, y in zip(l1, l2))
        same_bullet = same_lines and all(x.startswith("- ") == y.startswith("- ")
                                         for x, y in zip(l1, l2))
        # P0-c：A2 零真词
        leak2 = [w for w in REAL_WORDS if w in s2]
        good = (not leak0) and (s1 != "") and same_lines and same_len \
            and same_bullet and (not leak2) and (s2 != s1) and (s0 == "" or not leak0)
        ok = ok and good
        print(f"{tid:<6} |A0|={len(s0):3d} |A1|={len(s1):3d} |A2|={len(s2):3d}  "
              f"A0泄漏={leak0 or '无'}  行数={len(l1)}/{len(l2)}  "
              f"逐行等长={same_len} 前缀同={same_bullet}  A2泄漏={leak2 or '无'}  "
              f"{'✅' if good else '❌'}")

    # 逐字印出三条，供人眼核对（`e1ni`：唯一对这段敏感的那道题）
    t = next((x for x in tasks if str(x["id"]).startswith("e1ni")), tasks[0])
    tid = str(t["id"])
    print("\n" + "=" * 76)
    for lbl, src in (("A0", a0), ("A1", a1), ("A2", a2)):
        print(f"\n--- {lbl} · {tid}  len={len(src[tid])}")
        print(src[tid] if src[tid] else "(空串)")
    print("\n" + "=" * 76)
    print("P0 =", "✅ 四条全过（A0 整块消失 / A1≡A2 逐行等长 / A2 零真词 / 同一个库）"
          if ok else "❌ 不过，见上面的 ❌ 行")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

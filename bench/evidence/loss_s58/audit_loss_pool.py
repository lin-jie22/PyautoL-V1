"""把失分按「自检过没过 × 一跑就炸/就是错」分成三个池子。

⚠️ 这是一次**事后审计**（第九十一轮之后回头看第九十轮的冻结答案），
   不是预注册实验。它的作用是回答一个问题：**还剩多少是可修的**。

用法（仓库根目录，零模型调用、零网络）：

    python3 bench/evidence/loss_s58/audit_loss_pool.py

自带的反证（否则整张表作废）：
  V1 逐份复算的分数必须与 `model_s90/readings.log` 里落盘的逐题格数**完全相同**。
     不一致 ⇒ 说明「代码块提取」或「执行环境」与当时不同 ⇒ 本表作废。
  V2 反例冻结：写死的 ORACLE 代码块必须 14/14，写死的 CRACKED 代码块必须 < 14。
     两条都不成立 ⇒ 分类器是恒真的。

⛔ 本脚本**只读** evidence/ 与 tasks_v2.jsonl，不写任何仓库文件。
"""

from __future__ import annotations

import json
import re
import sys

ROOT = "bench/specgen/tasks_v2.jsonl"
EV = "bench/evidence/model_s90"

#: 第九十轮 `readings.log` 落盘的逐题格数（臂 → 题 → 三轮）。
#: 这是本脚本的**外部真值**：复算结果必须逐格等于它。
FROZEN: dict[str, dict[str, list[int]]] = {
    "A_7b": {
        "sg01": [12, 12, 14], "sg02": [7, 10, 10], "sg03": [14, 14, 14],
        "sg04": [14, 14, 14], "sg05": [0, 0, 0], "sg06": [8, 8, 8],
        "sg07": [12, 12, 12], "sg08": [7, 7, 7], "sg09": [12, 12, 12],
        "sg10": [14, 1, 14],
    },
    "B_4b": {
        "sg01": [10, 10, 10], "sg02": [14, 14, 14], "sg03": [14, 14, 14],
        "sg04": [14, 14, 14], "sg05": [7, 7, 7], "sg06": [14, 14, 14],
        "sg07": [13, 13, 13], "sg08": [14, 14, 14], "sg09": [12, 12, 12],
        "sg10": [4, 4, 4],
    },
}

#: 运行时错误的白名单（这些是「代码没跑起来」，不是「结果不对」）。
RT = frozenset({
    "NameError", "AttributeError", "TypeError", "IndexError", "KeyError",
    "ValueError", "ZeroDivisionError", "RecursionError", "UnboundLocalError",
    "SyntaxError", "ImportError", "ModuleNotFoundError", "StopIteration",
    "FloatingPointError", "OverflowError",
})


def load_tasks(path: str = ROOT) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                t = json.loads(line)
                out[t["id"]] = t
    return out


def code_of(answer: str) -> str:
    """从交付物里取出代码块；没有围栏就当整段都是代码。"""
    m = re.search(r"```(?:python)?\n(.*?)```", answer or "", re.S)
    return m.group(1) if m else (answer or "")


def first_failure(tests: list[str], code: str) -> tuple[str, str]:
    """返回 (首个失败的类型, 说明)。`-` 表示全部通过。

    每条断言都在**全新命名空间**里跑：先 exec 代码，再 exec 断言。
    这与 bench 的打分器同构（每条断言独立，不共享状态）。
    """
    for a in tests:
        ns: dict = {}
        try:
            exec(code, ns)  # noqa: S102 —— 被测对象本身就是代码
            exec(a, ns)  # noqa: S102
        except AssertionError:
            return "ASSERT", ""
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            kind = "RT" if name in RT else "OTHER"
            return kind, f"{name}: {exc}"
    return "-", ""


def score_of(tests: list[str], code: str) -> int:
    ok = 0
    for a in tests:
        ns: dict = {}
        try:
            exec(code, ns)  # noqa: S102
            exec(a, ns)  # noqa: S102
            ok += 1
        except Exception:  # noqa: BLE001
            pass
    return ok


def gate_of(arm: str) -> list[int]:
    """从运行日志里取「自检过了几次」。顺序与答案文件一致（rep-major，每 rep 10 题）。"""
    out: list[int] = []
    cur = None
    with open(f"{EV}/run_{arm}.log", encoding="utf-8") as fh:
        for line in fh:
            m = re.search(r"任务开始: 写一个函数 (norm_\w+)", line)
            if m:
                cur = m.group(1).replace("norm_", "")
            if "■ " in line and cur:
                v = re.search(r"校验=(\d+)跑/(\d+)过", line)
                out.append(int(v.group(2)) if v else 0)
    return out


def answers_of(arm: str) -> list[dict]:
    with open(f"{EV}/answers_{arm}.jsonl", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    # 第 0 行是 header（kind/run_id/ts/n_answers/cap/meta），没有 task_id
    return [r for r in rows if r.get("task_id")]


def self_check(tasks: dict[str, dict]) -> bool:
    """V2 反例冻结：oracle 必须满分，被削过的必须不满分。"""
    tid = "sg01"
    t = tasks[tid]
    good = score_of(t["tests"], t["oracle"])
    bad = score_of(t["tests"], "def norm_sg01(rows):\n    return list(rows)\n")
    print(f"  V2 oracle 满分？ {good}/14  {'✅' if good == 14 else '❌ 分类器坏了'}")
    print(f"  V2 削过的必须不满分？ {bad}/14  {'✅' if bad < 14 else '❌ 分类器恒真'}")
    return good == 14 and bad < 14


def main() -> int:
    tasks = load_tasks()
    print("=" * 74)
    print("  失分三池审计：自检过没过 × 一跑就炸 / 就是错")
    print("  零模型调用；逐份复算的分数必须与 model_s90/readings.log 逐格相同")
    print("=" * 74)
    print("\n[反证]")
    if not self_check(tasks):
        print("\n❌ 反证不成立 ⇒ 整张表作废")
        return 1

    pools = {"无闸": 0, "假绿-崩": 0, "逻辑错": 0}
    detail: list[str] = []
    mismatch = 0
    per_arm: dict[str, dict] = {}

    for arm in ("A_7b", "B_4b"):
        rows = answers_of(arm)
        gates = gate_of(arm)
        got_total = 0
        arm_pool = {"无闸": 0, "假绿-崩": 0, "逻辑错": 0}
        for i, r in enumerate(rows):
            tid = r["task_id"]
            rep = i // 10
            t = tasks[tid]
            code = code_of(r.get("answer", ""))
            got = score_of(t["tests"], code)
            exp = FROZEN[arm][tid][rep]
            if got != exp:
                mismatch += 1
                detail.append(f"  ⚠️ {arm} {tid} rep{rep + 1} 复算 {got} ≠ 落盘 {exp}")
                continue
            got_total += got
            loss = 14 - got
            if loss <= 0:
                continue
            passed = gates[i] == 1
            kind, msg = first_failure(t["tests"], code)
            pool = ("逻辑错" if kind == "ASSERT" else "假绿-崩") if passed else "无闸"
            arm_pool[pool] += loss
            pools[pool] += loss
            detail.append(
                f"  {arm:5s} {tid} rep{rep + 1} 闸={'过 ' if passed else '未过'} "
                f"失{loss:2d} 池={pool:8s} {msg[:52]}"
            )
        per_arm[arm] = {"total": got_total, "pool": arm_pool}

    print("\n[逐份明细]")
    for line in detail:
        print(line)

    total = sum(pools.values())
    print("\n[三池]")
    for k, v in pools.items():
        print(f"  {k:8s} {v:4d} 格  {v / total * 100:5.1f}%")
    print(f"  {'合计':8s} {total:4d} 格")

    print("\n[两臂]")
    for arm, d in per_arm.items():
        print(f"  {arm:5s} 总计 {d['total']}/420 = {d['total'] / 420:.4f}   "
              + "  ".join(f"{k}={v}" for k, v in d["pool"].items()))

    # ── 反事实：只把「假绿-崩」按满分算，看判定会不会翻 ──────────────
    crash = pools["假绿-崩"]
    a = per_arm["A_7b"]["total"]
    b = per_arm["B_4b"]["total"]
    print("\n[反事实]（⚠️ 不是读数，只回答「这个 Δ 能不能解释成能力差」）")
    print(f"  事实      : A {a}/420={a / 420:.4f}  vs  B {b}/420={b / 420:.4f}  "
          f"Δ={b - a} 格")
    print(f"  A+{crash}    : {(a + crash)}/420={(a + crash) / 420:.4f}  "
          f"Δ={(b - a - crash)} 格")

    print("\n[结论]")
    if mismatch:
        print(f"  ❌ {mismatch} 份复算与落盘不一致 ⇒ 本表作废")
        return 1
    print("  ✅ 60 份逐格复算与落盘读数完全相同 ⇒ 分类器与当时的打分器同构")
    print(f"  ① 无闸（闸没过/没跑）          {pools['无闸'] / total * 100:5.1f}%  "
          "→ §57 已接闸，只记账不改分数")
    print(f"  ② 假绿-崩（闸说过了，一跑就炸） {pools['假绿-崩'] / total * 100:5.1f}%  "
          "→ 可加「可执行性」判据，但检出≠修复")
    print(f"  ③ 逻辑错（闸没错，答案就是不对） {pools['逻辑错'] / total * 100:5.1f}%  "
          "→ 不读 oracle 的判据抓不到（§47 / §84 已量）")

    noise_recheck(a, b, pools["假绿-崩"])
    return 0


def noise_recheck(a_total: int, b_total: int, crash: int) -> None:
    """用干净的尺子重算 §56.4 的判定。

    ⚠️ 单位必须一致：`readings.log` 里的极差是**每轮**的极差，
       所以 Δ 也必须换成**每轮**，不能拿三轮合计去比。这一条差点写错。
    """
    print()
    print("=" * 74)
    print("  附：用干净的尺子重算 §56.4 的判定（反事实，不是新读数）")
    print("=" * 74)
    raw = [100, 90, 105]
    #: 每轮里由装置事件扣掉、不该算作采样抖动的格数：
    #: rep1 sg01 −2（闸打回后没重跑）、rep2 sg01 −2 + sg10 −13（无闸交付）、rep3 无。
    dev = [2, 2 + 13, 0]
    clean = [r + d for r, d in zip(raw, dev)]
    print(f"  臂A 三轮原始      : {raw}   极差={max(raw) - min(raw)} 格")
    print(f"  扣掉装置事件后    : {clean}   极差={max(clean) - min(clean)} 格   "
          "← 这才是采样噪声的量级")
    noise = max(clean) - min(clean)

    # 10 题口径（原样）
    d10 = (b_total - a_total) / 3
    print(f"\n  10 题 420 格（原样）")
    print(f"    每轮 Δ = {d10:.2f} 格   噪声 = 15 格（§56.4 用的那个）⇒ 余量 {d10 - 15:.2f} 格")
    print(f"    每轮 Δ 不变，噪声换成 {noise} 格                     ⇒ 余量 {d10 - noise:.2f} 格")

    # 9 题口径：剔除 sg05 —— A 那三份 0 分全部是自检假绿，不是能力读数
    n = 9 * 14
    d9 = (b_total - 21 - a_total) / 3
    print(f"\n  9 题 126 格/轮（剔除 sg05）")
    print(f"    臂A {a_total}/378 = {a_total / 378:.4f}   "
          f"臂B {b_total - 21}/378 = {(b_total - 21) / 378:.4f}")
    print(f"    每轮 Δ = {d9:.2f} 格   噪声 = {noise} 格 ⇒ "
          f"{'可分辨' if abs(d9) > noise else '噪声内（UNKNOWN）'}，余量 {abs(d9) - noise:.2f} 格")
    print()
    print("  ⛔ 这不是新读数：剔除 sg05 是「那三格不可解释为能力」的推论，不是重跑。")
    print("  ⛔ 也不能反过来说「A 若答对 sg05 就赢」：42 格按满分算是无依据的天花板假设。")
    print(f"     （事实：A+{crash} = {a_total + crash} < B {b_total}，仍差 {b_total - a_total - crash} 格）")

    # 两个反事实把余量夹成一个区间 —— 都写出来，不挑对自己有利的那个
    optimistic = (b_total - (a_total + crash)) / 3   # 假 A 答对 sg05：Δ 最小
    conservative = d9                                 # 剔除 sg05：Δ 最大
    print("\n  余量的区间（取决于怎么处理 sg05，两种处理都没有实测支撑）：")
    print(f"    最乐观（A 答对 sg05）每轮 Δ={optimistic:.2f} ⇒ 余量 {optimistic - noise:.2f} 格")
    print(f"    最保守（剔除 sg05）  每轮 Δ={conservative:.2f} ⇒ 余量 {conservative - noise:.2f} 格")
    print(f"    ⇒ 判定在两种处理下都是「可分辨」，但余量在 "
          f"{optimistic - noise:.2f} ~ {conservative - noise:.2f} 格之间摆动")


if __name__ == "__main__":
    sys.exit(main())

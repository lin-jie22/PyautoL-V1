# V75 预注册 —— 打回后的重写**只给目标 + 报错**（`REJECT_CARRY=3`）

> 证据目录：`bench/evidence/v6_s164/`
> 本轮**只回答一个问题**：`replan` 那条死边上，**最后一个没被隔离过的输入**（`history`，里面是模型自己那份旧代码）是不是钉子？

## 0 为什么要打这一刀

三条已闭合的读数把思路逼到这里：

| 轮 | 读数 | 它排除了什么 |
|---|---|---|
| V71 | 打回→重写兑现率 **1/19** | 排除「重写会自己变好」 |
| V73 | `REJECT_CARRY=2`（`replan` 不带 `state`）⇒ `redelivered/same` = **16/16**、`diff=0` | 排除 **`state` 块**是钉子 |
| V74 | **交付通道**去掉 `state` + 换正向 system ⇒ t04 **0.3333→1.0000**（六跑逐格同） | 证明「把上下文拿掉，生成就不再复读旧实现」 |

⚠️ V73 的「不带状态」其实**不等于看不见旧实现**：`planner.py:741`

```python
augmented = list(history) + [f"{failed.as_line()} ⇒ 失败: {error[:200]}"]
plan = self.propose(goal=goal, state=..., history=augmented, ...)
```

⇒ `state` 摘掉之后，`history` 里**仍然有模型自己那份被拒的代码**。
本轮摘掉的是这个最后未被隔离的输入。

## 1 本轮改了什么（唯一行为差别 = 一个 env 取值）

`mw/core.py` 的 `_carry` 分支新增 `reject_carry == 3`：

```python
elif _rc == 3:
    _carry_state = ""
    _carry_hist = []          # ⇒ replan 的 augmented 里只剩那一行失败说明
    self.log("  ↻ 打回重写：只给目标+报错（不带状态、不带轨迹）（REJECT_CARRY=3）")
```

- ⛔ `0`/`1`/`2` 语义**一个字不动**；⛔ 不动 `carried` 计数点；⛔ 不动 `self._reject_carry = ""`
  （AX124-⑩ 钉住那行字符串）；⛔ 不加 `Limits` 新键（`ENV_KEYS` / `len==27` / 撤回态全不动）。
- 默认（未设 env）⇒ 与 V67~V74 **逐字同路**。

## 2 单变量声明

跑批脚本 `run_v75_rc3.sh` 与 V73 的 `v6_s162/run_v73_carry.sh` **只差一行**：
`V1_REJECT_CARRY` 从 `2` 改成 `3`。其余常量（6 题 `coding_small`、`--memory-dir` 累积、
`--repeat 1`、`T=0`、`allow_shell`、`use-reward`、`CAND_FAILOVER=1`）**一个字不动**。
`V1_ANSWER_CLEAN` **不设**（D 格臂关）⇒ 交付路径与基线同形，避免混两个变量。

⇒ 基线 = **V73 的 B 臂**（8 跑）：`carried=8`、`redelivered=16`、`same=16`、`diff=0`、
`dup_skipped=16`、`passed_after=0`、t04 恒 0.3333、`mean` 0.8611。

## 3 判据（押三值；⛔ 判据分辨率不得高于装置）

分母口径写进名字：`n_runs=8`、`n_tasks=6`、`--repeat 1`（⇒ 不乘 R）。

| # | 判据 | 押 | 机制 |
|---|---|---|---|
| **P1a** | `rework_same`（8 跑绝对数，基线 **16**） | **降** | 若逐字重提是「输入里有旧代码」导致的，摘掉 `history` 后输出应至少变一次 |
| **P1b** | `rework_redelivered`（基线 **16**） | 只登记 | ⚠️ 若降到 **0** ⇒ P1a 的**分母消失** ⇒ 本轮记 `UNKNOWN`，⛔ 不许读成「改善了」 |
| **P2** | `task_score_t04`（基线 0.3333） | **升** | 重写换了实现就可能过题面示例 ⇒ 不必走到收尾就被判过；若「平」⇒ 钉子不在输入，在生成确定性；若「降」⇒ 摘轨迹反而更差，须如实报 |
| **P3** | 其余五题**逐题** ≥ 基线（t01 0.8333、t02/t03/t05/t06 = 1.0） | **平**（veto） | 本轮只动重写那一次，不该波及一次过的题 |
| **P4** | `rework_diff`（基线 **0**） | 只登记 | 与 P1a 同向；`same + diff == redelivered` 是硬不变式，不等 ⇒ 记账错 |

**通电自检（缺一即 `UNKNOWN`）**

1. `↻ 打回重写：只给目标+报错` 在 **8/8** 份 `core.log` 里出现；
2. `rework_carried` = **8**（每跑恰好一次携带）；
3. 该跑日志里 `[N] 候选=` 的 N 至少出现过 **≥3**（说明 replan 真的又排了一次，不是直接收尾）。

## 4 已知风险与边界（⛔ 不许越读）

- ⛔ **这不是「改提示词措辞」**：动的是**哪些输入进入那次调用**，不是那句话怎么写（V69/§132 的撤回仍有效）。
- ⛔ **不许说「模型会自己改错」**：本轮只测**一次**重写的输出是否变化。变 ≠ 变对。
- ⛔ **不许说「记忆有害」**：本轮摘的是**模型自己的旧代码**，不是记忆库。
- ⚠️ **分母风险写在 P1b 里**：`redelivered` 归零会让 P1a 变成无分母读数。
- ⚠️ 若 P1a 降而 P2 平 ⇒ 「重写换路了但没变对」⇒ 这条边的下一个问题是**质量**，不是多样性。
- 许撤不许藏：若 P1a 平（`same` 仍 16）⇒ 「输入构造」在重写通道上彻底关闭，该线结束，开关留默认关。

## 5 产物

`bench/evidence/v6_s164/`：`PREREG.md`（本文）、`run_v75_rc3.sh`、`analyze_v75.py`、
`runs_v75_r{1..8}.jsonl`、`core_r{1..8}.log`、`timeline.txt`、`analyze_v75.out`、`gate_v75.txt`、`RESULTS.md`。

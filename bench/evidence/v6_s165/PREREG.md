# V76 预注册 —— 真的只给目标 + 报错：把「旧答案原文」从重写提示词里清干净（`REJECT_CARRY=4`）

> 证据目录：`bench/evidence/v6_s165/`
> 本轮**只回答一个问题**：重写时**完全看不见自己那份旧答案**，输出会不会变？

## 0 为什么还要打一刀

V75 的结论必须按实际做到的操作来写（`v6_s164/RESULTS.md` §3）：

| 取值 | `state` | `history` | `blocked`（→`banned`） | `rejected`（→`reseek`） | 实测 |
|---|---|---|---|---|---|
| `1` | 有 | 有 | 有 | 有 | `same=16/8跑`（V67/V73 基线） |
| `2` | **无** | 有 | 有 | 有 | `same=16`（V73） |
| `3` | **无** | **无** | 有 | 有 | `same=16`（V75） |
| **`4`（本轮）** | **无** | **无** | **脱敏**（留 `tool\|` 前缀） | **脱敏**（留 `tool(` 前缀） | ？ |

⇒ 前三个取值都**没有**把旧答案从提示词里清掉：`blocked` 的元素是
`action_signature('finish', {'answer': <整段代码>})`，`rejected` 的元素是
`Step.as_line()` = `finish({'answer': ...})`。两处都带原文（`v6_s164/RESULTS.md` §3 有落原文）。
本轮把这两块的**正文**也拿掉，**第一次**做到「目标 + 报错 + 工具目录（+ 一条不带载荷的"这些动作试过"）」。
⚠️ 不整块摘掉的原因见 §1：整块摘会破 AZ63。

## 1 改了什么（唯一行为差别 = 一个 env 取值）

⚠️ **设计在起跑前改过一次**（原方案是"把 `blocked`/`rejected` 整个摘掉"）：那样会破
**AZ63**（钉住"所有进规划器的路都喂同一份 `reseek_lines`"，明写 ⛔ 不许放宽）。
改成**只脱敏载荷、不动接线** —— 摘的是「旧答案正文」，不是「这条不变量」。

`mw/core.py` 的 `_carry` 分支新增 `reject_carry == 4`：

```python
elif _rc == 4:
    _carry_state = ""
    _carry_hist = []
    self.log("  ↻ 打回重写：只给目标+报错（REJECT_CARRY=4，不带状态/轨迹 + 载荷脱敏）")
plan = self.planner.replan(..., blocked=sorted(attempted_sigs), rejected=reseek_lines,
                           state=_carry_state, redact=(_rc == 4))
```

`mw/planner.py`：新增 `_redact_action()`；`replan(..., redact=False)` 透传给 `propose(..., redact=False)`；
`propose()` 在**渲染前**把 `blocked`/`rejected` 的载荷正文换成占位
（`finish|(载荷已省略 131 字)` / `finish(载荷已省略 133 字)`），
⛔ 保留 `tool|` 与 `tool(` 前缀 ⇒ `_untried_tools()` 与 `reseek_block()` 仍在同一处事实之上。

冒烟实测（真函数、非真跑批）：同一份输入下 `redact=False` 提示词 **1229 字、含旧代码=True**；
`redact=True` **1004 字、含旧代码=False**，且 `一次都没试过的工具：recall / run_shell` 仍正确。

- ⛔ `0`/`1`/`2`/`3` 语义**一个字不动**（`redact` 默认 `False` ⇒ 与改动前逐字同路）。
- ⛔ 不加 `Limits` 新键；⛔ 不动 `self._reject_carry = ""`（AX124-⑩）；⛔ 不动 AZ63 的三处喂入。
- 契约实跑 **629/0**（含 AZ63）。

## 2 单变量声明

`run_v76_rc4.sh` 与 `v6_s164/run_v75_rc3.sh` **只差一行**：`V1_REJECT_CARRY` `3`→`4`。
其余常量（6 题 `coding_small`、`--memory-dir` 累积、`--repeat 1`、`T=0`、`allow_shell`、
`use-reward`、`CAND_FAILOVER=1`、`V1_ANSWER_CLEAN` 不设）**一个字不动**。

⇒ 基线 = **V75 的 8 跑**（`RC=3`）：`carried=8 redelivered=16 same=16 diff=0 dup_skipped=16 passed_after=0`、
t04 恒 0.3333、`mean` 0.8611。**同题集、同常量、只差这一个取值。**

## 3 判据（押三值；⛔ 判据分辨率不得高于装置）

| # | 判据 | 押 | 机制 |
|---|---|---|---|
| **P1a** | `rework_same`（8 跑绝对数，基线 **16**） | **降** | 旧答案原文是最后一块上下文；V74 证明拿掉上下文能改生成 |
| **P1b** | `rework_redelivered`（基线 **16**） | 只登记 | ⚠️ **归 0 ⇒ P1a 无分母 ⇒ 本轮 `UNKNOWN`**（模型改提动作而非 `finish`）；⛔ 不许读成「改善」 |
| **P2** | `task_score_t04`（基线 0.3333） | **升** | 换了实现才可能过题面示例；若「平」⇒ 钉子不是输入 |
| **P3** | 其余五题逐题 ≥ 基线 | **平**（veto） | 只动重写那一次 |
| **P4** | `rework_diff`（基线 0） | 只登记 | `same+diff==redelivered` 是硬不变式 |

**通电自检（缺一即 `UNKNOWN`）**

1. `↻ 打回重写：只给目标+报错（REJECT_CARRY=4，不带状态/轨迹 + 载荷脱敏` 在 **8/8** 份 `core.log` 里出现；
2. `rework_carried` = **8**；
3. 该跑日志里 `[N] 通道=` 的 N 至少出现过 **≥3**。

## 4 已知风险与边界（⛔ 不许越读）

- ⛔ 若 P1a 平 ⇒ **「输入构造」在重写通道上真正关闭**（`state`/`history`/`blocked`/`rejected` 四块全不是钉子）。
  那时断点只剩**输出协议**（`propose()` 的 plan JSON）或**模型对目标的确定性**——那是下一轮的问题。
- ⛔ 若 P1a 降而 P2 平 ⇒ 重写换路了但没变对 ⇒ 下一个问题是**质量**，不是多样性。
- ⛔ 不许说「模型会自己改错」：本轮只测输出**是否变化**，变 ≠ 变对。
- ⚠️ 本轮**没有摘掉 `banned` 提示**（只脱敏）："哪些动作试过"这个事实仍然给，被删的只有正文。
  ⇒ 若 `same` 仍为 16，则说明**旧答案正文不是钉子**，不是"提示被删导致打转"。
- 许撤不许藏：若 P1a 平，开关留默认关，该线结束。

## 5 产物

`bench/evidence/v6_s165/`：`PREREG.md`（本文）、`run_v76_rc4.sh`、`analyze_v76.py`、
`runs_v76_r{1..8}.jsonl`、`core_r{1..8}.log`、`timeline.txt`、`analyze_v76.out`、`gate_v76.txt`、`RESULTS.md`。

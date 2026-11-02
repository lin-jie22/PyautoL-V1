# V46 预注册 —— `rescued` 拆两档：一次「救了场」不该被记成「没救」

写于 2026-11-02（本轮开工前，**在任何代码改动之前**）。
上一轮 V45（§111）量到一个反常识读数：**B 臂的 PASS 完全靠让位**
（`run_shell` 是第 5 个候选、真写出 `report.md` 44 字节），
而台账里 `rescued=0`。

> 一个「救了场却记成没救」的计数器，比没有这个计数器更危险 ——
> 它会让人把"机制有效"读成"机制无效"，从而去修一个没坏的东西。

---

## 0. 这一轮**只**做一件事

把 `failover.rescued` 拆成两档，并保住旧读数的含义：

| 键 | 语义 | 与旧 `rescued` 的关系 |
|---|---|---|
| `rescued_answer` | 让位后**交付动作**成功（`_produces` 为真） | **等于**旧的 `rescued` |
| `rescued_step` | 让位后**非交付动作**成功（推进了任务但没交付） | 新增，旧版全记成 0 |
| `rescued` | 兼容字段 = `rescued_answer + rescued_step` | 旧读者（AZ105 / 打印行）不必改 |

**不改任何执行路径**：只加两个计数器与一条打印。行为一格不动。

## 1. 为什么要拆（V45 的原始读数）

```
A 臂（无 shell）：used=1  rescued=1  ⇒ 交付被救回，但内容是**编造的表** ⇒ verdict=FAIL
B 臂（有 shell）：used=1  rescued=0  ⇒ 而 PASS 完全靠它
```

B 臂的轨迹（`v6_s135/p2_real.txt`）：第 1 轮 5 个候选
`{code_agent:4, run_shell:1}`；`code_agent` 在这道题上**必败**
（`external/code_agent.py` 只认「函数名必须是 X」，题面里没有）；
`run_shell` 成功写出 `report.md`，但它**不是交付动作** ⇒
`if _produces:` 那一支根本没走到 ⇒ `rescued` 恒 0。

⇒ 这不是"机制没通电"，是**判据漏了一档**：
`rescued` 判的是"这一轮产生了交付答案"，不是"任务被推进了"。

## 2. 判据（预注册，⛔ 读到之后不许改）

### J1 离线两景 —— 分母 = 同一份 `Core`、零模型、两套候选

| 景 | 候选 | 期望 |
|---|---|---|
| D（交付救回） | `finish(坏答案)` → `finish(好答案)` | `used=1`、`rescued_answer=1`、`rescued_step=0` |
| S（步骤救回） | `finish(坏答案)` → `recall(查询)` | `used=1`、`rescued_step=1`、`rescued_answer=0` |

**押注：成立。** 且 S 景必须**真的 dispatch 了 `recall`**（不是只加了个数）。

### J2 守恒 —— 分母 = 两个离线景 + 单候选景

`rescued == rescued_answer + rescued_step`（三景全成立）。
单候选景（无弹药）⇒ 两个子计数都 **0**（开关不许泄漏）。

### J3 真实复跑 B 臂（有 shell）—— 分母 = 与 V45 逐字相同的题面/环境

**押注**：`rescued_step >= 1`，`rescued_answer == 0`，`verdict` 仍是 `PASS`（`report.md` 有）。

⛔ 若 `rescued_step == 0`：如实登记为**押错**，并给出原始轨迹
（第 1 轮每个候选的 `ci`、`tool`、`ok`、`_fo_pending`）——
那说明 `used=1` 记的**不是**我以为的那次失败，`rescued` 的口径还要再改。

### J4 真实复跑 A 臂（无 shell）—— 旧读数含义不变

**押注**：`rescued_answer == 1`、`rescued_step == 0`（V45 记 `rescued=1`）。
⛔ 拆档**不许**改变 A 臂的判决：`verdict` 仍是 `FAIL`。

### J5 不改行为 —— 分母 = V44 八景 + V45 五景探针

两份旧探针改动前后输出**逐字节相同**（sha 不变）。
这是"读数改动"的唯一自证方式；⛔ 不许用"我只加了两个 `+= 1`"当理由。

### J6 撤回态精确红

新增 AZ109 的撤回语义 = 删掉 `rescued_step += 1` 那一行 ⇒ **只有 AZ109 红**。
多处红 = 无（纪律 4）。

### J7 判决入口不被过程量混入

`rescue_rate` 的分子**仍然是 `rescued_answer`**（= 旧 `rescued`），
⛔ 不许变成 `rescued_answer + rescued_step` —— 否则"救回一个中间步骤"
会被算成"救回一次交付"，那正是 V39 `hit_rate` 栽过的分母病。

## 3. 明确**不做**的

- ⛔ 不动 `cand_failover` 的触发条件（只对交付动作让位那一档还留着 —— 那是第 5 项 `cand_failover=2`）。
- ⛔ 不动 `_produces` 的定义、不动自检、不动 `max_candidates`。
- ⛔ 不把新字段写进 prompt / 语义记忆 / 打分器。
- ⛔ 不跑 96 格（读数不进 prompt）。
- ⛔ 不去修第 2 项「外部能力不占宽度」—— 本轮只把**读数**对齐，
  否则改完说不清"读数变了"是机制变了还是数法变了。

## 4. 预期会被打脸的地方（先写下来）

1. **`used` 可能记的不是我以为的那次失败。** B 臂第 1 轮有 4 个 `code_agent`，
   若它们各不相同且依次失败，`used` 应该是 4 而不是 1 —— 实测是 1，
   说明中间有 3 个走的是**别的出口**（`attempted_sigs` 重复闸 / guard 拦 / 预演否决）。
   若真如此，`rescued_step` 的分母（`used`）本身就是个混合量。
2. **`rescued_step` 可能一次都不触发**：若 `run_shell` 成功前 `_fo_pending` 已被重置，
   或 `run_shell` 在第 1 轮之外的轮次执行，这条就记不上。
3. A 臂复跑可能**不复现** `rescued=1`（模型非确定性，即便 `T=0` 也可能是跨时段不同）。
   那时只能说"本时段两臂都只跑了 1 次，不构成稳健性"。

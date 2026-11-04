# V62 预注册 —— 「打回 → 重写」这条边：报错原文活过「让位成功」的清零

> 预注册写在**任何一次本臂跑批之前**。下面的预测值只从**已冻结**的产物推：
> ① `bench/evidence/v6_s151/RESULTS.md` + `arm_b_cli.txt`（V61 的 B 臂：`V1_VERIFY_EXEC=1`）
> ② `bench/evidence/v6_s151/arm_a_cli.txt` …… 本轮**不用**（它是 `VERIFY_EXEC=0`，口径不同）
> ③ `/tmp/v62probe/state.jsonl`（本轮开跑前的**单题探针**，真实模型、V61 配置、
>    `--state-dump`；t04 一题一 rep）
> ④ `bench/tasks/coding_small.jsonl` 的 `prompt` 字段（题面，⛔ 不是 `tests`）
>
> ⛔ 全部预测值都标了"这是哪一轮的产物"。⛔ 不押"再加一层验证"（V61 已判完）。

---

## 0. 本轮改了什么（以及**为什么不是**原计划那一条）

### 0.1 原计划（V61 §127.9 第 1 项）

> ① 打回后不许让位给非交付动作；② 重复闸对被打回的交付放行一次；③ 若重提仍逐字相同 ⇒ 换载荷。

### 0.2 探针把这个计划**改了一半**（这是本轮最重要的一条前置读数）

单题探针（t04，真实模型，`--state-dump`）落出的 `state.jsonl` 里，**`it=3` 与 `it=4` 的 state 含**：

```
[工作记忆] finish ⇒ ✘ [工具失败] 自检未通过：1/6 项未过：题面示例未通过：
           TypeError: 'tuple' object does not support item assignment
```

⇒ **报错原文早就在 prompt 里了**，通道是 `[工作记忆]`（`mw/core.py:1610`，`capture` + `think`）。
所以：

| 原假设 | 读数 | 判决 |
|---|---|---|
| 「模型没看到，所以没改」 | state 原文含 `自检未通过` + `TypeError` | **否证** |
| 「载荷送不到 ⇒ 要修通道」 | 通道早通 | **不成立，不许当成本轮的主张** |

因此本轮**不主张**"修好了载荷"。本轮只主张一件更窄的事：

> **同一条信息，在打回之后**能不能出现在**重规划那个位置**（`replan` 的 `failed` 行），
> 且**只出现一次**；以及它出没出现，**模型改不改**。

### 0.3 本轮的改动清单（都进了 `Limits`/账本，可开可关）

| 位置 | 改动 | 性质 |
|---|---|---|
| `mw/config.py` | `Limits.reject_carry`（`V1_REJECT_CARRY`，默认 0） | **唯一分臂变量** |
| `mw/core.py` | `_gate_finish` 打回时留 `_reject_carry`；规划处 `elif _carry:` 走 `replan(error=…)`，用掉即清 | 被测量 |
| `mw/core.py` | 重复闸：`_is_delivery and _rejected_once` ⇒ `same`/`redelivered`/`dup_skipped` | **纯记账**（补 V61 的漏斗第四格） |
| `mw/core.py` | `rework_stats`（6 键）+ `stats["rework"]` | 纯读数 |
| `run.py` | `kernel_rework`（零点 8 键 / 落盘 9 键，派生 `rework_gap`） | 纯读数 |
| `tests/` | `AZ124`（十条）+ `_keys` 登记 + P5 锚 26→27 + **AZ63 喂入点 2→3** | 量具 |

⚠️ **记账那一条会改行为吗？** 不会：它只在一个 `if` 里加计数与一行日志，
被判为重复的动作**本来就不执行**。但它是**两臂共有**的（与开关无关）⇒
它不能解释两臂差异，只能解释"读数为什么比 V61 更全"。

---

## 1. 要回答的问题

| 记号 | 问题 | 读哪一格 |
|---|---|---|
| **Q1 送达位置** | 开 `reject_carry` 后，报错原文进没进**重规划位置**？进了几次？ | `rework.carried`；日志 `↻ 打回报文进下一轮提问` |
| **Q2 这条边存在吗** | 打回之后**又交了一次**吗？（V61 是 0） | `rework.redelivered` |
| **Q3 模型改不改** | 重交的那一份与被打回那版**逐字相同**吗？ | `rework.same` / `rework.diff` / `rework.passed_after` |
| **Q4 分数** | 三值：升 / 平 / 降 | 两臂 `mean` + 逐题 `score` |

---

## 2. 分母（三选一，必须写理由）

**本轮全部因变量按「次」计，分母是 `verify.failed`（打回次数），不是题。**

理由（来自 V61 冻结产物，不是推测）：
- V61 B 臂 `verify.failed=4`，**全部落在 t04** 的两 rep（每 rep：主循环 1 + 收尾 1）；
- 6 题里只有 t04 会触发示例判据失败 ⇒ 按「题」算分母只有 1（甚至 0.5），
  一格的抖动就能把 `same` 从 0 变成 100%；
- 而 `carried/same/diff/redelivered` 的语义本身就是**事件**，不是题。

⇒ 预期分母：A 臂 `failed=4`（V61 同一配置）、B 臂 `failed≥4`（若重写也不对，可能更多）。
⚠️ 因此 `same/diff` 的读数上限是 4 左右，**不是 12**。

---

## 3. 预测（每条标"依据"与"落空时怎么读"）

| # | 预测 | 依据 | 落空时怎么读 |
|---|---|---|---|
| **P1** | `rework.carried`：A=**0**、B=**≥2** | 机制只在 `carry=1` 且 `_gate_finish` 打回时置位；V61 冻结产物里 t04 两 rep 各打回 1 次 ⇒ ≥2 | B=0 ⇒ **开关没通电**（先看 `switch` 字段，再查 `_gate_finish` 是否被调用），⛔ 不许读成"位置无影响" |
| **P2** | `rework.same`：B **≥2**（照抄） | 单题探针（真实模型）同一场景下 2 次 `⏭ 重复动作已跳过: finish`，且 state 原文证明它看到了 `TypeError` | B=0 且 `diff>0` ⇒ **多给一遍/换位置真的让它改了**，那么 Q3 的答案是"改"，与探针相反，必须解释矛盾（可能是 `replan` 的 `failed` 行而非 `[工作记忆]` 起了作用） |
| **P3** | `rework.diff` = **0**、`passed_after` = **0** | 同上：探针里它看到了具体异常仍未改；V50 已判"提示词层修不了这条边" | `diff>0` ⇒ P3 落空，但这是**好**消息：说明位置/显著性有效，下一轮该加这类通道 |
| **P4** | `mean`：A=**0.8611**、B=**0.8611**（三值押**平**） | V61 B 臂 A 口径冻结产物 0.8611；本轮 B 只多一段文本，不改任何判据 | B>A ⇒ 升，需检查是不是"少跑了几轮把预算省给了交付"；B<A ⇒ 降，先查是否多耗了轮次导致 t04 提前收尾 |
| **P5** | 轮数：A=**26**、B=**26~28** | V61 B 臂 26 轮；carry 只在重规划那一次替换成 `replan`（不再走 `propose`），**调用次数不变** | B 明显更多 ⇒ 说明它开始重交（`redelivered>0`），这是 Q2 的正面证据，不是坏消息 |
| **P6** | `unaccounted`：两臂都=**4** | 本轮的 `dup_skipped` **只给那一格起名，不改 `_koff_line` 的公式** | 任一臂≠4 ⇒ 我在改记账时动了公式（回归，必须修） |
| **P7** | 两臂 `caliber_sig` **不同** | `reject_carry` 在 `Limits` ⇒ 进 `as_dict` | 相同 ⇒ 开关没进 `Limits`（V60 的 `allow_shell` 那个洞的第 4 种形态） |
| **P8** | 两臂 `kernel_rework.rework_gap` 都=**0** | 恒等式 `redelivered = same + diff`（两条记账路径各 +1 一次） | ≠0 ⇒ 有一条重交没落格（检测器正常工作） |
| **P9** | `rework.tasks_rejected`：两臂都=**1**（只有 t04） | V61 冻结产物：4 次打回全在 t04 | >1 ⇒ 有第二题开始被打回（V61 里 t03/t05/t06 零误杀），要单独看 |

⭐ **押方向押三值**：P4 明确写"平"。`≥` 同时接受"没升"和"升"，那等于没押。

---

## 4. 判据的**输入**必须来自被测对象（纪律 27）

本轮唯一用到的"判据"就是示例判据本身（V61 已落地），它的输入是 `goal`（题面）。
⛔ 我没有用编造的样本去试 `check_goal` / 正则 / 尺子。
⛔ 探针（`/tmp/v62probe`）只用来**发现**问题，不作为判分证据；它跑的是同一份题集里的 t04。

---

## 5. 不许做的三件事

1. ⛔ 不许把本轮读成"载荷送不到被修好了"——`[工作记忆]` 早就在送（§0.2 有 state 原文）。
2. ⛔ 不许因为 Δ=0 就说"这条边不值得修"：本轮要回答的是**它存不存在**（`redelivered`）。
3. ⛔ 不许顺手调 `verify_retries` / `max_candidates` / `cap` / `scope`：那会把分母一起改掉。

---

## 6. 跑批

```
bash bench/evidence/v6_s152/run_v62.sh a    # 对照：V1_REJECT_CARRY=0（= V61 的行为）
bash bench/evidence/v6_s152/run_v62.sh b    # 实验：V1_REJECT_CARRY=1
```

两臂常量（逐项相同，所以它们不是变量）：`V1_VERIFY_EXEC=1`（V61 的 B 臂口径）、
`--allow-shell`、`V1_CAND_FAILOVER=1`、`qwen2.5:7b`、T=0、`max_tokens=1024`、
`max_steps=12`、`state_chars=2400`、`max_candidates=3`、`verify_retries=2`、
`verify_static=2`、`repeat=2`、`--no-external --no-ledger --no-answers`。

⛔ **不是**同一口径：`Limits` 多 `reject_carry` ⇒ 两臂 `caliber_sig` 必不同（P7 就是钉它）。
⛔ 与 `542f9053d19a7090` / `79b2f68ba2248816` 都不可比（断段人工声明）。

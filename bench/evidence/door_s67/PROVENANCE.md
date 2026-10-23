# door_s67 —— 「两扇门并成一扇」的 A/B（第六十七轮，2026-10-23）

## 这份证据在回答什么

第六十六轮（`attr_s66`）用同一道题、同一模型、同一把尺子量出两个数：

| 臂 | 谁把任务交过去 | 读数 |
|---|---|---|
| A | **规划器**自己从工具目录里挑中 `agent:code_agent` | `0.0000`（`stalled`，`steps=3`） |
| B | **内核**写死在第 1 步注入（`V1_EXTERNAL_ANSWER_TOOL`） | `1.0000`（`done`，`steps=1`） |

死因写在日志里：门外 agent 的契约是「从题面原文里抽『函数名必须是 X』，抽不到就不猜」。
门 B 交的是题面**原文**；门 A 交的是规划器**改写过的**话 ⇒ 它认不出来 ⇒ 拒绝作答。
**它拒绝得对** —— 错的不是它，是"同一个能力有两扇门，其中一扇把要办的事说错了"。

⇒ 本轮修法：把入参收成**一处** —— `mw/core.py` 的 `answer_task()`。
任何 `produces_answer=True` 的候选，在 `dispatch` **之前**一律被覆写成
`{"task": 题面原文, "deadline_s": 现算}`（`deadline_s` 一起归一化，因为旧门 A
不带它 ⇒ 会退回 `spec.timeout_s` 这个**上限**，而不是预算）。

本文件是那次修复的**行为级判据**：同一道题、同一模型、同一把尺子，
**A 臂现在必须和 B 臂读出一致的数**。

## 配置

- 套件：`suite_one_t05.jsonl`（`bench/tasks/coding_small.jsonl` 里的 1 条 `t05_top_k`）
  - sha256 = `9011263b697350781b5bde013af889191af39fc75aaa878d4a96bc99e6363a51`
    （与 `attr_s66/suite_one_t05.jsonl` **逐字相同** —— 所以两轮的读数可以放在一起看死因）
- 模型：`qwen2.5:7b`（本机 Ollama，`http://127.0.0.1:11434/v1`），`temperature 0`
- 预算：1x，`--fresh`（每题新建内核 + 记忆隔离）
- 打分器：`code_exec`（`scorer_sha=d4b1cbbfd7f1`，`suite_sha=75d537b98d7b`）

| 臂 | 只差一个环境变量 | 文件 |
|---|---|---|
| **A** | 不设 `V1_EXTERNAL_ANSWER_TOOL` ⇒ 外部能力由**规划器自己挑** | `run_A_pick.log` |
| **B** | `V1_EXTERNAL_ANSWER_TOOL=agent:code_agent` ⇒ 由**内核写死**注入 | `run_B_cfg.log` |

## 读数（两臂**一致** —— 这就是判据）

| | A（规划器自选） | B（内核注入） |
|---|---|---|
| 出口 | `■ done \| steps=1` | `■ done \| steps=1` |
| 分数 | `mean=1.0000` `[1.0000,1.0000]` | `mean=1.0000` `[1.0000,1.0000]` |
| 账目 | `attempted=1 recorded=1 skipped_no_answer=0` | 同左 |
| 可信 | ✅ | ✅ |
| 外部段 | `外部[agent:code_agent] 调=1 成=1 败=0 采用=1 559字 (规划器自选，未配置) 份=2→2 用例=5` | `外部[agent:code_agent] 调=1 成=1 败=0 采用=1 559字 份=2→2 用例=5` |
| 交接意图（日志） | `使用 collections.Counter 统计词频，然后排序处理` ← **规划器写的** | `整题交给外部能力` ← **内核写的** |
| 退出码 | `EXIT_A=0` | `EXIT_B=0` |

★ **最硬的一格不是分数，是 `559字 份=2→2 用例=5` 两臂逐字相同。**
  外部 agent 是**生成式**的，输入只要有一个字不同、那 559 字的输出就不可能一样。
  ⇒ 这一格直接证明"交过去的是同一份东西"，不依赖我对日志的解读。

## 与上一轮同一题的对照（`attr_s66` → `door_s67`）

| 臂 | 第六十六轮（未修） | 第六十七轮（已修） |
|---|---|---|
| A | `■ stalled \| steps=3`，`mean=0.0000`，可信 ❌，`skipped_no_answer=1` | `■ done \| steps=1`，`mean=1.0000`，可信 ✅ |
| B | `■ done \| steps=1`，`mean=1.0000`，可信 ✅ | 同上（未变） |

⚠️ 两次 A 的**差别只有内核代码**（`answer_task()` 归一化），模型、题、尺子、预算、
`--fresh`、温度全部相同；`temperature=0` 且同机同模 —— 但**不要把"同分"读成
"确定性"**：本仓 `7z` 已量化过 T=0 的翻面率（147 次重复请求里 1 次不同）。
这一格的说服力来自 `559字` 逐字相同与 `■ done` 的出口，不来自"温度为零"。

## ⛔ 三条禁令（给未来的自己）

1. **别把这 1.0000 读成"内核自己的能力"**。整题是交给 `external/code_agent.py` 做的，
   内核只负责"把任务交对"。能力读数是 `loop_s6x` 那几条，不是这里。
2. **别把这一条当成"外部能力变强了"**。变的是**交接**：门 A 的 `task` 从
   规划器改写版变成题面原文。上一轮同一个 agent 在同一题上就是 1.0000。
3. **两次运行不可互换**：A/B 的 `limits` 不同（外部能力的注入位置不同）⇒ `calib.sig`
   不同，**不许**把它们互相当作前后对照；本文件只用来判"两扇门是否同源"。
   能力前后对照必须是同口径，见 `loop_s65` / `loop_s66`。

## 复现

```bash
# A（不配置 ⇒ 规划器自选）
V1_LLM_BASE_URL=http://127.0.0.1:11434/v1 V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b \
  V1_STATE_DIR=/tmp/v1_az60_a/state python3 run.py --bench --solver llm --fresh \
  --temperature 0 --suite bench/evidence/door_s67/suite_one_t05.jsonl
# B（只多一个变量 ⇒ 内核注入）
V1_EXTERNAL_ANSWER_TOOL=agent:code_agent …（其余同上，STATE_DIR 换成 /tmp/v1_az60_b/state）
```

## 校验

`FILES.sha256` 是本目录四个文件的 sha256；在本目录内 `shasum -a 256 -c FILES.sha256` 可核对。

# attr_s66 —— 失败归因的 A/B（第六十六轮，2026-10-23）

## 这两个文件在回答什么

第六十四/六十五轮两批真实曲线里，`agent:code_agent` 共 **21 次 `ok=False`**，
而日志每一行只有 `ok=False` + 意图 —— **零条原因**。汇总行里连「外部」这一段都不出现
（`grep -c "外部[" = 0`），因为旧判据是「**配置了**外部能力才印」。
⇒ 21 次失败在证据里看起来像没发生过。

这一批只做一件事：**让同一道题的死因变成一行可读的字**，并且用 A/B 把
「是谁把任务交过去」这个变量单独拎出来。

## 配置

- 套件：`suite_one_t05.jsonl`（从 `bench/tasks/coding_small.jsonl` 抽出 **1 条** `t05_top_k`）
- 模型：`qwen2.5:7b`（本机 Ollama，`http://127.0.0.1:11434/v1`），`temperature 0`
- 预算：1x，`--fresh`（每题新建内核 + 记忆隔离）
- 打分器：`code_exec`（`scorer_sha=d4b1cbbfd7f1`）

| 臂 | 只差一个环境变量 | 文件 |
|---|---|---|
| **A** | 不设 `V1_EXTERNAL_ANSWER_TOOL` ⇒ 外部能力由**规划器自己挑** | `run_A_unconfigured.log` |
| **B** | `V1_EXTERNAL_ANSWER_TOOL=agent:code_agent` ⇒ 由**内核写死**在第 1 步注入，`task` 传**题面原文** | `run_B_configured.log` |

## 读数

| | A（规划器自选） | B（内核注入） |
|---|---|---|
| 汇总行外部段 | `外部[agent:code_agent] 调=1 成=0 败=1 采用=0 0字 (规划器自选，未配置) ⚠️1败/1种: …` | `外部[agent:code_agent] 调=1 成=1 败=0 采用=1 559字 份=2→2 用例=5` |
| 出口 | `■ stalled | steps=3` | `■ done | steps=1` |
| 分数 | `mean=0.0000`（`skipped_no_answer=1`，`可信=❌`） | `mean=1.0000`（`recorded=1`，`可信=✅`） |
| 墙钟 | 13:32:17 → 13:32:52 = **35s**（预算 120s，**还剩 85s**） | 13:32:53 → 13:33:30 = **37s** |
| 退出码 | `EXIT_A=1` | `EXIT_B=0` |

## 唯一的原因（现在它写在日志里）

```
↳ agent:code_agent ok=False 原因=agent:code_agent 执行异常 RuntimeError:
  外部 agent 未交付：题面里没有『函数名必须是 X』⇒ 本 agent 不适用（不猜）
```

B 臂同一行是 `↳ agent:code_agent ok=True`。

差别**不在能力上**，在**交接方式**上：
- B 把 `{"task": goal}` 原样交过去，题面里有「函数名必须是 top_k」⇒ agent 认；
- A 是规划器挑的，它先把题面**改写成自己的话**
  （`实现 top_k 函数，输入一个字符串列表，返回出现次数最多的前…`）⇒ agent 认不出来，
  **拒绝作答**（拒绝得对：`external/code_agent.py` 的契约是「不猜」）。

## ⛔ 三条禁令（给未来的自己）

1. **别把 A 的 0.0 读成「模型做不到」**。B 用同一模型、同一题、同一尺子拿了 1.0000。
2. **别把 A 的 stalled 读成「预算不够」**。它 35 秒就退了，`steps=3`，
   剩 85 秒一次都没再试别的路径（`⏭ 重复动作已跳过` ×2 → `🧊 停滞 2/2`）。
3. **两次运行不可互换**：A 的口径与 B 的口径不同（`外部能力 1 个` 的注入位置不同），
   本文件只用来**定位死因**，不是能力读数。能力读数在 `loop_s65` / `loop_s66`。

## 复现

```bash
# A
V1_LLM_BASE_URL=http://127.0.0.1:11434/v1 V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b \
  V1_STATE_DIR=/tmp/v1_attr_a/state python3 run.py --bench --solver llm --fresh \
  --temperature 0 --suite bench/evidence/attr_s66/suite_one_t05.jsonl
# B（只多一个变量）
V1_EXTERNAL_ANSWER_TOOL=agent:code_agent …（同上）
```

## 校验

`FILES.sha256` 是上面三个文件的 sha256；`shasum -a 256 -c FILES.sha256` 在本目录内可核对。

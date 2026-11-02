# V46 结果 —— `rescued` 拆两档

对应 `PREREG.md`（写于任何代码改动之前）。日期 2026-11-02。

## 一句话

**V45 那句「B 臂的 PASS 全靠让位却记成 `rescued=0`」在这一轮被证实并修正：
它记成了 `rescued_step=1`。** 两档互不冒充，行为一格未动。

## 判据逐条

| 判据 | 押注 | 实测 | 判决 |
|---|---|---|---|
| J1 离线两景 | D: answer=1/step=0；S: step=1/answer=0 | 相同；S 景真 dispatch 了 `recall` | ✅ |
| J2 守恒 + 无弹药 | `rescued == answer+step`；单候选两档全 0 | 三景全成立；ONE: 0/0（`no_more=1`） | ✅ |
| J3 真实 B 臂 | `rescued_step>=1`、`rescued_answer=0`、verdict 仍 PASS | **step=1 / answer=0 / verdict=PASS** | ✅ |
| J4 真实 A 臂 | `rescued_answer=1`、`rescued_step=0`、verdict 仍 FAIL | 1 / 0 / FAIL | ✅ |
| J5 不改行为 | V44 八景 + V45 五景探针逐字节相同 | `a8dbe298…` / `cba10e13…` 均未变 | ✅ |
| J6 撤回态 | AZ109 只它红 | `红=['AZ109'] 多红=无 Traceback=无` | ✅ |
| J7 判决入口 | `rescue_rate` 分子仍是 `rescued_answer` | A(1/2)=0.5、B(0/2)=0.0 | ✅ |

## 真实两臂（`p2_real.txt`，唯一变量 `--allow-shell`）

| 读数 | A 无 shell | B 有 shell |
|---|---|---|
| `status` / `verdict` | `done` / **FAIL** | `stalled` / **PASS** |
| `failover.used` | 1 | 1 |
| **`rescued_answer`** | **1** | **0** |
| **`rescued_step`** | **0** | **1** |
| `rescued`（兼容字段） | 1 | 1 |
| `plan.rounds` / `cands_total` | 2 / 3 | 3 / 7 |
| `plan.by_tool_max_round` | 1 | **4** |
| `plan.produces_by_round` | `[0,2]` | `[4,1,1]` |

⭐ 两臂的 `plan` 读数与 V45 **逐字段相同** ⇒ 这一轮确实是"同口径复跑"，
变的只有那两个新计数器。这不是巧合，是"读数改动不许动行为"的可观测形态。

## B 臂轨迹（`logs_b_shell.txt`，第 1 轮 5 个候选）

```
[1] 通道=深 候选=5
    ↳ agent:code_agent ok=False 原因=…『函数名必须是 X』⇒ 本 agent 不适用（不猜）
    ↪ 交付被打回 ⇒ 让位给候选 2/5（不重规划，直接用已有的）
    ↳ run_shell ok=True
    ↪ 让位救回一个**步骤**（非交付动作）：run_shell      ← 这一行是新的
[2] 候选=1  ⏭ 重复动作已跳过: agent:code_agent  ⇒ 🧊 停滞 1/2
[3] 候选=1  ⏭ 重复动作已跳过: agent:code_agent  ⇒ 🧊 停滞 2/2 ⇒ 转向交付
```

## 押错的地方（如实登记）

1. **PREREG §4.1 押「`used` 应该是 4 而不是 1」—— 押错。**
   真实轨迹只 dispatch 了 **2** 个候选：第 1 个 `code_agent` 失败、第 2 个
   `run_shell` 成功 ⇒ `executed=True; break`，剩下 3 个 `code_agent`
   **一次都没进执行器**。`used=1` 是对的，不是丢失计数。
   ⇒ 我的推断错在把"4 个候选都失败过"当成了已知事实：它们只是**没被尝试**。
   这与 V45 §111.3 的发现同宗（日志只打被执行的候选，看起来像"少了一个"）。

2. **A 臂第 1 轮又被判"截断"，而 `plan.truncated_total` 仍是 0。**
   日志：`⚠️ 模型输出被**截断**（⇒ 调大 max_tokens，当前 1024）；原文片段:
   '[i]}\' sales.csv | sort -nr -k2 > report.md", "timeout_s": 10}, …]}'`
   ⇒ 尾部的 JSON 其实是**闭合**的（`}]}` 结尾），而 `truncated_total`
   只统计 `plan.steps[:n]` 的截断 —— **`_looks_truncated` 判出来的截断不进这个数**。
   这是两个不同的"截断"，共用一个词。V46 第 3 项要修的正是这里：
   判截断时留头 + 留长度，别让"分类器说截断"与"读数说没截断"并存。

3. **`verdict=PASS` 只回答"东西在"。** B 臂 `report.md` 44 字节，内容是
   `华北 2700 / 华南 2000 / 华东 700 / 区域 0` —— 最后一行是 awk 表头被当成数据
   （V43/V44/V45 逐字相同）。按内容判仍是 **PARTIAL**。

## 契约与撤回态

- 契约 **AZ109**（608→**609**）：六条判据，见 `tests/contract_test.py`。
- 撤回态 **AZ109**（194→**195**）：删掉 `rescued_step += 1` 两行 ⇒ 只 ② 红。
  ⭐ ③（守恒）**仍绿** —— 两档同时归零时守恒当然成立。
  ⇒ **守恒是必要条件，不是通电证明**（V44 的 `status=done` 永远自洽，同宗）。

## 这一轮**没有**做的事

- ⛔ 行为一格未动（J5 两把旧探针逐字节相同）。
- ⛔ 没跑 96 格；两个新字段不进 prompt、不进打分器、不进 `answers.jsonl`。
- ⛔ 没修第 2 项（外部能力不占宽度）—— 先让读数对齐，否则改完说不清
  "读数变了"是机制变了还是数法变了。

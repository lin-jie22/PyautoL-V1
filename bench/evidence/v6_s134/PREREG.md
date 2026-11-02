# V44 预注册 —— 「状态说实话」：给交付物加一道**产物验收**

> 冻结时刻：2026-11-02 14:10（**在写任何一行实现之前**）。
> 冻结时的**已知**读数（V43，`RESULTS.md` / `logs_a` / `logs_b` / `p5_afterfix`）：

| 臂 | 命令行的 `--allow-shell` | 状态行 | 产物 `report.md` | 产物内容 |
|---|---|---|---|---|
| A 12:56 | 有 | `stalled` | **不存在** | — |
| B 12:58 | 有 | `done` | **不存在** | — |
| C 13:13 | 有（且接线已修） | `stalled` | **存在 44 字节** | 三个数字全对，格式不合要求 |

⇒ 已知的两个谎：**`done` 而产物不存在**、**`stalled` 而产物正确**。

---

## 0. 这一轮**不**碰什么（写死，防止顺手改坏）

1. ⛔ 不改主循环任何分支、不改候选、不改 `max_candidates`、不改提示词。
2. ⛔ 不改 `memory.record("finish", …)` 的**内容**（它进 `[语义记忆]` ⇒ 改它等于改
   下一轮的 prompt ⇒ 96 格不可比）。⇒ **本轮 `Caliber.signature()` 必须逐字不变**。
3. ⛔ 不新增 `Limits` 字段（那会动 `Caliber`、动 P5 锚、断所有历史段）。
4. ⛔ 不动 `answers.jsonl`、不动打分器、不动题集。

## 1. 唯一改动面（四处，全部是**读数**）

| 位置 | 改动 |
|---|---|
| `mw/verify.py` | 新增 `expected_artifacts()` / `check_delivery()`：从**题面原文**抽约定产物，在**工作目录**里核 |
| `mw/core.py` | `Outcome` 加 `verdict` 字段；`_finish` 算一次并进 `stats["delivery"]`；`■` 行加 `验收=` |
| `run.py` | `cmd_run` 打印 `verdict`，**退出码由 verdict 决定**（不再由 `status` 决定） |
| `tests/contract_test.py` | AZ107 + 撤回态 |

## 2. 判据（**先写死，不许事后调**）

从题面里抽文件型 token（`[\w./\-]+\.(md|csv|tsv|json|jsonl|txt|py|html|yaml|yml|log|xlsx)`），
按 token **前面 12 个字符里有没有写动作**分成两类：

- **产物**（output）：前置窗口含 `写入|写到|写进|存到|保存|生成|输出|导出|创建|落盘|放到|放进`
  （英文 `write|save|create|output|generate`）
- **输入**（input）：其余

⛔ 这条分类就是本轮的**核心设计**：不分类的话，"找到了输入文件 `sales.csv`"会被记成
"部分完成"，而用户真正要的东西一个字都没有。V43 的 B 臂正好是这一形态。

判定表（**穷尽五值**，无"其他"）：

| 条件 | verdict |
|---|---|
| 有产物 ∧ 全部存在 | `PASS` |
| 有产物 ∧ 部分存在 | `PARTIAL` |
| 有产物 ∧ 一个都没有 | `FAIL` |
| 无产物 ∧ 答案非空 | `ANSWER` |
| 无产物 ∧ 答案为空 | `EMPTY` |

`NONE` 故意**不存在**：它会被读成"成功"。`ANSWER`/`EMPTY` 明说"没约定产物，只验了答案文本"。

`run.py` 退出码：`0 ⇔ verdict ∈ {PASS, ANSWER}`。

## 3. 离线探针的**预测**（跑之前先写死；错就认）

六景，**真 `Core`、零模型**（`StubLLM`），工作目录是临时目录：

| 景 | 造的场景 | 预测 |
|---|---|---|
| S1 | 题面"写入 report.md"，文件不存在，有答案 | `FAIL` |
| S2 | 同上，文件存在 | `PASS` |
| S3 | 题面无文件 token，答案非空 | `ANSWER` |
| S4 | 题面无文件 token，答案空 | `EMPTY` |
| S5 | 题面"生成 a.md 和 b.json"，只有 a.md | `PARTIAL` |
| S6 | 题面只有输入 `sales.csv`（存在），无写动作 | `ANSWER`/`EMPTY`，且 `inputs_missing=[]` |

## 4. 真实任务的**预测**（三个已知现场，逐条押）

| 现场 | 题面抽出的产物 | 预测 verdict | 若押错说明什么 |
|---|---|---|---|
| A 12:56（`stalled`、无产物） | `report.md` | `FAIL` | 抽取漏了"写入…的 report.md" |
| B 12:58（`done`、无产物） | `report.md` | **`FAIL`** ← 与 `status=done` **相反**，这就是修好的那件事 | 抽取把 `sales.csv` 当成产物 ⇒ 会变 `PARTIAL` |
| C 13:13（`stalled`、产物 44 字节） | `report.md` | **`PASS`** ← 与 `status=stalled` **相反** | 我的 `_ARTIFACT_RE` 认不出中文路径旁的 ASCII 名 |

## 5. 押错的处置（**事先声明**）

- 若 B 被判成 `PARTIAL`：说明分类器把输入当产物 ⇒ **判据错**，改分类器，
  **不许**改预测去迁就读数。
- 若 C 被判成 `FAIL`：说明抽取漏 token ⇒ 同样是判据错。
- 两种情形都要把**原样输出**留在 `p1_probe.txt` 里，不许只留结论。

## 6. 必须同时交出的**负结果**

`expected_artifacts()` 抽不出来 ⇒ 这一整层是**装饰**。所以必须报：

- v8 六道题（`bench/specgen/tasks_v8.jsonl`）抽出的产物数 —— 已知 **0 个/题**
  （题面是纯函数补全，0 个文件 token）⇒ 六道题**全部** `ANSWER`/`EMPTY`，
  96 格**一格不动**。这是本轮"不改尺子"的自证。
- 若某题抽出产物 ⇒ 那一题的 verdict 会变成 `FAIL`/`PASS`，**必须登记为噪声源**。

## 7. 主线的诚实回答（先写，免得事后圆）

本轮 **不**让累积循环更近一步：它是读数修复。
**它服务主线的唯一方式是**：`status` 现在会被写进语义记忆
（`memory.record("finish", f"{status}: …")`），一个"自称 done 而没产物"的
成功标记会作为先例回灌。验收层先在**判决**上留下真相，至于要不要把它写进记忆
（那会改 prompt），**单开一轮**，不在本轮偷渡。

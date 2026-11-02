# V47 预注册：外部能力不适配时不许占宽度

冻结于动手之前。任何读数出来之后补写的判据，一律标 `POST-HOC` 并单独列，不进判决。

---

## 0. 这一轮要回答的问题

V45 量到一个数：B 臂第 1 轮 `cands=5`，`by_tool = {code_agent:4, run_shell:1}`，
`by_tool_max_round=4`。其中 **4 个 `agent:code_agent` 全部必败** ——
它自己报的拒答理由是「题面里没有『函数名必须是 X』⇒ 本 agent 不适用（不猜）」。

V45 给的结论是「**有效宽度 = 2，`n=5` 是假宽**」。

**这一轮问的是**：把这 4 个**结构上不适用**的候选从宽度里去掉，
① 宽度读数会不会回到真实值；② 去掉之后**还剩什么**；
③ 交付会不会因此退化。

⛔ 这一轮**不改提示词**、**不动 `max_candidates`**、**不加 `Limits` 字段**
（后者会动 `ENV_KEYS`/`ZERO_OK_KEYS`/P5 硬锚 `len(fields)==25`，是另一件事）。

---

## 1. 装置与单变量

| 项 | 取值 |
|---|---|
| 内核 | 同一份 `mw/*.py`（本轮的改动） |
| 题面 | 与 V45/V46 逐字相同（`sales.csv` 区域合计 → `report.md`） |
| 模型 | `qwen2.5:7b` / `T=0` / `max_tokens=1024` / `cand_failover=1` |
| 唯一变量 | `agents.json` 里 `code_agent` **有没有** `applies_regex` |
| A 臂（off） | `agents.json` 无该字段 ⇒ `applies=None` ⇒ 恒适用（= V45/V46 的行为） |
| B 臂（on） | `agents.json` 有该字段 ⇒ 题面不匹配 ⇒ 不注入、不占宽度 |

⚠️ **断段声明**：内核改了（采纳点新增过滤 + 新出口），
`caliber_sig` **不变**（`applies` 不进 `catalog()`，见 §4）。
⇒ 与 V45/V46 的读数**不可逐格相减**，必须用本轮 A 臂作基线。
这是「改了旋钮、读数变了、签名没变 = 静默不可比」那条纪律的**执行方式**：
既然签名不会自己变，就必须人工声明并自带基线。

---

## 2. 「塌」的精确定义（判据的分母先写死）

⛔ 不许用 `cands_total` 单独判好坏 —— V45 已经证明它会被"名义上的候选"喂胖。
本轮的分母是 **`usable`**：`cands` 里 `applies` 判为真（或工具没声明适用条件）的个数。

| 词 | 定义 |
|---|---|
| `usable` | 本轮**真能跑**的候选数 |
| `produces` | 本轮名义交付动作数（`finish` 或 `produces_answer=True`） |
| **塌** | `usable` 降到 **0**，或交付 `verdict` 从 PASS 掉到非 PASS，或产物与 V43~V46 不再逐字相同 |

⭐ `cands_total` **预期会下降**（去掉的就是它的一部分），
下降本身**不是**判据，也**不是**退步 —— 判据看的是 `usable` 与交付。

---

## 3. 判据（全部预先写死，逐条可红）

| # | 判据 | 红的样子 |
|---|---|---|
| **J1** | B 臂 `plan.by_tool_max_round` **必须 < A 臂**（预期 A=4、B≤1） | B 仍 ≥ 4 ⇒ 过滤没通电 |
| **J2** | B 臂 `plan.dropped_not_applicable` **> 0**，且 `dropped_na_by_tool` **只含 `agent:code_agent`** | 误杀 `run_shell`/`finish`/`recall` ⇒ 判据写宽了 |
| **J3** | A 臂 `dropped_not_applicable == 0` 且 `skipped_not_applicable == 0`（开关不泄漏） | A 臂非零 ⇒ 默认路径被改了 |
| **J4** | 两臂 `verdict` **都是 PASS**，且 `report.md` **44 字节、与 V43~V46 逐字相同** | 任一臂掉 ⇒ 删宽度换来了退化 |
| **J5** | B 臂 `run_shell` **仍被执行且 ok=True**（不因过滤而消失） | 消失 ⇒ 过滤把唯一的真能力也吃了 |
| **J6** | 离线探针：同一组输入，`applies=None` 的臂逐字节 == 改动前的旧探针输出 | 变了 ⇒ 默认路径被改 |
| **J7** | `agents.json` 的 `applies_regex` 与 `external/code_agent.py::_FUNC_NAME` **对同一组题面判同**（契约钉住） | 判不同 ⇒ 两处实现漂了 |

### 3.1 预期会变、且**不算退步**的读数（先声明，免得出数后自辩）

- `plan.cands_total`：7 → 预期 **1~3**（去掉 4 个不适配的）。
- `plan.produces_by_round[0]`：`4` → 预期 **0**。
  ⛔ 这**不是**"交付能力塌了"：那 4 个名义交付动作**全是必败的**。
  名义 `produces` 与 `usable` 分列，正是 V45「有效宽度」那条的落地。
- `failover.rescued_step`：1 → 预期 **0**（`run_shell` 变成首候选 ⇒ 不需要让位）。
  ⛔ 若因此把 `rescued_step` 判成"机制退化"就错了 —— 让位没被用，是因为**不再需要**。
  判据用 J5（`run_shell` 仍执行），不用 `rescued_step` 的数值。
- `plan.rounds`：3 → 预期 **1~2**（后两轮被"全不适配"出口截断）。

### 3.2 ⛔ 明确不做的判定

- ⛔ 不说「内核变强了」：这一轮改的是**候选池的准入**，不是能力。
- ⛔ 不用分数（96 格）判这一轮 —— 题集是单函数题，`code_agent` 在**题集上适用**，
  本轮的病只在**真实任务**上现形。跑 96 格只会得到"0 差异"的假绿（V39~V42 的老账）。

---

## 4. 实现形状（先写死，防止事后改口径）

1. `ToolSpec` 加 `applies: Callable[[str], bool] | None = None`；
   **不**渲染进 `catalog_line()` ⇒ `catalog_sha` 不变 ⇒ 提示词不变。
2. `agents.json` 新增可选字段 `applies_regex`（字符串）。
   缺省 / 空 / 非法正则 ⇒ `applies=None`（**保守恒适用**：误杀比漏杀贵）。
   非法正则要留痕（`external_bad_applies`），⛔ 不许静默。
3. **两处判**（都读 `spec.applies`，不读工具名）：
   - **注入点**：`_ext` 不适配 ⇒ 不注入，`external.skipped_not_applicable += 1`；
   - **采纳点**：`cands` 里不适配的丢掉，`plan.dropped_not_applicable` /
     `dropped_na_by_tool` 记账。
4. **全不适配的出口**：过滤后 `cands` 为空 ⇒ **不是失败、不是停滞**，
   独立计数 `not_applicable_rounds`，把被丢动作**原文**喂给下一轮
   （同 `reseek_lines` 的做法：让它知道这些不行），到 `max_stall` 阈值转交付。
   ⛔ 不许复用 `stalled` / `blocked` / `consecutive_failures` ——
   三种相反的事共用一格，是 V44/V45/V46 反复交学费的那条。

---

## 5. 押注（先押，正文登记对错）

| # | 押什么 | 押错的代价 |
|---|---|---|
| **P1** | B 臂 `usable ≥ 1`，且**至少一轮** `usable ≥ 2` | 若只剩 1 且后两轮全空 ⇒ **"假宽"被证实**：模型想不出第二个能跑的动作，宽度全靠那个必败工具撑着。这是本轮最有价值的负结果，如实登记 |
| **P2** | B 臂 `not_applicable_rounds ≥ 1`（模型第 2 轮还会再提 `code_agent`） | 若为 0 ⇒ 模型第一次被丢掉后就不再提了（它真的在看反馈），那"累积"多一条正面证据 |
| **P3** | `verdict` 两臂都 PASS | 若 B 臂掉 ⇒ 过滤有害，**先回滚再分析**（许撤不许藏） |

---

## 6. 撤回语义（先写，后写撤回态）

撤回 = 删掉**采纳点**那次调用里的过滤（`cands = keep` 改回 `cands = keep + drop`）。

- **必红**：J1（`by_tool_max_round` 回到 4）、J2（`dropped_not_applicable == 0`）。
- **必绿**：J3（A 臂本来就 0）、J6（默认路径不经过过滤）。
- ⚠️ 撤回后 `not_applicable_rounds` 仍为 0（没有丢弃就没有全空轮）⇒
  「守恒式」在这条上**不会红** ⇒ 同 V46 的教训：**守恒是必要条件，不是通电证明**，
  所以锚必须同时钉"能非零"。

---

## 7. 冻结声明

本文件写完之前**没有**跑过任何新实验；实现完成之后**不再修改**本文的判据段。
读数见 `p1_probe.txt`（离线，两次跑须逐字节相同）与 `p2_real.txt`（真实两臂）。

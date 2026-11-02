# V48 预注册：宽度 ≥ 2 ≠ 手段 ≥ 2

冻结于动手之前。任何读数出来之后补写的判据，一律标 `POST-HOC` 并单独列，不进判决。

---

## 0. 这一轮要回答的问题

V47 留下一条只能靠**人读 `per_round`** 才能说出来的事实：

```
on 臂 per_round = [{"n":1},{"n":2,"tools":{"run_shell":2}},{"n":0},
                   {"n":2,"tools":{"run_shell":1,"finish":1}},{"n":0},{"n":0}]
```

第 2 轮 `n=2`、`by_tool_max_round=2` —— 按现有读数，它与第 4 轮
（`run_shell` + `finish`，也是 `n=2`）**长得一样**。但它们不是一回事：

| 轮 | `n` | 工具构成 | 真实含义 |
|---|---|---|---|
| 2 | 2 | `{run_shell: 2}` | **同一路打两枪**（两条 shell 命令） |
| 4 | 2 | `{run_shell: 1, finish: 1}` | **两路**（干活 + 交付） |

**这一轮问的是**：把「同一轮里有几种工具」这层单独立成读数，
① 它能不能把这两种形态分开；② 分开之后**是否等于**「有效手段」；
③ 加这个数动不动行为。

⛔ 这一轮**不改提示词**、**不动 `max_candidates`**、**不加 `Limits` 字段**、
**不加 `ENV_KEYS`**（P5 硬锚 `len(fields)==25` 不动）。
⛔ **不修改 `per_round` 的既有结构**（见 §2）。

---

## 1. 装置与单变量

| 项 | 取值 |
|---|---|
| 内核 | 同一份 `mw/*.py`（本轮只加读数） |
| 题面 | 与 V45/V46/V47 逐字相同（`sales.csv` 区域合计 → `report.md`） |
| 模型 | `qwen2.5:7b` / `T=0` / `max_tokens=1024` / `cand_failover=1` |
| 唯一变量 | `agents.json` 里 `code_agent` **有没有** `applies_regex`（沿用 V47 两臂） |
| A 臂（off） | 无该字段 ⇒ 恒适用（= V45/V46 行为） |
| B 臂（on） | 有该字段 ⇒ CSV 题面不适配 ⇒ 不注入、不占宽度 |

⚠️ **断段声明**：内核改了（`_note_plan_round` 多记三个数），
`caliber_sig` **不变**（没有 `Limits` 变化、`catalog()` 不变）。
⇒ 与 V45/V46/V47 的读数**不可逐格相减**，A 臂就是本轮基线。

---

## 2. 新读数的形状（先写死，再动手）

三个新字段，**都加在 `_plan_zero()` / `stats()["plan"]`**：

| 字段 | 类型 | 回答什么 |
|---|---|---|
| `distinct_by_round` | `list[int]` | 第 i 轮有**几种**工具（与 `produces_by_round` 逐位对齐） |
| `distinct_tools_max_round` | `int` | 所有轮里**最多**几种（与 `by_tool_max_round` 并列） |
| `rounds_same_tool_wide` | `int` | **`n>=2` 且 `distinct==1`** 的轮数（"一路多枪"） |

⛔ **不动 `per_round` 的既有键**。理由不是美观，是纪律 20：
`v6_s135`/`v6_s136`/`v6_s137` 三份**已冻结**的探针里有
`per_round == [{"n":1,"tools":{"finish":1}}]` 这种**整字典相等**断言。
往那个字典里塞一个键，这三份会红 —— 而红的原因不是"行为变了"，
是"我把冻结的断言结构改了"。**证据脚本不许写回自己的证据路径**（V45 踩过）。

⭐ 代价：`distinct_by_round[i]` 与 `len(per_round[i]["tools"])` 在信息上**冗余**。
刻意接受这个冗余，因为它换来一条**可机检的交叉一致性**（J7 ④）——
V38 那次"三源相等"证明过：两条独立路径算出同一个数，比一条路径自己说自己对更硬。

⛔ `rounds_same_tool_wide` 必须带 `n>=2` 条件。**`n=1` 时 `distinct` 恒为 1**，
不写这个条件的话，"每轮只给一个候选"会被算成 100% 的一路多枪 —— 那是反向读数。

---

## 3. 「有效手段」不设新字段（这条是本轮的**立场**，不是省略）

| 层 | 谁提供 | 它答不出的问题 |
|---|---|---|
| 宽度 `n` | `cands_total` / `per_round.n` | 是两路，还是同一路两枪 |
| **手段种类** `distinct` | **本轮新增** | 这几种**能不能用** |
| 可用手段 | `dropped_not_applicable` / `dropped_na_by_tool`（V47） | — |

⛔ 不再造第 4 个字段叫 `effective_distinct`：它 = `distinct` 减去被准入丢掉的那些，
而**被丢掉的那些本来就不在 `cands` 里**（V47 在采纳点过滤，过滤后的才进本读数）。
⇒ 硬造它只会得到 `distinct` 的复制品。§3 的结论由**两臂并列**给出，不由新字段给出。

---

## 4. 判据（全部预先写死，逐条可红）

### 4.1 离线（`probe_v48_distinct.py`，真 `Core`、零模型、零网络）

| 编号 | 断言 |
|---|---|
| **J1** | 两个 `run_shell`（args **不同**）⇒ `n=2`、`by_tool_max_round=2`、**`distinct_tools_max_round=1`**、`rounds_same_tool_wide=1` |
| **J2** | `run_shell` + `finish` ⇒ `distinct_tools_max_round=2`、`rounds_same_tool_wide=0` |
| **J3** | 三个同一 tool ⇒ `n=3`、`distinct_tools_max_round=1`、`rounds_same_tool_wide=1` |
| **J4** | 每轮单候选（`n=1`）⇒ `distinct_by_round` 全 1，但 **`rounds_same_tool_wide=0`**（`n>=2` 那道闸） |

### 4.2 真实两臂

| 编号 | 断言 | 性质 |
|---|---|---|
| **J5** | on 臂：`distinct_tools_max_round=2`、`rounds_same_tool_wide=1`（第 2 轮）、`distinct_by_round=[1,1,0,2,0,0]` | ⚠️ **不是押注**：由 V47 的 `per_round` 直接复算得出，写在这里只为"事后不许改口" |
| **J6** | off 臂：`distinct_tools_max_round=2`、`rounds_same_tool_wide=0`、`distinct_by_round=[2,1,1]` | ⚠️ 同上，复算 |
| **J7** | **押注（唯一一条）**：重跑两臂的 `per_round` 与 V47 **逐字段相同** | T=0 确定性；若不复现，本身是结论（V13 同宗） |
| **J8** | off 臂 `distinct=2` 而其中 4/5 是必败的 `code_agent` ⇒ **`distinct` 也不等于"可用手段"** | 这条是判决的一半，不设阈值 |

### 4.3 行为零改动

| 编号 | 断言 |
|---|---|
| **J9** | `v6_s134`（V44 八景）、`v6_s135`（V45 五景）、`v6_s137`（V47 五景）三份冻结探针**逐字节相同**（sha 见各自 `p1_probe.txt`） |

### 4.4 守恒（每景都断言，与具体数字无关）

① `distinct_by_round[i] == len(per_round[i]["tools"])`；
② `distinct_by_round[i] <= per_round[i]["n"]`（且 `n=0 ⇒ distinct=0`）；
③ `distinct_tools_max_round == max(distinct_by_round, default=0)`；
④ `rounds_same_tool_wide == Σ(1 for r in per_round if r.n>=2 and distinct==1)`。

⛔ ④ 是"两个数从同一处产生、用两种算法对上"—— 单独任一个都能自洽地撒谎。

---

## 5. 不做的事（写死，防止本轮顺手扩大）

- ⛔ 不改 `_extract` 的去重键（`tool|sorted(args)`）：V47 已判"它**不该**被拦"。
- ⛔ 不因为"两个 `run_shell` 算一路"就去砍候选 —— 那是**改行为**，且两条命令可能都必要。
- ⛔ 不动 `max_candidates`（3）。
- ⛔ 不用 `n=1` 的轮次算"一路多枪"（§2 末条）。
- ⛔ 不把 `distinct` 写进 `catalog_line()` / `caliber_sig`（提示词一字不改）。

---

## 6. 押注（先写，后对）

| 编号 | 我的预测 | 置信 |
|---|---|---|
| J7 | 重跑两臂 `per_round` **逐字段复现** | 中高（V47 修 `_na_rounds` 后重跑即复现过） |
| J1~J4 | 四条离线断言**全中**（装置是我自己写的，错也只可能错在期望值上） | 高 |
| 附加（不计分） | on 臂耗时仍 ≈150s、产物仍 44B `2054621…` | 中（引擎侧无改动，但 T=0 不等于逐秒确定） |

---

## 7. POST-HOC（读数出来之后补写，**不进判决**，但必须留痕）

### 7.1 J9 的判据写错了：逐字节相同不成立，而且其中一份**早就是红的**

原判据：「三份冻结探针逐字节相同」。实测（`p1_j9.txt`）：

| 探针 | 结果 | 归因 |
|---|---|---|
| `v6_s134`（V44 八景） | `a8dbe298…` **逐字节相同** | 它不打印整个 `plan` 字典 |
| `v6_s137`（V47 五景） | sha 变了，但 `✅/❌` 行**逐行相同** | 它打印整个 `plan` 字典 ⇒ 多出三个新键 |
| `v6_s135`（V45 五景） | **S1 转红** | ⛔ **不是本轮**：`agent:code_agent` 被 V47 的准入判据丢弃 |

⇒ 判据改成：**比 `✅/❌` 行集合，并对每一处差异做归因**，不比字节。
字节相等只在"不打印 plan 字典"的探针上才成立 —— 那是装置的偶然性质，不是行为证据。

### 7.2 `v6_s135` 的红是 **V47 留下的未登记后果**（本轮才发现）

A/B 归因（不是推理）：

1. `git stash push mw/core.py`（撤掉本轮全部改动）⇒ 复跑 ⇒ **S1 仍然红**
   （`cands_total=2`、`dropped_not_applicable=1`）⇒ 与 V48 无关。
2. 把 `agents.json` 换回 V45 世界的 `v6_s137/agents_off.json`（无 `applies_regex`）
   ⇒ **S1 恢复绿**（5/5 全绿）⇒ 红的原因是那个**配置字段**，不是准入代码写错。

⇒ 这是一条**新的「撤回态腐烂」形态**：不是锚点位移（§89.12 那 3 条），
而是「**后续轮次加了判据，把更早轮次探针的期望值变成假**」。
门禁不跑历史探针 ⇒ 它一直静默。V47 当时没登记，本轮补记（许撤不许藏）。

⚠️ 处置：**不改那份冻结探针**（纪律 20：证据脚本不许写回自己的证据路径）。
它现在是一条已知红，登记在 §114.6，与 §89.12 那 8 条并列计数。

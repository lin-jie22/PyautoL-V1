# PREREG_v175 —— 分数可动性消费接线：修复轴映射 + impl_sha 落账 + Q7 信息量反证

**轮次**：V175（补全 2 第一刀，接 §242.7 / §225.1）
**落盘时间**：见文件 mtime（⛔ 主实验必须晚于 mtime，违者本预注册作废）
**装置**：`mw/scoreinfo.py`（扩展）+ `mw/goals.py`（overlay/修复轴/渲染）+ `bench/probe_v175.py`（P1~P6）
**证据**：本目录 `readings_v175.json`

---

## §0 STAGE0（先量后动 —— 以下读数全部早于本文件落盘）

### 0.1 身份缺口（产品账本成不了池）

`state/score_ledger.jsonl` 36 行 / 24 格，行字段 `(caliber, caliber_sig, candidate, family,
passed, score, session, task_id)`：

- `candidate` 是**自由文本**（如「内核/http qwen2.5:7b（每题新建内核 + 记忆隔离）」），
  不是实现身份；每格 cand 去重后几乎全是 1。
- `session` 不是实现身份：caliber 显示 `temperature: 0.0` ⇒ 同一系统的多次会话是
  **同一实现的重复测量**（k01 三会话同分 1.0 是三次测量，不是三个实现）。
- ⇒ 用现有行做 (实现, 分数) 池：**24 格全部 n_impls=1 ⇒ TOO_FEW ⇒ 全部弃权**。
  （实测数字由 P5 落盘，此处是侦察读数。）
- `run.py --goals-act` 的 goal_obs 行同样无身份（`{"task_id","score","session":None,"source"}`），
  但**答案在记账时点是可得的**（`core.run(...).answer`）⇒ 身份可落账，缺的只是字段。

### 0.2 ⛔ 覆盖定理：第三型 gate 在当前判据下恒被 V173-A 覆盖 —— §242.7 的押注被改写

§242.7 押的是「SCORE_UNMOVABLE 与 CRITERION_UNREACHABLE **并列成第三种 gate**」。**先量后动否证**：

> 设 `claim`（可动性判负）触发条件为「池 CONSTANT ∧ n_impls ≥ MIN_IMPLS ∧ 常数值 < TARGET」。
> 则：`claim` ⟹ 该格存在 `< TARGET` 的分数 ∧ n_obs ≥ n_impls ≥ 3
> ⟹ `unreachable()`（V173-A）在同一轮 `escalations()` 中**必已**把该格收进 esc
> ⟹ `propose(escalated=esc ∪ claim格) == propose(escalated=esc)`。
>
> 唯一窗口（MIN_IMPLS=2、n_obs=2）：此时 `propose()` 对该格只产 **PROBE**（观测不够
> MIN_OBS=3，RAISE 尚不产生）⇒ gate 仍无差别。claim 的 MIN_IMPLS 取 **3** 后，
> 窗口消失，**覆盖是全称的**。

⇒ **本轮不加第三 gate**（加了 = 死代码 + 假装有牙）。第三型的真实产出改为：
**修复轴映射**（诊断 → 修复动作）——这是 §242.7 两个押注里没被否证的那半，也是
ASSESSMENT「缺的是诊断产物→有效修复动作的映射」的第一片。
**登记条件**：当 RAISE 判据变为**可达**（如均分改滑窗/改 best-of）时，V173-A 的定理
失效，claim 的 gate 价值才出现 —— 那时接线是一行（本文件 §1.3 钉住「不进 gate」的
现状，⚠️ 到那天要显式改判据，不许悄悄接）。

### 0.3 产品路径没有候选搜索环 —— 不造假调用点

`v121.search` 式「生成候选 → 逐个判分 → 跳过」只活在 bench 重放脚本
（`bench/evidence/v6_s24x/v157.py` 等）里；`mw/` 的产品路径（core/planner/loop）
没有同构的候选判分环（planner 的 `max_candidates` 是计划宽度，不是判分搜索）。
⇒ 「候选跳过决策」在产品侧**无接线点**，⛔ 不造。产品里真实存在的追分决策点：
① `propose()` 的 RAISE 提议；② `--goals-act` 的执行预算切分。
⇒ **Q7 的反证（信息量能不能选出该跳的）在 bench 重放世界做** —— 那里的事件流真实存在
（V157 的 588 部署事件 / 2710 全搜索事件，零新判分可重放）。

### 0.4 对拍靶（装置自校验，⛔ 数字来自已存读数，不是本预注册编的）

| 靶 | 值 | 来源 |
|---|---|---|
| tab_all | BASE 2710/21｜G1 609/21｜G2 2506/21｜G4 904/21｜G3(.5) 786/17｜G3(1) 1384/21｜G3(1.66) 2135/21｜G3(2) 2331/21 | `v6_s245/readings_v157.json` |
| tab_real（35 记忆格） | BASE 110/20｜G1 60/20｜G2 85/20｜G4 101/20｜G3(1) 70/20｜G3(.5) 44/15 | 同上 |
| 部署流 | n_eval **588**、到 1.0 **21/25**、没改进没命中 **559**、没中键 **520** | `v6_s245/readings_remain.json` / READINGS_v2_remain.txt |
| (实现,键) 对 | 526（永不中 321） | `readings_v157.json` cells |

⚠️ V157 的 W3/W4/W7 曾押在 2710 错误基线上（RESULTS_V157 §0 自踩）——
本轮策略对比一律**同跑内对照**（G6 与 G3/G5/G4 在同一次重放里测），⛔ 不跨轮比大小。

### 0.5 时间线登记（按 mtime 回填）

- STAGE0 侦察：2026-11-17 13:50–14:15（§0.1~0.4 读数）
- 本预注册落盘：**2026-11-17 14:17:12**（mtime）
- 主实验（probe 首跑）：2026-11-17 14:31 首跑（`readings_v175_run1_SUPERSEDED.json`
  留档 —— 首跑的损失口径把「已达标后的再命中」误计为丢命中，⛔ 判据一字未改，
  改的是**装置的计数口径**：丢首中/丢再中拆开）；复跑 14:34（`readings_v175.json`）

---

## §1 判据唯一来源（装置定义 —— 实现只许照抄，不许改语义）

### 1.1 身份与池（`mw/scoreinfo.py` 扩展，纯函数）

- `impl_sha_of(text) -> str`：答案文本指纹 = `sha256(text)[:16]`。同文同值、异文异值。
- `pool_from_rows(rows, *, cell=None) -> (pool, n_no_impl)`：从行里抽 `(impl_sha, score)`；
  **缺 `impl_sha` 的行计数后排除**（不许当成实现 "None" 混进池）；坏行不崩、计数留痕。
- `movability(rows, *, min_impls=3) -> dict`：池过 `classify()` 得 label；
  输出 `{label, n_impls, n_scores, bits, n_rows, n_no_impl, claim}`，
  `claim = "SCORE_UNMOVABLE"` **当且仅当** `label==CONSTANT ∧ n_impls ≥ min_impls ∧
  桶值 < TARGET`；其余一律 `claim=None`。

### 1.2 语义边界（⛔ 不许放大）

- `claim=SCORE_UNMOVABLE` 说的是：「**已试**实现池上，分数从未分辨过任何一个实现
  ⇒ 在这个池的范围内追分连『动没动』都观测不到」。
- ⛔ **不是**「不可能改进」（那是 V173-A 的结构性命题，形制不同）—— 新实现可能拉开分差，
  那时 label 翻、claim 自动消失（fail-open，每轮从账本重算，不做持久化状态位）。
- 弃权三条照旧（TOO_FEW / RULER_BROKEN / IMPL_SCORE_CONFLICT）⇒ `claim=None`，宁可不判。

### 1.3 修复轴映射（`mw/goals.py`，纯函数）—— 本轮的正菜

`repair_axes(reason, movability) -> tuple[str, ...]`，值域封闭 `{"CRITERION","MODEL","RULER"}`：

| 输入 | 输出（元组，主轴在前） | 修复动作指向 |
|---|---|---|
| reason=CRITERION_UNREACHABLE | ("CRITERION",) | 改**判据**（目标定义/聚合），不是改实现 |
| reason=STUCK_MEANS | ("MODEL",) | 改**模型/方法**（换实现路径），不是改判据 |
| movability.claim=SCORE_UNMOVABLE（叠加） | 追加 "RULER" | 改**尺子**（打分轴/分辨力），不是继续改实现 |
| movability 为 None/UNKNOWN/无 claim | 不追加 | 弃权，不给尺子结论 |

- `escalations()` 的证据字典增 **`movability` overlay 字段**（诊断字段）：
  有池给 §1.1 输出；无池/弃权 ⇒ `None`。
- `render_escalations()` 渲染修复轴 + overlay 读数（许撤不许藏：转运维的格每格都要打）。
- ⛔ **overlay 不进 gate**（§0.2 覆盖定理）：`propose()` 的输出与 overlay/claim **无关**。
- 依赖方向：`goals → scoreinfo`（消费侧）；⛔ 反向不许（AZ136g 原样）。

### 1.4 G6 策略（bench 侧，Q7 反证的装置）

重放事件流 `(key, s, src, …)`，在线因果（⛔ 只用**已判过**的分数做标签）：

> 某键已判 m 次且 **m 个分数逐位相同且 < 1.0** ⇒ 跳过该键后续判分。

- `m=2` 主、`m=3` 敏感性（两个都跑，⛔ 不许只报好看的）。
- 与 G2 的分工：键内已见 1.0 的停手是 G2 的事（V157 已量）；G6 只管「**分没到 1.0
  且没动过**」的键 —— 这正是「CONSTANT 格上追分」的字面义。

### 1.5 P6 指标（两个口径分开报，⛔ 分母不同不许比大小）

1. **条件率**（事件级）：`P(后续命中 | 过去-pool CONSTANT@<1.0)` vs `P(命中 | 非 CONSTANT)`；
   同报 `P(改进 | ·)`（s > 判前 best）。
2. **策略表**（V157 apply 框架，同跑内对照）：G6(2)/G6(3) 与 BASE/G1/G2/G3(m)/G4/G5
   同表：`判分 | 省 | 到 1.0 | 掉`；G6 另报 **丢命中数 / 丢改进数**（被跳事件里
   `s≥1.0` 的条数 / `s>判前 best` 的条数）。
3. 两个框架各带 §0.4 对拍自校验（逐位对上才许出结论）。

---

## §2 探针（P1~P6；⛔ 判据以本节为准，跑后不许改）

| # | 命题 | 判据 |
|---|---|---|
| P1 | 池抽取可归因 | 缺身份行**计数排除**（n_no_impl 精确）、坏行不崩、有身份行进池 |
| P2 | overlay fail-open + 零回归 | 无池/缺身份/弃权 ⇒ overlay=None；**旧行为**（两理由的 esc 输出）在无 impl_sha 时逐字不变 |
| P3 | 修复轴映射 | 值域恰 3 名字；映射表逐条对（含叠加 RULER 与不叠加两种）；同入同出（纯函数） |
| P4 | 覆盖定理实证 | claim 触发的格 ⊆ unreachable 的 esc（构造 4 组含窗口组）；`propose(esc) == propose(esc∪claim格)` 逐字相同；⛔ 反例组（claim 不触发的格）必须不在 ⊆ 里 |
| P5 | 产品账本现状体检（部署态） | 36 行真账本 ⇒ 24 格全 TOO_FEW/UNKNOWN、`n_no_impl=36`、claim 全 None —— **现状读数**（身份缺口），不是缺陷声明 |
| P6 | Q7 反证 | ① 条件率：注册方向 = `P(命中\|CONSTANT@<1.0) < P(命中\|非)`；② 策略表：G6 省下的判分 vs 丢掉的命中/改进，与 G3/G5/G4 同表。⛔ **两个方向都如实报**：成立 ⇒ 信息量选得出该跳的（V157 价格负结果的反面）；不成立 ⇒ 负结果入档，与价格并列 |

⛔ P6 判负不许改装置迁就（V174 P3 教训：先信池错、再信判据，判据原文一字不动）。

---

## §3 门禁（回填）

- 契约 **706/0**（+AZ137a~f，700→706）；锚层 97/0
- 撤回态 **274**（268→274：AZ137×6）；R14 定向演练 **6/6 精确转红**
  （日志 `withdraw_r14.log`；AZ137b 连带 K5 已登记 COUPLED —— `impl_sha_of` 是
  scoreinfo 里 `hashlib` 的唯一使用点，TH2/TH3 同型的「同一行两个观测面」）
- 文档计数 0 漂移（契约 706×5 处 + 撤回态 274×4 处）
- `run_tests.sh` 全量门全绿（单跑复核，步骤 8 隔离 ✅ —— 见 §243.6）
- DESIGN §243 台账 + VERDICTS 重生成

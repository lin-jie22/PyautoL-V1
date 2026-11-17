# PREREG_v177 —— 行为级分数信息量与可动性：「同行为同分、异行为异分」逐池可量

**轮次**：V177（§245，接 §242.7-2 / §243.7-② / §244.7-1）
**落盘时间**：见文件 mtime（⛔ 主实验必须晚于 mtime，违者本预注册作废）
**装置**：`mw/scoreinfo.py`（+`behavioral()`）+ `mw/goals.py`（repair_axes 认识行为级 claim）
+ `bench/probe_v177.py`（P1~P7）+ `tests/contract_test.py`（AZ139a~h）+ `bench/withdraw_check.py`（R16）
**证据**：本目录 `readings_v177.json`、`withdraw_r16.log`

---

## §0 STAGE0（先量后动 —— 以下读数全部早于本文件落盘）

### 0.1 通过向量的原料在哪（零尺子改动）

`bench/scorers/code_exec.py` 逐条 test 独立子进程跑（`run_one()`），打分器内部
**已经算出通过向量**（`passed` 计数），但输出协议只有标量 `score = passed/len(tests)`
（`code_exec.py:127`）—— 向量不出协议。

⇒ 取数路径：探针侧 `import code_exec`（`run_one` 可直接调用，实测导入成功）逐条收
向量，**与 ExternalScorer 的实得分对拍** `sum(vec)/len(vec) == score`（容差 1e-9）。
对拍是装置自校验：⛔ 对拍不过不许出结论。**打分器一字不改**（V176 的零尺子改动纪律）。

### 0.2 均分尺子的结构事实（注册为边界，不是判据）

现役打分器 `score = 通过条数/总条数` ⇒ 分数只携带通过向量的**汉明权重**：
同权重的不同向量必然同分（4 条测试上 `(1,0,0,0)` 与 `(0,1,0,0)` 都是 0.25）。
⇒ 行为级 INJECTIVE 只说明**这个池**里恰好没有同权重碰撞，⛔ 不是「分数能分辨任意行为」。
要分辨同权重行为只有两条路：改聚合（加权）或加/换测试 —— 两条都是 RULER 修复轴。

### 0.3 两层读数的关系（可证命题，契约钉住；⚠️ 前提：两边都不弃权）

- **T1**：行为级 CONSTANT ⟺ 分数级 CONSTANT（`classify()` 的 kind）。
  （行为级 CONSTANT ⇒ 所有行同分 ⇒ 分数级 CONSTANT；反向：所有行同分 ⇒ 行为→分
  映射是常数。）
- **T2**：分数级 INJECTIVE ⇒ 行为级 INJECTIVE。
  （反证：两行为同分 ⇒ 各含 ≥1 实现 ⇒ 两实现同分 ⇒ 与「每实现一分」矛盾。）
- ⚠️ **前提不可省**：冲突闸弃权时两边结论都无意义 —— 反例：两行**同行为异分**
  （A,v,0.5）（B,v,0.7）：分数级是 INJECTIVE（2 实现 2 分），行为级必须**弃权**
  （BEHAVIOR_SCORE_CONFLICT）。T2 不带前提就是假的 —— 这条反例进 P4 与 AZ139b。

### 0.4 产品侧没有通过向量数据源 —— 不造假接线点

`state/score_ledger.jsonl` / goal_obs 行都只有标量分（V175 落了 impl_sha，仍无向量）
⇒ `escalations()` 的 overlay 数据源里**没有**行为可读，产品路径不接行为级读数。
本轮唯一接线：`repair_axes()` 认识 `BEHAVIOR_UNRESOLVED` → RULER（纯函数面补全，
探针可调；⛔ 不是产品调用点）。向量落账（写侧）是下一刀的选项，本轮不做。

### 0.5 真池侦察（sys_small，待测预期 ⛔ 不是判据）

每题池 = `{oracle, partial, null} ∪ admit_sys.MUTANTS[tid]`（5~6 实现/题，4~5 条测试/题）。
按均分物理（§0.2）手推 s01（4 测试）：oracle=1111@1.0、partial/wipe_ttl=1000@0.25、
null/wipe_state=0000@0.0 ⇒ 3 行为 3 分 ⇒ 预期行为级 INJECTIVE、分数级 COARSE
（5 实现 3 分）—— 两层对照的活例子。⚠️ 这是**预期**，P6 实测为准；其余题是否
出现同权重碰撞（行为级 COARSE）由读数回答。

### 0.6 时间线登记（按 mtime 回填）

- STAGE0 侦察：2026-11-17 15:55–16:10（§0.1~0.5 读数）
- 本预注册落盘：**见 mtime**
- 主实验（probe 首跑）：见 `readings_v177.json` 头部时间戳（必须晚于本文件 mtime）

---

## §1 判据唯一来源（装置定义 —— 实现只许照抄，不许改语义）

### 1.1 输入与单位

`behavioral(rows)`，`rows` = 可迭代的 `(impl, behavior, score)` 三元组：

- `impl`：实现标签（str() 归一；本层只做计数与冲突检测的归属，⛔ 不是身份账本）。
- `behavior`：**通过向量** —— 非空 tuple/list of **bool**（该题测试集上的逐条通过位串）。
  ⛔ 不收 0/1 整数（bool 是 int 子类的镜像陷阱：放行 1 会把口径混了当合法）。
  全池向量长度必须一致（长度不齐 = 混了不同测试集 ⇒ 弃权）。
  向量的展示形态：`"1010"`（True→1）。
- `score`：标量分 ∈ [0, TARGET]（闸与 V174 同：非数值/bool/NaN/inf/越界 ⇒ 弃权）。
- 完全相同的三元组去重；⚠️ 同 `(impl, behavior)` 不同 score、或同 impl 不同 behavior
  ⇒ 冲突闸（见 1.2）。

**行为类** = 通过向量取值相同的行归一类。⛔ 单位是**行为类**不是实现（与 V174 的
「每实现一票」不同层）；`n_impls` = 去重 impl 标签数（报告用），`n_behaviors` = 行为类数。

### 1.2 三道弃权闸（fail-open，顺序：尺子坏 → 冲突 → 样本少）

⚠️ **落盘初稿把冲突闸排在尺子坏之前 —— 主实验前自查改正**（2026-11-17 16:15，
主实验未跑）：坏分必须先被拦（同行为两行 0.5 与 NaN：排前面的闸会把它读成
「冲突」，掩盖「尺子坏了」；V174 把 RULER_BROKEN 排第一（「坏分不许被去重悄悄
吃掉」）同理）。实现按**改正后**的序：

1. `RULER_BROKEN` —— 分数非数值/bool/NaN/inf/越界；向量非 bool 元素/空/长度不齐；
   三元组形状不对。detail 点名行与原因。
2. `BEHAVIOR_SCORE_CONFLICT` —— 同行为多分（违反**同行为同分**）或同实现多行为
   （确定性判分下只能是口径混了）。detail 点名是哪一种、是谁。
3. `TOO_FEW` —— `n_behaviors < 2`（行为→分的映射只有 0/1 个点，分不了类）。

⛔ 任何一条都不许崩、不许静默降级成 CONSTANT（V173/V174 同构）。
⛔ 撤回态下的 detail 格式不许抛异常带走汇总行（AZ136f 教训：`sorted({None,…})`）。

### 1.3 分类（过闸后，按行为类分桶）

`buckets = {score: [behavior, ...]}`（行为类按 `"1010"` 串点名；键 `f"{s:g}"`），
`k = n_scores`，`n = n_behaviors`：

| 条件 | kind | 语义 |
|---|---|---|
| `k == 1` | `CONSTANT` | 0 信息：分数从未分辨过任何两种行为（bits 强制 0.0，防 -0.0） |
| `k == n` | `INJECTIVE` | **同行为同分 ∧ 异行为异分**都成立（分数是行为的忠实编码） |
| `1 < k < n` | `COARSE` | 存在**异行为同分**（分数把行为折叠了） |

输出字段（固定，缺字段即 bug，AZ139e 钉值域与自洽）：

    kind / reason / n_impls / n_behaviors / n_scores
    resolution = k/n ∈ (0,1]｜bits = 桶分布香农熵｜bits_max = log2(k)
    buckets = {分: [行为串, ...]}｜claim｜detail（无事为 None）

弃权输出：kind=`UNKNOWN`，reason ∈ {`BEHAVIOR_SCORE_CONFLICT`, `RULER_BROKEN`,
`TOO_FEW`}，计数类字段 None，buckets={}，detail 给点名。⛔ 弃权就是弃权。

### 1.4 claim（行为级可动性，V175 claim 的行为面对应物）

    claim = "BEHAVIOR_UNRESOLVED"
      ⟺ kind == CONSTANT ∧ n_behaviors ≥ MIN_BEHAVIORS(3) ∧ 桶值 < TARGET

语义：「**已试**行为类上，分数从未分辨过任何两种行为（通过向量）」。
⛔ 不是「不可能改进」—— 新行为/新分数拉开分差时 kind 翻、claim 自动消失（每轮重算）。

三合取逐条的理由：

- `n_behaviors ≥ 3`：与 V175 的 MIN_IMPLS=3 同理由（2 个是巧合的常见形态 ——
  均分尺子上两个同权重向量撞分太常见；3 个才够说「池上没分辨过」）。
  ⚠️ 量词单位是**行为**不是实现（claim 说的是行为），故不沿用 MIN_IMPLS。
- `桶值 < TARGET`：做完了 ≠ 做不到（V175 同；常年 1.0 的池是「做完了」）。
- `CONSTANT`：COARSE/INJECTIVE 都已分辨过至少一对行为。

### 1.5 repair_axes 扩展（纯函数面补全，⛔ 不是产品接线）

`repair_axes(reason, movability)` 的 RULER 叠加条件从
`claim == SCORE_UNMOVABLE` 扩为 `claim ∈ {SCORE_UNMOVABLE, BEHAVIOR_UNRESOLVED}`
（两个 claim 都指向「改尺子」：加/换测试或改聚合）。其余映射一字不动。

### 1.6 注册定理（契约 AZ139a/b 钉；⚠️ 前提=两边都不弃权，§0.3）

- **T1**：`behavioral().kind == CONSTANT ⟺ classify().kind == CONSTANT`
- **T2**：`classify().kind == INJECTIVE ⇒ behavioral().kind == INJECTIVE`

---

## §2 判据（probe_v177，逐条先注册后跑）

| # | 命题 | 反证在哪 |
|---|---|---|
| P1 | 常数臂：3 行为同分 ⇒ CONSTANT，claim **触发**；2 行为同分 ⇒ CONSTANT 但 claim **不触发**（反巧合合取）；CONSTANT@1.0（3 行为）⇒ 不触发（做完了≠做不到） | P5 |
| P2 | 单射臂 + **两层对照**：A,B 同行为同分 + C,D 异行为异分 ⇒ 行为级 INJECTIVE，而同池 `classify()` 是 COARSE（4 实现 3 分）—— 两层读数不同层的实证 | P5 |
| P3 | 粗臂：4 行为 3 分（两同权重向量撞分）⇒ COARSE，buckets 逐行为点名，bits 对上手算 | P5 |
| P4 | 弃权三条 + 陷阱：TOO_FEW(1 行为)/RULER_BROKEN(1.2、-0.1、NaN、bool 分、非 bool 向量、空向量、长度不齐)/BEHAVIOR_SCORE_CONFLICT(同行为多分、同实现多行为)；§0.3 反例（同行为异分的池分数级 INJECTIVE）在此登记 | P5 |
| P5 | 变异体有牙：恒 CONSTANT ⇒ P2/P3 红；恒 INJECTIVE ⇒ P1 红；拆冲突闸 ⇒ P4 冲突臂不再弃权 | 本条自反 |
| P6 | 真池（sys_small 6 题 × 5~6 实现）：向量由 `code_exec.run_one` 收、与 ExternalScorer 分数**对拍**（不过不许出结论）；逐题 behavioral() + classify() 两层对照；双跑逐字相同 | P7 |
| P7 | 值域自洽：kind 恰 4 名、reason 恰 3 名、claim ∈ {None, BEHAVIOR_UNRESOLVED}、resolution/bits 区间恒成立；T1/T2 在构造池上成立 | P5 |

⛔ 判据写在本文件，`bench/probe_v177.py` 只负责执行与读数落盘。

---

## §3 契约与撤回态（AZ139a~h / R16）

契约只锚**判定的形状**（纯函数面，不在契约里跑打分器）：

| # | 钉什么 |
|---|---|
| AZ139a | T1 双向（前提=不弃权） |
| AZ139b | T2 + §0.3 反例（同行为异分 ⇒ 行为级必须弃权、分数级是 INJECTIVE） |
| AZ139c | claim 三合取缺一不给（2 行为/1.0/COARSE/弃权 ⇒ 无 claim；3 行为@<1.0 ⇒ 有） |
| AZ139d | 冲突闸两个子检查都弹（同行为多分、同实现多行为），且干净池不弹 |
| AZ139e | 值域封闭（kind 4 名 / reason 3 名 / claim 2 值 / 字段齐全 / bits·resolution 区间） |
| AZ139f | RULER_BROKEN 全陷阱逐个弹（不许崩、不许分类） |
| AZ139g | repair_axes：BEHAVIOR_UNRESOLVED→RULER、SCORE_UNMOVABLE→RULER、其余映射不动 |
| AZ139h | 纯函数：双跑逐字相同（输出不掺随机） |

撤回态 R16 逐条单因变异、精确转红（COUPLED 如实登记）：
⚠️ 已知结构耦合预备 —— AZ139e 钉 kind 字面值域，而 `KIND_*` 与 V174 共用常量
⇒ AZ136f 的撤回（`KIND_CONSTANT` 改名）必然连带 AZ139e（同型：AZ137b 连带 K5），
到时**登记 COUPLED，不换「更巧的」变异伪造单红**。

---

## §4 边界（⛔ 不许放大）

1. ⛔ **不是新打分器**（§169.7 排除项仍有效）：不判对错、不改分数 —— 只给
  「分数对行为的分辨力」称重。
2. **行为 = 通过向量**（该题测试集上的位串），⛔ 不是「语义行为」；换测试集 = 换口径
   ⇒ 标签是 **(题 × 池 × 测试集)** 的属性（V174「标签是 (题×池) 的属性」再加一维）。
3. ⛔ 均分尺子下分数至多分辨权重类（§0.2）—— INJECTIVE 是**池的属性**不是题的属性；
   「同权重碰撞」的修复只有 RULER 轴（改聚合/加换测试）。
4. ⛔ 产品路径无向量数据源 ⇒ overlay/propose 一字不动、无消费接线（§0.4）。
5. claim 不是「不可能改进」（§1.4）。
6. 真池 6 题 × 5~6 实现是小样本；⛔ 不放大成「所有任务的分数都/都不分辨行为」。
7. ⛔ 变异体（P5）只证判据非恒真，不证「读数是对的」—— 读数对不对由对拍与
   构造臂的手算对上（P3/P6）承担。

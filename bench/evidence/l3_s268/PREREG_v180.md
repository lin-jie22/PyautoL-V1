# PREREG v180 —— 让 COARSE 也给轴：**哪一层**的 COARSE？

接 DESIGN §247.7-①。上一轮（V179）判定「修复轴消费是空操作」，机制是两条 claim
都映射到同一个 `RULER`，轴丢掉了「哪一层坏」。本轮不再问「轴够不够细」，
改成问一个更小、更能判死的问题：

> **「分辨力不足」该由哪一层的读数说话？**

## §0 已经知道的（V179 读数，⛔ 不重测）

| 事实 | 出处 |
|---|---|
| 真实池（sys_small 6 题 × 6~8 实现）**分数级 6/6 全 COARSE** | V179 P2 / `readings_stage0_v179_run2.json` |
| 同一批池**行为级只有 1/6 是 COARSE**（m02_vfs），其余 5 格 INJECTIVE | 同上 |
| `repair_axes` 全仓产品调用点**只有 1 处**（`goals.py:666`，`render_escalations`） | `grep -rn "repair_axes("` |
| 那处的 `movability` 实参来自 `scoreinfo.movability()`，其 `claim` 值域只有 `{SCORE_UNMOVABLE, None}` | `scoreinfo.py:292-340` |

⇒ 推论（本轮要**量**而不是假定）：`repair_axes` 里那条
`CLAIM_BEHAVIOR_UNRESOLVED ⇒ RULER` 分支在**部署路径上不可达**。
行为层的读数从来没有到达过轴 —— 这比「轴粒度不够」更具体：**是接线缺了一段**。

## §1 两条候选规则（同一批池上，先量后动）

| 名 | 触发条件 | 语义 |
|---|---|---|
| `R_score` | `movability.label == COARSE` | 「同分实现不止一个」⇒ 尺子分辨力不足 |
| `R_beh` | `behavioral.kind == COARSE ∧ n_behaviors ≥ MIN_BEHAVIORS(3)` | 「**存在异行为同分**」⇒ 尺子分辨力不足 |

两者都只往轴里加 `RULER`（⛔ 不改值域：`AZ137d` 钉的三个名字不动）。

差别在假阳性：`R_score` 会把「多个实现行为完全相同、只是写法不同」也当成尺子坏。
行为级 INJECTIVE 是**反证**：分数在这些行为上是分开的 ⇒ 尺子没有问题，问题在模型。

## §2 押注（三值，方向先写死）

| # | 命题 | 押 |
|---|---|---|
| A | 真池上 `R_score` 会在**行为级 INJECTIVE** 的格上给 `RULER`，且这类格 ≥4 | 成立 |
| B | `R_beh` 给出的格集合 == 行为级 COARSE 的格集合，且 `R_score` 的集合**真包含**它（结构命题，⛔ 不押个数） | 成立 |
| C | 本轮改动在**部署**上不改任何一格轴（真账本行没有 `vec` ⇒ 行为级 UNKNOWN ⇒ `R_beh` 触发 0 格） | 成立 |

⛔ C 是本轮最重要的一条：它决定这轮是「有牙」还是「有牙没猎物」（纪律 72）。
如果 C 不成立（部署上居然有 vec 行），那说明 V178 的发射早就跑过账本，我上面的
「生产 0 触发」判断就是错的 —— 那也必须照实报。

## §3 判据（先写，后跑；⛔ 不许事后加）

| # | 命题 |
|---|---|
| P1 | 真池 6 格：`R_score` 的假正例（行为级 INJECTIVE 却给 RULER）≥4，`R_beh` 的假正例 = 0 |
| P2 | `R_beh` 在 `n_behaviors = 2` 的 COARSE 池上**不**给（与 claim 的 `MIN_BEHAVIORS` 同源） |
| P3 | `repair_axes` 旧输入逐个不变（`AZ137d` 那 7 个 + `("X", {"claim": BEHAVIOR_UNRESOLVED})`） |
| P4 | 去重：claim 与 beh 同时要求 `RULER` ⇒ 轴里 `RULER` 只出现一次 |
| P5 | overlay 带行为读数：有 vec 的池 ⇒ `esc[cell]["movability"]["beh"]["kind"]` 在场；无 vec ⇒ 不得是 `INJECTIVE`（缺席不许当「有分辨力」） |
| P6 | 部署两臂逐格相同：改前 / 改后，真账本上每一格的轴 tuple **完全相同**（C 的可执行形态） |
| P7 | 有牙：把 `R_beh` 改成「恒不给」的变异体 ⇒ P1/P2 全红；改成「非 INJECTIVE 就给」的变异体 ⇒ P1 红 |

## §4 不做

1. ⛔ 不加新轴名（`AZ137d` 值域封闭）。
2. ⛔ 不让 `R_score` 触发（P1 若成立，它就是假阳性源）—— 只作为**对照臂**存在。
3. ⛔ 不改 `behavioral()` / `movability()` 的判据与弃权闸。
4. ⛔ 不说「消费做完了」：本轮只补**一段接线**，动作层仍未接。
5. ⛔ 不碰 V79。

## §5 口径

- 装置：`probe_v180_stage0.py`（STAGE0，先于任何产品代码改动）+ `v180.py`（实现后判据）。
- 真池重建与 V179 同一台（`sys_small` 6 题 × oracle/partial/null/变异体，真尺子子进程）。
- 分母一律写进字段名（`n_cells` / `n_beh_coarse` / `n_fp`）。

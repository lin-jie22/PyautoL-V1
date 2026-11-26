# PREREG v249 —— 把 v248 的「两行标签 + 装置执行」接成 `--goals-swap-edit` 新档

- 日期：2026-11-26
- 轮次：V249（DESIGN §319）
- 前置：`bench/evidence/l13_s318/RESULTS_v248.md`（E 臂 `6/6 = 10/16`，A/N 均 `0/16`）
- **本轮不调模型**：这是**接档**的预注册，不是实验的预注册。判据全部是结构性的。

---

## 0. 为什么够资格接，以及接它**不等于**证明它有效

v248 的预注册 §6 写的门是「`E ≥ 3` 且 `LINE=16` 占多数 ⇒ 可接 flag（默认 off）」。
实测 `E = 10/16`、`LINE=16` 占 `13/16` ⇒ **过门**。

⚠️ 但必须同时写清三件事，否则接档会变成包装：

1. **v248 是单题（`t01`）、单起点、单模型（7B）的读数。** 接进产品只是让它**可选**，
   **不构成**「生产链路有效」的证据。生产链路多题、真打分器（`scorer.score()` 而非
   `reflect.verify`）、起点来自账本 —— 三个口径都不同。
2. **执行由装置做。** 这一档提供的是「模型给意图 → 装置落地」的通道，
   ⛔ 不是「模型学会了自己改代码」。
3. **只适用最小修复 = 一行**的 bug。改两行 / 改结构的场景，这一档产出的补丁必然不完整
   （装置只改一行），会表现为「分数不动」而不是报错。

---

## 1. 接口：新增一个枚举参数，默认档**逐字节不变**

| 项 | 值 |
|---|---|
| 参数 | `--goals-swap-edit {off,line}` |
| 默认 | **`off`** |
| `off` | 走现有路径，**提示与解析与 V235~V248 逐字节相同** |
| `line` | 提示换成「带行号 + `LINE:`/`EXPR:` 两行」，产出由 `apply_edit` 单行替换 |

⛔ **非法值必须抛，不许静默回落 `off`**（V240 已立：`max_tokens` 非法 ⇒ 构造即抛；
「以为改大了其实没改」与「以为开了其实没开」是同型，纪律 127）。

---

## 2. 默认档逐字节不变的**取证方式**（不接受口头保证）

三条一起：

| 编号 | 断言 | 取值来源 |
|---|---|---|
| **E1** | `build_proposal_prompt(goal, code, why, actuals, trace=tr)` 对本项目的 `t01` 仍产出 **`2307` 字节 / `1bdea4fc92d67817`** | v245/v247/v248 三批实测，跨轮稳定 |
| **E2** | `build_proposal_prompt(..., edit=False)` 在无 trace 时仍产出 `2818c5673cc0884d`（AZ163a 已钉） | 现有契约 |
| **E3** | `--help` 里出现 `--goals-swap-edit`，且不传时 `args.goals_swap_edit == "off"` | argparse 实测 |

**E1 是主锚**：它同时钉住「代码槽、why 槽、actuals 槽、trace 槽、尾段」五处的拼接顺序。

---

## 3. 新档的行为定义（必须与 v248 的 E 臂**逐字节同构**）

v248 的 E 臂是**三段构造**，生产版必须复现同一顺序：

```
A = build_proposal_prompt(goal, code, why, actuals, trace=tr)   # 现有默认产出
N = A.replace(code, numbered_code(code), 1)                     # 代码槽 → 带行号
E = N.replace(TAIL_PROPOSAL, TAIL_EDIT, 1)                      # 尾段 → 两行格式
```

- `numbered_code(code)`：`"\n".join(f"{i:3d}| {ln}")`，行号是**真实坐标**，从 1 起。
- `TAIL_EDIT` 逐字节同 `bench/evidence/l13_s318/preflight_v248.py` 里的 `TAIL_E`。
- **`edit=True` 且 `trace` 为空 ⇒ `ValueError`**：没有轨迹就无从指认「哪一行」，
  静默降级会产出一个「只改一行」的提示却没有定位依据（fail-closed，纪律 62 的正例路径在 §5）。

### 3.1 装置 `apply_edit(code, line, expr)`

| 情况 | 判定 | 行为 |
|---|---|---|
| `line is None` | `parse_fail` | 不产出，落 `err` |
| `expr` 为空 | `empty_expr` | 不产出 |
| `line` 越界 | `oom` | 不产出 |
| 替换后 `ast.parse` 失败 | `syntax_fail` | 不产出 |
| 正常 | `""` | 返回新代码 |

- **缩进由装置补**：`原行前导空白 + expr.strip().lstrip("|- ")`。
  ⛔ 不让模型负责缩进（否则「缩进写错」会被算成模型能力，纪律 230 的同型）。
- 失败一律**不替换**，返回空串 + err ⇒ 调用方按「本次换路失败」记账，**不落一个坏候选**。

---

## 4. 判据（本轮唯一一行，结构性）

**主判据**：`run_tests.sh` 全绿 **且** `check_counts` 11 处 0 漂移 **且** 默认档三条锚（E1/E2/E3）全过。

| 结局 | 动作 |
|---|---|
| 全过 | 接受。DESIGN §319 写明「接上了，但有效性仍是 v248 的单题读数」 |
| 任一红 | **撤回本次改动**（不留「半接」态）。⛔ 不许把红改成「已知问题」放行 |

⛔ 本轮**不报分数**：一次模型都没跑。任何「接上后变好了」的说法都属于**未测**。

---

## 5. 已知代价（接之前就写，不许事后补）

1. **`harness_sha` 会变**（改 `mw/reflect.py`）。⇒ 跨轮锚 `3b33bcb8` 作废，
   下一轮起不能拿它做「同一装置」的证明。必须在 DESIGN 里点名。
2. **契约条数 846 → 846+N**，锚层 100 → 100+M，撤回态 382 不变。
   ⇒ README / demo/README / site/index.html / quickstart.sh 的声明数要同步，
   `check_counts` 是唯一真值来源（AZ47 会抓）。
3. **`_swap_candidates` 在 `line` 档多调一次 `trace_failure`**：
   纯本地 Python 跑一遍失败输入，不调模型、不加钱，但**每换路多一次解释器开销**。
4. **`line` 档的产出比 `off` 档短得多**（v248：22~39 tok vs 191 tok）⇒
   **token 记账的口径变了**，跨档比 token 消耗没有意义。

---

## 6. 边界（⛔ 不许放大）

1. 接档 **不等于** 生产链路有效。生产版与 v248 的三处差异（多题、真打分器、账本起点）**未测**。
2. `line` 档 + `alt_code`（第二体）的组合**不支持**：`edit=True` 且 `alt_code` 非空 ⇒ `ValueError`。
   v237 已证明第二体槽位对产出零影响，但那是**整函数重写**口径，`line` 口径未测 ⇒ 不许静默放行。
3. ⛔ 不说「自省闭环通了」「L3 完成」「内核会自己变强」。
4. 撤回态 382 条**不动**：本轮不改任何被撤回的代码。

---

## 7. 交付物

- `mw/reflect.py`：`numbered_code()` / `apply_edit()` / `parse_edit()` /
  `build_proposal_prompt(..., edit=False)`。
- `run.py`：`--goals-swap-edit`（默认 `off`，非法即抛）；`_swap_candidates(..., edit=...)`。
- `tests/contract_test.py`：AZ169a~g 共 7 条。
- `README.md` / `demo/README.md` / `site/index.html` / `quickstart.sh`：计数同步。
- `DESIGN.md` §319：代价、锚点、边界。

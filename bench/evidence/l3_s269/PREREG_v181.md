# PREREG v181 —— 让部署账本长出 `impl_sha` + `vec`

接 DESIGN §248.8-1。V180 判出：行为级读数在**部署**上 0 触发，根因不是接线缺，
是现账本 36 行写在 V175/V178 之前 —— **两个键一个都没有**。本轮只问一件事：

> **真跑一段带 scorer 的账本之后，部署侧的行为级池组得起来吗？组起来之后，轴上会多什么？**

⛔ 本轮**不改产品代码**（写点 `run.py:2558`/`:3019` 已在）。这是一轮「喂猎物」的实验，
不是又一层机制。

## §0 已经知道的（⛔ 不重测）

| 事实 | 出处 |
|---|---|
| `state/score_ledger.jsonl` **36 行 / 7 会话 / 5 族**，键里**无 `impl_sha` 也无 `vec`** | 本轮 STAGE0 census |
| 两个写点都在：`run.py:2558`（账本行）、`run.py:3019`（goal 观测行），且都「捕到才写键」 | `sed -n 2540,2570p run.py` |
| 账本追加有 solver 闸：`args.solver not in ("oracle","null","partial")` | `run.py:2539` |
| 零模型下 `--solver auto/llm` 会落到 **oracle 分支且改标签**（`run.py:2469`）⇒ 不写账本 | 同上 |
| `code_exec` 打分器在有可执行代码时发 `vec`；抽不出代码**不发键** | `bench/scorers/code_exec.py:17-24,138` |
| `MIN_POOL=2` / `MIN_IMPLS=3`（分数级 claim）/ `MIN_BEHAVIORS=3`（行为级 claim）/ `TARGET=1.0` | `mw/scoreinfo.py:37,171,338,40` |

⇒ 推论（本轮要**量**）：**「零模型让账本长出这两个键」的路不存在** ——
三个零模型 solver 恰好就是被闸掉的那三个参照点。所以本轮必须跑一次模型。
⚠️ 这是近十轮第一次动用模型，理由不是「想跑得更真」，而是**非参照 solver 只有模型**。
模型走**本机** ollama（`http://localhost:11434/v1`，`qwen2.5:7b`），⛔ 不连外网；
`qwen2.5:14b` 不参与（MEMORY 纪律：14b 单独立轮）。

## §1 押注（三值，方向先写死）

| # | 命题 | 押 |
|---|---|---|
| **EX** | 「存在零模型路径能让部署账本长出 `impl_sha`/`vec`」 | **不成立** |
| **A1** | 非参照 solver 的账本行 `impl_sha` 覆盖 = **100%** | 成立 |
| **A2** | `vec` 覆盖 ≥ **2/3** 行（code_exec 只在抽出代码时发） | 成立 |
| **B1** | 「同格同时有两键」的格 ≥ **4/6** | 成立 |
| **B2** | 同格实现去重（`impl_sha` 唯一值）≥2 的格 ≥ **4/6**（T=0.9 的多样性） | 成立 |
| **C1** | 行为级 label ≠ UNKNOWN 的格 ≥ **1** | 成立 |
| **C2** | 轴上出现 `RULER` 的格 ≥ **1**（来自 beh `COARSE ∧ n_behaviors≥3`） | **不成立** |
| **C3** | 轴上出现 `CRITERION` 的格 ≥ **1**（来自分数级 `SCORE_UNMOVABLE`） | 成立 |
| **D** | 在真行上抹掉 `vec` ⇒ 出现的 `RULER` 数归零、`beh` 全 None；抹掉 `impl_sha` ⇒ 池空、claim/beh 全 0 | 成立 |
| **E** | 本轮不改产品代码 ⇒ 契约 733/0、锚层 97/0、撤回态 297、文档计数 11 处 0 漂移 | 成立 |

**C2 押「不成立」的理由（先写死，事后不许改口）**：`MIN_BEHAVIORS=3` 要
**≥3 个不同行为类 + 其中至少两个撞分**。本轮每格只有 5 个实现，而 `coding_small`
对 7B 偏易 ⇒ 大概率「多数实现全对 ⇒ 只 1 个行为类」⇒ 连 COARSE 都构不成。
如果 C2 反而成立，说明我对「易题上模型的行为分化」估低了 —— 那也是结论，照实报。

## §2 判据（先写，后跑；⛔ 不许事后加）

| # | 可执行形态 |
|---|---|
| P0 | `--solver oracle\|null\|partial` 各跑到**临时账本**（`--ledger /tmp/...`），追加行数 = 0；同一装置 `--solver llm-direct` 到临时账本，追加 > 0（证明不是「装置哑火」，纪律 88） |
| P1 | 新行 `impl_sha` 覆盖 100%；`vec` 覆盖率与 B1 同表打印（`n_rows_has_impl` / `n_rows_has_vec` / `n_rows_both`） |
| P2 | 逐格表：`n_impls` / `n_behaviors` / 分数级 `label`+`claim` / 行为级 `kind`+`claim` / 轴 tuple |
| P3 | `goals.escalations(real_log, real_obs)` 在真账本上跑：转运维格数 ≥1，且 `movability` overlay 里出现 `beh` 子字段的格数 ≥1 |
| P4 | 变异 D 两臂（内存里抹 `vec` / 抹 `impl_sha`，⛔ 不写盘）⇒ 计数按 §1 D 变 |
| P5 | 门禁四项读数与 V180 逐字相同（733/97/297/11 处 0 漂移） |

## §3 口径

- **装置**：`probe_v181_stage0.py`（STAGE0：census + P0 的四臂临时账本）、
  `v181.py`（真账本上的逐格表 + P3/P4）。
- **账本**：产品账本 `state/score_ledger.jsonl`（这是「部署」的定义域）。
  ⛔ 不许把临时账本的行并进来；⛔ 不许给 36 行旧行补字段。
- **分母写进字段名**：`n_rows` / `n_rows_has_impl` / `n_rows_has_vec` / `n_rows_both` /
  `n_cells` / `n_cells_both` / `n_cells_beh` / `n_cells_ruler` / `n_cells_criterion`。
- **实现身份** = `scoreinfo.impl_sha_of(答案文本)`（答案在 solver 外面捕获，⛔ 不改跑题路径）。
- **vec** = `code_exec` 的逐 test 通过向量，与 `score` 同一次测量（T1 自洽闸由
  `beh_pool_from_rows` 执行，容差 `VEC_SCORE_TOL`）。
- **solver**：`llm-direct`（1 题 1 次调用，最便宜的非参照 solver），`--temperature 0.9`
  （要多样性；⛔ 不许为了凑 COARSE 中途改温度）。
- **K**：先跑 **5** 个会话（6 题 × 5 = 30 行）；若 `vec` 覆盖 < 2/3 或 B2 不成立，
  **只许照实报，不许补跑到成立**。

## §4 不做

1. ⛔ 不改 `run.py` 两个写点、不改 `mw/scoreinfo.py` 任何判据/阈值。
2. ⛔ 不放宽 solver 闸（不许把参照点写进账本）。
3. ⛔ 不新增轴名、不改 `KINDS`、不碰 `AZ132`/`AZ137`。
4. ⛔ 不说「L3 完成」「行为级接通了动作」：本轮只让池**长得出来**。
5. ⛔ 不碰 V79。

---

## §6 追加预注册（18:59 写，**早于任何会话跑进产品账本**）

起因：STAGE0 之后读 `mw/goals.py:108` —— `coerce()` 的返回值是

```python
return {"task_id": ..., "score": ..., "session": ..., "source": ...}
```

**只有这四个键**。而 `--goals` 的 obs 恰恰是 `G.coerce_all(G.load(ledger))`。
⇒ V180 §248.6-1 那句「写点已在，缺的只是一段新账本」**少算了一道闸**：

> 第三道闸不是写点，是**归一化**：账本长出字段之后，`coerce()` 会在 obs 上把它们扔掉。

这道闸只有真账本 + 真 `--goals` 才看得见（`scoreinfo.movability()` 单独喂原始行时一切正常，
V179/V180 的合成池就是这样绕过了它）。本轮因此从「喂猎物」变成「喂猎物 + 查它有没有被吃」。

| # | 命题 | 押 |
|---|---|---|
| **X1** | 新账本跑完后（**不改代码**），`esc[cell]["movability"]` 里出现 `beh` 子字段的格数 = **0** | 成立 |
| **X2** | 同一时点，`movability` 的 `claim` 触发格数 = **0**（obs 无 `impl_sha` ⇒ label=UNKNOWN） | 成立 |
| **X3** | `coerce()` 透传两键之后，X1/X2 两数由 0 变成 ≥1；且 `stats()`/`evaluate()`/`propose()` 的输出**逐字不变**（新键不参与任何判据） | 成立 |

判据（补）：

| # | 可执行形态 |
|---|---|
| P6 | 修前 / 修后两臂：`G.escalations()` 返回的 esc **格集合完全相同**（只 overlay 变），`G.propose()` 的 gid 序列逐字相同 |
| P7 | 撤回态：删掉 `coerce()` 的透传两行 ⇒ X1/X2 回到 0，新锚转红（各自演练，禁止多红） |

⛔ 修 `coerce()` 时**只**加透传，不动 `movability()`/`behavioral()`/`classify()` 的任何阈值与判据。
⛔ 透传必须 fail-open：`vec` 只在是 list/tuple 时透传，`impl_sha` 只在是非空 str 时透传
（坏形状由 `beh_pool_from_rows()` 的行闸去计数，⛔ 不在归一化层二次判）。

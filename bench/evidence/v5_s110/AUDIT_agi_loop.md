# 核查：V1 的「AGI 循环」到底有没有在跑

**触发**：用户问「我一直要做的 AGI 系统，你有没有在做？」
**做法**：只查落盘证据，不凭印象。核查时间 2026-10-29 17:0x。
**结论一句话**：器官是真的、接线是真的、循环真跑过 12.5 天 —— 但 §71~§79 我跑的每一把都是 `--fresh`，
等于**每次冷启动**。我一直在体检一个被拔掉记忆的 agent。

---

## 1. 器官（模块存在，且在主链路上被调用）

| 模块 | 行数 | 主链路调用点（实测） |
|---|---|---|
| `mw/memory.py` | 432 | `core.py:1419 self.memory.recall(goal, k=limits.recall_k)` |
| `mw/worldmodel.py` | 530 | `core.py:13 observe`（失配 ⇒ 沉淀 episodic + semantic） |
| `mw/planner.py` | 557 | `core.py:844 replan` / `857 propose` / `1660 answer` |
| `mw/metacog.py` | 599 | 自我模型（`_kwm_absorb` 收 stats） |
| `mw/mutate.py` | 378 | 变异台账 |
| `mw/values.py` | 257 | 价值 |
| `mw/abstractor.py` | — | 抽象层 |
| `mw/critique.py` | — | 评审账本 |

⛔ 这一步的意义：`mw/*.py` 合计 **9 549 行**，其中约 4 000 行是 AGI 器官，不是量具。

## 2. 循环（真的跑过，不是「机制有牙没用武之地」）

`state/memory.db` **4.9 MB**，只读打开实测：

| 表 | 行数 |
|---|---|
| `episodes` | **2 705** |
| `facts` | 42 |
| `facts_fts` | 42（FTS5 全文索引建立成功） |
| `blobs` | 1 024 |

`episodes` 时间跨度 **10-17 02:51 → 10-29 15:53（12.5 天）**，13 天里每天都有写入：

```
10-17 247  10-18 352  10-19 323  10-20 224  10-21 17   10-22 464
10-23 136  10-24 102  10-25 323  10-26 103  10-27 270  10-28 108  10-29 36
```

`kind` 构成 = **完整任务生命周期**：`goal 640 / failure 705 / mismatch 628 / finish 639 / outcome 93`。
⇒ 这**不是**契约测试的痕迹，是内核真实跑出来的「目标 → 失败 → 失配 → 完成」。

## 3. 但我跑的那一支把循环拔了

```python
run.py:1103   with tempfile.TemporaryDirectory(prefix="v1_fresh_") as td:
run.py:1105       os.environ["V1_STATE_DIR"] = str(Path(td) / "state")
run.py:995    _seed = (0, 0) if args.fresh else _memory_seed(s)
```

`--fresh` ⇒ **每题一个临时 state，跑完即删**；`memory_seed` 记 `(0,0)` 是诚实的（临时目录确实空）。

而 §71~§79（8 轮）的命令行**每一把**都是 `--fresh --no-external`。

⇒ **本轮所有结论（`Δ=0`、`keep_ratio`、`cff84197`）都只对「冷启动的 agent」成立。**

`run.py:1097-1102` 的注释自己记着同族事故：
> 原先只 `_build` 一个新 Core，DB 仍是共享的 `state/memory.db` ⇒ 记忆跨题跨运行累积。
> 后果：两轮"同口径"读数一次 0.8611、一次 0.7222，差额全部来自起跑时已存在的语义记忆。

## 4. ⭐ 更要紧：跑过的循环，产出的知识 41/42 是噪声

| `facts.source` | 条数 | `hits` 合计 |
|---|---|---|
| `worldmodel` | **41** | 628 |
| `selftest` | **1** | 24 |

**唯一那条 `selftest` 是真知识**：
> `python 写文件时应显式指定 encoding=utf-8，否则默认编码会在非 ASCII 上静默截断`
> `confidence 0.900`，`hits 24`

**那 41 条 `worldmodel` 长这样**（原文）：
> `执行 [ascii confidence encoding note] 时不可假设其结果等同于「会返回与截断相关的历史结论条目」（实测为「新增 key=d4e8…」）`
> `confidence 0.400`，`hits 1`

`confidence` 分布：**0.4 有 22 条**（就是下限），0.42~0.5 有 11 条，0.7 有 7 条，0.9 只有 1 条。

⇒ 这 41 条**不是知识，是日志**：把「我执行了 X，结果不是我预期的 Y」写成了一条事实。
⇒ 累积这种事实，`recall()` 检索到的是噪声。**这才是「循环跑起来也没变强」的真实原因候选**，
而 §71~§79 从来没读过这一层（因为 `--fresh`）。

## 5. 污染源已定位（不是推测，是受控实验）

### 5.1 现象

今天 `state/memory.db` 落了 **36 条** episode（时段 03:06:36 → 15:53:37），特征：
- `wall_s = 0.0`、`llm_calls 4/30`、`steps 2/10` ⇒ **StubLLM 离线桩**
- 题目是 `solve(x)` / `flatten(d)`（契约测试的最小用例），**不是** `tasks_v8` 的
  `clean_lines` / `group_names` / `week_of`
- `kind` = goal 8 / mismatch 8 / failure 12 / finish 8
- `state/critique.jsonl` 同期多 8 条 `family=tasks_v4` 的记录（`at03~at08`）

`README.md:416` 写着「`V1_STATE_DIR`/`V1_LOG_DIR`/`V1_WORKSPACE` 测试必须覆盖它们（不许碰生产目录）」。

### 5.2 定位过程（四步，每步一个读数）

| 步 | 做法 | 读数 |
|---|---|---|
| 1 | 按题面反查产地 | `tests/contract_test.py:4145` + `bench/gen_tasks.py:333` ⇒ 老 bench 题集 |
| 2 | 排除契约测试本身 | `contract_test.py:29-34` **先设 env 再 import mw**，`_TMP=mkdtemp()` ⇒ 它自己隔离正确 |
| 3 | 受控实验：只跑 `gen_v8.py` 自检 | `state/memory.db` mtime **未变** ⇒ 与造题脚本无关 |
| 4 | 统计「会构造 Core 又没隔离」的探针 | `probe_verify.py`：`Core(`=1、`V1_STATE_DIR`=**0**、`.run(`=**5**`；`probe_recall.py`：`Core(`=1、`V1_STATE_DIR`=0、`.run(`=1 |

### 5.3 因果链（已用实验闭合）

`probe_verify.py:148` 建 `Core(settings=..., llm=...)` **不传 `memory`** ⇒ `Core` 自建
`MemoryStore(cfg.settings().paths.db)`；而 `paths.db` 来自 `mw/config.py:86`
`_env_path("V1_STATE_DIR", ROOT / "state")` ⇒ **env 没设就落到 `ROOT/state` = 生产库**。

**决定性实验**（零污染）：同一个探针，两次跑

```bash
# ① 设了 env：写进临时目录，生产库不动
V1_STATE_DIR=$TD/state ... python bench/probe_verify.py
  → 生产库 mtime 15:53:37 → 15:53:37（未变）
  → $TD/state/memory.db 出现          ✅ 探针尊重 env

# ② 不设 env（env -u）：写生产库
env -u V1_STATE_DIR ... python bench/probe_verify.py
  → state/memory.db sha256 81b3359e → 547568bb  ❌ 被写
```

⇒ **探针没有缺陷，缺陷在 `run_tests.sh`**：第 287 行（6f）与第 308 行（6g）跑这两个探针时
**都没设 `V1_STATE_DIR`**，只有第 128/152/170 三个步骤设了。

### 5.4 修法（已落地）

1. `run_tests.sh` 顶部加 **步骤 0**：`CI_ROOT=$(mktemp -d)` + `export V1_STATE_DIR/V1_LOG_DIR`
   ⇒ 所有没显式指定的步骤自动继承隔离值（第 128/152/170 的显式覆盖仍然生效）。
2. 末尾加 **步骤 8「门禁隔离自检」**：跑前记 `state/memory.db` / `state/critique.jsonl` 的
   `sha256`，跑完比对，**不一致就 `FAIL=1`**。
   ⇒ 这条是让「不许碰生产目录」从散文变成量具（§78.7.5 那条教训的直接应用）。

**撤回态已验**：把 `export V1_STATE_DIR` 去掉再跑，`sha256` 从 `81b3359e` 变 `547568bb`，
步骤 8 判红 ⇒ **检测器有牙，不是装饰**。

**修复后全量门禁复验**（3m2s，`EXIT=0`）：

```
步骤 2/7  契约测试      ✅ PASS=588  FAIL=0
步骤 3/7  外部锚层      ✅ PASS=97   FAIL=0
步骤 8    门禁隔离自检
  ✅ 生产记忆库未被触碰（state/memory.db sha256=81b3359ebfe7e9ae）
  ✅ 评审账本未被触碰（state/critique.jsonl sha256=94bdbc1c6b0bb05e）
```

探针的写入确实换了位置：`$TMPDIR/v1_ci_state.XGmzbj/state/memory.db`（86 016 字节），
生产库 `4.9 MB / 2705 episodes` **一条没多**。

⇒ 这条同时解释了 §4 的噪声从哪来：**`facts` 的 41/42 噪声事实，很可能有一部分就是这么被灌进去的。**
在修好之前，任何「累积」读数都不可归因。

## 6. 判断

| AGI 的定义性特征 | 器官 | 真跑过 | 进过我的结论 |
|---|---|---|---|
| 累积（记忆） | ✅ 432 行 + 2 705 episodes | ✅ 12.5 天 | ⛔ **从没有** |
| 预测（世界模型） | ✅ 530 行 + 628 hits | ✅ | ⛔ **从没有** |
| 自省（元认知） | ✅ 599 行 | 待核 | ⛔ **从没有** |

**我在做零件，没在做循环。** 而且不是「忘了」——是 `--fresh` 这条测量纪律变成了唯一姿势。
它本身没错（测候选好坏不能混进记忆多少），但它垄断了 8 轮的全部读数。

## 7. 下一轮（第 110 轮）方向：先把这个循环量出来

**V19（新首选，取代 V15）**：累积对照。

| 臂 | 命令 | 语义 |
|---|---|---|
| A | `--fresh` | 现状：每题清零 |
| B | `--memory-dir D` | 共享记忆（学/考范式） |

`run.py:609` 已保证两者互斥 ⇒ 两条**干净的分支**，不需要改装置。
读数：第 N 轮的 `M` vs 第 1 轮，`Δ` 必须超过噪声下限（用 `mw/measure.py` 的 `noise_floor`）。
判负：**共享记忆的 `M` 不升** ⇒ V1 没有累积能力，「AGI 路径」这个说法不成立，必须改口径。

⚠️ 但 B 臂有个**前置条件**：`facts` 里 41/42 是噪声（§4）。不先压噪，累积的是垃圾，
`M` 不升会被误读成「记忆没用」，而真因是「记忆里全是日志」。
⇒ 所以顺序是：**① 修 `facts` 准入（`worldmodel` 同义反复挡在门外）→ ② 跑累积对照**。

① 是内核改动 ⇒ 必须同步撤回态 + 文档 22 处 + 门禁；`harness_sha` 必断段（刻意）。

### 7.1 为什么不先做 V15

V15（交付前多给一次带反馈的调用）问的是**单任务内部**的事。
V19 问的是**V1 是不是一个会变强的系统**。后者高一个量级，且它是「AGI」这个词的唯一可验证含义。

### 7.2 判负条件（先写死）

1. 压噪后 `facts` 仍有 > 10% 条 `confidence ≤ 0.5` 且 `hits = 1` ⇒ 准入闸没起作用，不是记忆的问题。
2. 压噪后 B 臂 `M` 相对 A 臂的 `Δ` 不超噪声下限 ⇒ **累积无效**，V1 只能做「单任务 harness」。
3. B 臂 `M` 上升但 `recall` 命中率不升 ⇒ 是参数在动，不是记忆在起作用（假因果）。

## 8. 自踩（本轮 2 条）

1. ⚠️ **我第一次用 `grep -n "MemoryStore\|memory\b\|mem\."` 得到「空结果」，差点当成「V1 没有记忆」**。
   BSD grep 不认 `\|` ⇒ 这是我 MEMORY 第 8 条自己记过的坑，**而且是用在不存在性结论上**。
   改用 Grep 工具后立刻查到 `run.py` 有 7 处 memory 相关。
   ⇒ 纪律：**任何「不存在」的结论只能用 Grep 工具或 Python 下**。
2. ⚠️ 我口头先说了「V1 没有跨任务状态」，是**凭印象**，而且**错了**（有 432 行 + 2705 条 episode）。
   在这个仓库里凭印象说「没有 X」，成本极高。

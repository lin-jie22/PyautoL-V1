# V51 结果 —— 交付闸通电了，但它拦下的东西**没有下一跳**

证据：`PREREG.md`（含 §10 POST-HOC）/ `probe_v51_gate.py` / `p1_probe.txt` /
`run_v51.sh` / `run_v51c.sh` / `logs_a.txt`~`logs_c.txt` / `mount_diff.txt` / `RESULTS.md`。
设计：`DESIGN.md §117`。

---

## 1 装置（两臂只差一个文件）

```
diff -r /tmp/v51/wtA/mw /tmp/v51/wtB/mw
  → 差异文件数 = 1（mw/core.py）
```

`B` 的改动 = `_gate_finish` 增加「题面要求的产物在不在」这一问；
`A` = HEAD `972424e` 逐字节。其余全同：同一题面（`sales.csv` → `report.md`）、
同一份 `sales.csv`（真值 华北 2700 / 华南 2000 / 华东 700）、`qwen2.5:7b` / T=0 /
`MAX_TOKENS=1024` / 宽 3 / `cand_failover=1` / `allow-shell` / 各自冷启动 state。

## 2 三臂读数

| 读数 | A（HEAD） | B（+交付闸） | C（POST-HOC：B + `optional`） |
|---|---|---|---|
| `verdict` | **FAIL** | **FAIL** | **FAIL** |
| `status` | done | stalled | not_applicable |
| `plan.rounds` | 1 | 3 | 5 |
| `plan.by_tool` | `{finish:1, run_shell:2}` | `{finish:3, run_shell:2}` | `{finish:3, run_shell:2}` |
| `verify.runs` | 1 | 2 | 3 |
| `verify.failed` | 0 | **1** | **2** |
| `verify.artifact_checks` | —（字段不存在） | **1** | **2** |
| `verify.artifact_failed` | — | **1** | **2** |
| `failover.used` | 0 | **1** | **1** |
| `failover.rescued_step` | 0 | 0 | **1** |
| `report.md` | 不存在 | 不存在 | **不存在** |
| `wall_s` | 33.0 | 68.2 | 89.1 |
| `llm_calls` | 3/48 | 9/48 | 12/48 |

⚠️ A 臂的 `artifact_*` 是 `None` —— 那一臂的 `verify_stats` 里**没有这三个键**。
⛔ 不许把它读成 0：**缺席不是零**（纪律 5）。这也是为什么新计数必须自己带分母
（`artifact_checks`），而不是从 `failed` 里"推"出来。

## 3 押注结算（PREREG §4）

| # | 押注 | 结果 |
|---|---|---|
| J1 `verdict` PASS | ❌ **错**（仍 FAIL） |
| J2 `verify.failed ≥ 1` | ✅ 中（1 / 2） |
| J3 `artifact_failed ≥ 1` | ✅ 中（1 / 2） |
| J4 `failover.used ≥ 1` | ✅ 中（1） |
| J5 `report.md` 存在 | ❌ **错**（三臂全无） |
| J6 反向闸 | 见 §7 |

⇒ **闸通电了（J2/J3），让位也通电了（J4），但链条没有跑出产物（J1/J5）。**

## 4 为什么没跑出来：三层，一层比一层更深

### 4.1 第一层：闸是按预期工作的

B 臂现场（`logs_b.txt`）：

```
↳ finish ok=True 直接生成report.md文件
🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：题面要求的产物不存在：report.md
↪ 交付被打回 ⇒ 让位给候选 2/3（不重规划，直接用已有的）
```

`artifact_failed=1` 是**结构化计数**（不是从日志里数出来的）。
⇒ 这是 V50 §116.9 那条「`finish` 成功是让位唯一没接线的地方」的**第一次接线成功**。

### 4.2 第二层：被让位到的那条命令，在 `dispatch` 里就被拒了（B 臂）

```
↳ run_shell ok=False 原因=run_shell 缺参数 ['timeout_s'] 检查sales.csv文件是否存在
```

`run_shell` 的目录行是 `run_shell(cmd, timeout_s)`，描述写「超时秒数**(可选)**」，
handler 也写 `args.get("timeout_s", 30)` —— 而 `dispatch()` 用
`[k for k in spec.params if k not in args]` 判缺参 ⇒ **可选参数被当必填**。

⛔ 这不是"模型不会用工具"，是**目录对模型撒了谎**。而且它解释了 V45/V50 里
反复出现的「让位了但没救回」：让位选中 `run_shell` 并不难，难的是那条调用
能不能活过 `dispatch`。

### 4.3 第三层：让位复用的是「以为已经做完」时想的候选（C 臂）

C 臂把 `timeout_s` 修成真可选之后（`catalog_sha` 不变，见 §6），同一条候选：

```
↳ run_shell ok=True 检查sales.csv文件是否存在
↪ 让位救回一个**步骤**（非交付动作）：run_shell
```

**命令成功了，但它是一条 `test -f` 检查，不是写文件。** 于是：

| 轮 | 候选 | 结果 |
|---|---|---|
| 1 | finish / **run_shell(检查存在)** | finish 打回 → 让位 → 检查成功，产物仍无 |
| 2 | finish | `⏭ 重复动作已跳过` |
| 3 | finish（"手动计算并生成报告"） | 第 2/2 次打回 |
| 4~5 | `code_agent`×1 | 全被准入判据丢掉 ⇒ **准入空轮** 2/2 |
| 收尾 | — | `🧪 收尾交付过闸` + `📦 收尾交付 77 字符` |

⇒ **让位省掉的是"重规划"，而重规划正是唯一能让模型知道"报告还不存在"的地方。**
旧候选是在"finish 已经算交付"的信念下提出的，它们回答的是"还要不要确认一下"，
不是"怎么把文件真写出来"。

## 5 顺带打出的两个洞（都还没修，登记在此）

### 5.1 `_close_out` 的闸只有自检，没有产物问

B/C 两臂都出现 `🧪 收尾交付过闸`，而 `report.md` 不存在：
`_close_out`（`core.py:2256`）跑的是 `vfy.run(best, self._checks)`，
**不是** `_gate_finish`。⇒ 同一道判据两个消费者，只接了一个。
`verdict` 仍然是诚实的 FAIL（它在 `_finish` 里另算一次），所以**读数没撒谎，
但"过闸"这个词撒谎了**。

### 5.2 准入空轮会把"没候选"变成"交付"

C 臂第 4~5 轮：`code_agent` 全被判不适用 ⇒ `not_applicable` ⇒ 转向交付。
对一个**产物缺失**的任务，这条出口等于"没有可用手段时交一段文字"。
它不是本轮变量，但它决定了"链条断了以后系统会怎么收场"。

## 6 代价与边界

- **耗时 33.0 → 68.2 → 89.1s**，`llm_calls` 3 → 9 → 12。闸不是免费的：
  每次打回都要多一次规划与一次收尾。
- `catalog_sha` 两个口径**未变**：`8decb60895d4a972` / `29091404966ca8e1`
  （`ToolSpec.optional` 不进 `catalog_line()`）。⇒ 96 格口径没被这两处改动破坏。
- ⛔ **没有买到一格正确性**：三臂 `verdict` 全 FAIL、`report.md` 全不存在。

## 7 反向闸（J6）

见 `RESULTS` 追加节（12 格）。判据：单函数题上 `expected_artifacts` 返回空
⇒ 产物问**不该开火** ⇒ 两臂逐题相同。

## 8 自踩

1. **第一版探针**用 `c.s.paths.workspace = ...` 赋值 ⇒ `Paths` 是 frozen dataclass，
   当场 `FrozenInstanceError`。改成构造前设 `V1_WORKSPACE`。
2. **S6 第一版**想把 workspace 设成非法值来触发 except ⇒ `_env_path` 会把任何字符串
   变成合法 `Path`，走不到异常分支（**那是"没测到"，不是"没坏"**）。改成 monkeypatch
   `vfy.check_delivery` 抛一次。
3. **改 `withdraw_check.py` 时把 AZ112 整条删了**（`old_string` 带上了它的收尾括号）：
   加完 AZ113/AZ114 才发现 `CASES` 里少了那条。已补回，条数 `198 → 200`
   （恰是 +2，不是 +1 也不是 +4）。
4. 本轮没有并发跑契约与真实臂；12 格反向闸与三臂**串行**（抢同一个 ollama 会让耗时不可比）。

---

## 7 J6 反向闸（12 格：6 题 × 1 rep × 2 臂）

| 臂 | `mean` | `std` | 区间 | `caliber_sig` | `suite_sha` | `catalog_sha` |
|---|---|---|---|---|---|---|
| A | 0.8611 | 0.0000 | [0.8611, 0.8611] | `2f853355fb041d57` | `618a18cafd2f0da5` | `8decb60895d4a972` |
| B | 0.8611 | 0.0000 | [0.8611, 0.8611] | `2f853355fb041d57` | `618a18cafd2f0da5` | `8decb60895d4a972` |

⇒ **两臂整卷读数完全相同，三个口径键全同** ⇒ 产物问在这 6 道单函数题上**没有开火**。
这正是 PREREG §4 J6 要的形状（`outs=[]` ⇒ `ANSWER` 通道 ⇒ 不拦）。

⚠️ **判据写的是"逐题相同"，实际只能读到整卷。** `--no-answers` 不落逐题明细，
`measurement` 只有 `mean/std/lo/hi/n` ⇒ "6 个格子一一相同"**没有被证明**，
只能说"整卷均值与离散度完全相同"。这是装置限制，不是通过。
（要逐题就得加 `--answers`，那会把答案写进产物目录，本轮没做。）

## 9 确定性

`p1_probe.txt` 两次跑**逐字节相同**：

```
c1c588b7ca25c15a9cfdcb941c215f82d60d71f9cfff6f1be7bdc6a88aeb79d6
```

六景（打回 / 通过 / 不乱开火 / 点名缺失 / 超限交付 / 判据异常不拦）全绿。

---

## 10 收口：**交付闸已撤回**，只留下被单独隔离出来的那条 bug

### 10.1 为什么撤

| 项 | 读数 | 判 |
|---|---|---|
| `verdict` | A/B/C 全 FAIL | 没买到正确性 |
| 耗时 / `llm_calls` | 33.0→68.2→89.1s ／ 3→9→12 | **净负** |
| 老契约 | AZ107 / AZ108 / AZ110 **同时变红** | 扰动三张与本题无关的断言 |
| `report.md` | 三臂全不存在 | 目的没达成 |

把 `core.py` 单独撤回到 HEAD、只留 `tools.py` 的 `optional` 之后，三条老契约**全部回绿**，
AZ108 的 `plan` 逐字段回到 `rounds=1 / by_tool={finish:2, run_shell:1}` ⇒
**扰动源确实是交付闸**，不是 `optional`。

按纪律「没证据更好 = 不改」⇒ **撤回交付闸**（`git diff mw/core.py` 为空）。

⚠️ 撤回**不等于**"这条边不存在"。它仍然是被证据钉死的一件事：
`finish` 的 `ok` 只问"东西在不在"，而 `verify.runs/failed/artifact_*` 三组读数
证明闸**能**问出"用户要的产物在不在"。缺的是它问完之后的那两步（见 §4.2/§4.3）。

### 10.2 落地的是什么

只有一处：`ToolSpec.optional` + `dispatch` 的缺参判定。

| 项 | 撤前（B 臂） | 撤后（C 臂） |
|---|---|---|
| 同一条候选 `run_shell({"cmd": "test -f sales.csv && echo exists"})` | `ok=False`，`run_shell 缺参数 ['timeout_s']` | **`ok=True`、`rc=0`** |

- `catalog_sha` 两个口径**一个字符没变**（`8decb60895d4a972` / `29091404966ca8e1`），
  因为 `optional` 不渲染进 `catalog_line()`；
- 契约新增 **AZ114**（三条子判据：缺省能跑 / `cmd` 仍必填 / 目录行不变），
  撤回态新增同名条目（`CASES 198 → 199`）；
- ⛔ 它**不改变**任何 `verdict`：C 臂仍 FAIL。落地理由是"目录不能对模型撒谎"，
  不是"能让任务做完"。

### 10.3 契约与文档

| 项 | 值 |
|---|---|
| 契约 | **618/0**（原 617，+AZ114） |
| 撤回态 | **199** 条（原 198，+AZ114） |
| 文档计数 | README / demo / site / quickstart 已同步（617→618、198→199） |

⚠️ 撤回态本轮只做**定向演练 AZ114**，没有跑全量 199 条：
本机一次契约 ~38s，199 条 ≈ 2 小时。**沿用 V49 的「198 条 / 9 红」记录，不当作本轮读数**。
（V50 那次把"没跑完"写成"卡住"是误判：0 字节日志是 Python 块缓冲，不是死锁 —— 见 §8.5。）

### 10.4 §8 补一条自踩

5. **把"慢"读成"卡住"**（V50 遗留）：`withdraw_check.py` 200 条 × ~38s ≈ 2 小时，
   而 V50 在 47 分钟时看到"日志 0 字节 + 备份目录 mtime 不动"就判它死了 ——
   后者是块缓冲，前者只能说明"还没跑完"。本轮起：长跑一律 `python3 -u`，
   并且**先算一次成本**（cases × 单次契约耗时）再决定跑不跑。

### 10.5 门禁与撤回态（本轮实测）

```
步骤 2/7  契约          ✅ PASS=618  FAIL=0
步骤 3/7  锚层          ✅ PASS=97   FAIL=0
步骤 3b   文档计数      ✅ 22 处声明，0 处漂移（真值：契约 618 / 锚层 97 / 撤回态 199）
步骤 8    生产库指纹    ✅ state/memory.db sha256=359c7100c2272d14（未变）
步骤 7g   P5            ✅ limits 字段 25 个（未被本轮改动）
```

撤回态**定向演练**（`python3 -u bench/withdraw_check.py AZ114`）：

```
一、基线 ✅ ('618','0') 红=无
二、✅ AZ114 红=['AZ114'] 目标=✔ 多红=无 汇总行=('617','1') 崩溃=无
三、✅ 还原后基线 ('618','0') 红=无
```

⚠️ 启动时它报 **1 个过期备份 `bench/build_g7d_seed.py`**（V50 被杀时留下的
`if False and stale:` 变异标记）。本轮该文件**已被 `git checkout --` 还原**，
所以"当前内容 ≠ 当初写进去的变异"⇒ 它拒绝还原 —— 这正是那条保护起作用的样子。
⇒ 追认 V50 那条纪律：**撤回态不是原子操作**，收尾必须 `git status` 全仓比对。

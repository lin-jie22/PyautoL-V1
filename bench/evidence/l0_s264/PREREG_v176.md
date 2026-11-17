# PREREG_v176 —— 表现型空间扩容：有状态 + 多文件小系统套件（sys_small）

预注册时间：2026-11-17 15:11（主实验落盘之前；⛔ 判据在本文件定，读数不许回改判据）
接 §225.1 / ASSESSMENT §1「67 题全是单函数纯函数（单元测试规模）」的规模天花板。
本轮把它抬到：**有状态对象（场景序列驱动）+ 多文件模块（跨文件导入）**。

---

## §0 STAGE0 侦察（先量后动，全部实测）

### 0.1 现状读数

- `bench/tasks/*.jsonl` 8 套 **40 题** + `bench/heldout/` 9 套 **27 题** = **67**，
  逐题形态「写一个纯函数」（ASSESSMENT §1）；`Suite.load` 只认 id/prompt/tests/
  oracle/partial/null/meta/assertions —— 没有文件格式、没有场景约定。
- 打分器 `bench/scorers/code_exec.py`（尺子）：每条 test 一段独立子进程
  （`answer + test` 拼接执行，`python -I` + rlimits + 独立临时 cwd）。
  **⛔ 本轮不动这把尺子**（改它 = 换尺子，历史 calibrate 全部失效）。
- `run_tests.sh` 步骤 5：`run.py --bench --offline --solver oracle --suite-dir
  bench/tasks` —— globs 全部 `*.jsonl`，逐领域 calibrate + **oracle 必须满分**。
  ⇒ 新套件自动进全量门，且必须满足「校准过 + oracle 1.0」。
- `bench/check_counts.py`（AZ47）核对文档里的 契约/锚层/撤回态 数字。
- 准入器先例：`bench/taskspec.py::admit()`（生成型题族的 A3~A6 四条互不依赖证据
  + 反证纪律「没有反证的检测器是死代码」）—— 手写套件**没有**对应准入器。

### 0.2 两个冒烟（决定本轮的机制押注，2026-11-17 15:05 实测）

1. **有状态场景测试 = 现有尺子的原生能力**：多行测试串（构造 → 多步操作 → 断言）
   在 `code_exec` 下照常判分；「每次调用清空状态」的 wipe 变异体实测
   1.0 → **0.5**（2 条测试抓到持久化）⇒ 测试对状态有牙，无需新尺子。
2. **多文件 = 答案格式约定（FILES 字典）+ 测试侧物化**：答案代码定义
   `FILES = {"models.py": "...", "store.py": "..."}`；测试段把 FILES 落盘到
   沙箱临时目录并 `sys.path.insert(0, os.getcwd())`，跨文件 `from models import
   Record` **真导入**（store.py 内部的相对导入同样走真实 import 机制）。
   实测：合规答案 **1.0**（含跨文件导入）；单文件答案 **0**（NameError: FILES）。
   ⇒ 多文件的格式强制由**测试**承重，尺子零改动、run.py 零改动。

### 0.3 覆盖/押注边界（⛔ 不许放大）

- 「多文件」指**模块边界 + 跨文件导入**（在沙箱里物化成真模块）；⛔ 不是
  多文件工程管理（无构建系统、无包安装、无相对路径目录树）。
- 「有状态」指**场景序列驱动的对象**（十几步交互、跨调用持久化、事务/过期
  等语义）；⛔ 不含长程规划、外部世界动作、真实时间（时间一律显式入参，保确定性）。
- 表现型仍是**函数/类实现**；「自己写测试」「自己设计接口」不在本轮。

---

## §1 装置定义（判据唯一来源 = 本文件）

### 1.1 套件 `bench/tasks/sys_small.jsonl`（6 题）

| id | 形态 | 考什么 | partial（中档）是什么 |
|---|---|---|---|
| s01_kv_expiry | single/有状态 | TTL 键值库：put(k,v,now,ttl)/get(k,now)/keys(now)/delete | 忽略 TTL（永不过期） |
| s02_limiter | single/有状态 | 滑窗限流器：limit/window，hit(key,now)/count(key,now) | 固定窗口（边界错） |
| s03_tx_journal | single/有状态 | 事务日志：begin/put/del/get/scan/commit/rollback，事务内可见暂存 | rollback 只撤销最后一步 |
| m01_records | files/纯逻辑 | models.py(Record) + filter.py(select) + stats.py(summary)，跨文件导入 | filter 丢 min_value 条件 |
| m02_vfs | files/有状态 | node.py(File/Dir) + vfs.py(VFS：mkdir/write/read/ls/rm 递归) | rm 不递归（只删空目录） |
| m03_tiered | files/有状态 | lru.py(LRU 容量淘汰) + tiered.py(两级缓存：get 提升到 L1) | LRU 按 FIFO 淘汰 |

- `meta.format` ∈ **{single, files}**（⛔ 封闭词表）；single 的测试直接用类，
  files 的测试以物化前导（落盘 FILES + sys.path）开头并跨文件 import。
- 每题必带三臂校准材料：oracle（1.0）/ partial（严格居中）/ null（0.0）。
- 时间/随机性一律显式入参 ⇒ 全套件确定性可复现。

### 1.2 准入器 `bench/admit_sys.py`（手写套件的准入，含反证）

检查项（每题逐条，fail-open：**缺读数 ⇒ 拒绝**，不许当通过）：

| # | 检查 | 判据 |
|---|---|---|
| A1 oracle_full | oracle == 1.0 | 参考解必须满分（只 >null 不够） |
| A2 null_floor | null == 0.0 | 空答案必须拿不到分 |
| A3 mid_order | null < partial < oracle **严格** | 中段有序（不是二值尺子） |
| A4 mutants_caught | 注册的每个变异体得分 **< oracle** | 反真空：测试必须有牙 |
| A5 determinism | oracle/partial 复打分逐位相同 | 同答案同分 |
| A6 registry_covered | 注册表 = 套件题集；每题 ≥1 变异体；每题 ≥1 `wipe_*`；format=files 另需 ≥1 `flat_*` | 注册表不许漏题 |

- **变异体手写注册**（⛔ 不自动生成）：`wipe_*` = 状态/行为抹除（如每次调用
  清空、忽略 TTL）；`flat_*` = 拍平成单文件（无 FILES ⇒ 测试当场 NameError）。
- `verdict(readings)` 纯函数出 `{ok, failed[]}`；`admit()` 跑真打分器攒 readings。
- **A7 有牙自证**（探测项，进 PREREG P5 不进准入器本体）：构造四种坏读数
  （oracle≠1.0 / partial==null / 变异体不掉分 / 注册表漏题），verdict 必须全拒。

### 1.3 契约锚（AZ138，进 tests/contract_test.py，逐条做撤回态）

- a. `verdict()` 真值表 + fail-open（缺键/坏类型 ⇒ 拒绝）
- b. 注册表覆盖形状（A6 的静态面：题集相等、wipe/flat 齐全）
- c. 套件结构：6 题可载入、三臂材料非空、meta.format 封闭、files 题测试含
  物化前导、single 题测试不引用 FILES
- d~g. 视实现落点补（变异耦合面实测后定数，⛔ 不预写条数上限）

---

## §2 判据（主实验 probe_v176.py，P1~P7）

| # | 命题（注册方向） | 怎么算过 |
|---|---|---|
| P1 | 套件结构 | Suite.load 6 题、id 唯一、三臂材料非空、format 词表封闭 |
| P2 | 校准 | calibrate_or_raise（真 code_exec）过：6/6 两极可分 + 中段严格有序 + margin>0，逐题三臂读数入档 |
| P3 | 准入 | admit() 全绿：A1~A6 逐题过 |
| P4 | 变异体有牙 | 每题注册变异体全部 < oracle；wipe_* 对有状态题掉分（≤0.9）；flat_* 对 files 题 **0.0**（格式强制） |
| P5 | 准入器有牙（A7 反证） | 四种坏读数全部被判拒（oracle≠1.0 / partial==null / 变异体==oracle / 注册表漏题） |
| P6 | 部署态 | `run.py --bench --offline --solver oracle --suite sys_small.jsonl`（隔离 state/bench 目录）：退出码 0、逐题 oracle 满分、可信 |
| P7 | 确定性 | readings 双跑逐字相同（除时间字段）；verdict 纯函数双调用相同 |

⛔ 判据失败 ⇒ 原文不改、留档 SUPERSEDED、修装置或如实记 FAIL。

---

## §0.4 时间线登记（按 mtime 回填）

- STAGE0 侦察：2026-11-17 15:01–15:10（§0.1~0.2 读数）
- 本预注册落盘：2026-11-17 15:12:34
- 套件落盘：15:18:09｜准入器：15:20:58｜probe：15:22:41｜主实验读数：15:23:01
  ⇒ 主实验晚于本预注册 ✅

## §0.5 门禁（2026-11-17 回填，数字=实测）

- 契约 **712/0**（+AZ138a~f，706→712）+ R15 撤回态逐条定向演练 **6/6 精确转红**
  （AZ138d 连带 AZ138a 实测登记，日志 `withdraw_r15.log`；撤回态 274→**280**）
- 文档计数 0 漂移（契约 712×5 处 + 撤回态 280×4 处）
- `run_tests.sh` 全量门：单跑复核（步骤 5 自动覆盖 sys_small，含 oracle 满分校验）
- DESIGN §244 台账 + VERDICTS 重生成

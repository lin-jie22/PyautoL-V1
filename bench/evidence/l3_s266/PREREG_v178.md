# PREREG_v178 —— 行为向量落账（写侧）：分数账本行带上通过向量，L3 自省的前置

**轮次**：V178（§246，接 §245.7-①）
**落盘时间**：见文件 mtime（⛔ 主实验必须晚于 mtime，违者本预注册作废）
**装置**：`bench/scorers/code_exec.py`（发射 `vec`）+ `mw/bench.py`（`Scored.vec` +
传输校验 + `run_suite` 行）+ `run.py`（两处写侧落账）+ `mw/scoreinfo.py`
（`beh_pool_from_rows()` 读入口）+ `bench/probe_v178.py`（P1~P7）+
`tests/contract_test.py`（AZ140a~g）+ `bench/withdraw_check.py`（R17）
**证据**：本目录 `readings_v178.json`、`withdraw_r17.log`

---

## §0 STAGE0（先量后动 —— 以下读数全部早于本文件落盘）

### 0.1 向量现在在哪丢的（缺口本体）

`bench/scorers/code_exec.py` 的 `main()` 逐条 test 独立子进程跑（`run_one()`，
code_exec.py:71）**已算出**逐测试通过与否，但输出协议只发标量
`score = passed / len(tests)`（code_exec.py:127，`round(score, 4)` 发射）——
向量在打分器出口被丢弃。生产写侧（run.py:2552 账本行、run.py:3017 goal_obs 行）
只见过标量 ⇒ `mw/scoreinfo.behavioral()`（V177）只能靠探针侧重新跑 `run_one`
取数（probe_v177.py:274），**账本自己喂不出行为级读数** —— 这就是 §245.7-① 的缺口。

### 0.2 写侧形状（V175 impl_sha 落账的同款路径，照抄形态）

- 账本行（run.py:2552–2559）：`{session, family, task_id, score, passed,
  caliber_sig, caliber, candidate, impl_sha}` —— impl_sha 按「捕到才写键」模式。
- goal_obs 行（run.py:3014–3023）：`{task_id, score, session, source, impl_sha}`
  —— 同款 dict-spread。
- `ScoreLedger.append`（mw/ledger.py:290）只拒**缺必需字段**的行
  （`REQUIRED_FIELDS = (family, passed, caliber_sig)` + 写侧 `caliber`），
  **新键不拒** ⇒ 落 `vec` 不触写闸。
- `run_suite` 的 `per_task` 行（mw/bench.py:810）：`{rep, id, score, why,
  elapsed_s}` —— `Scored`（mw/bench.py:264，frozen）现为 `(score, why, detail)`；
  `detail` 的传输口径（mw/bench.py:344–356）正是可选字段先例：
  **缺席合法（老打分器），给了但格式坏 ⇒ ScoreError（fail-closed，不许静默丢）**。

### 0.3 发射精度（自洽闸容差的来源）

`code_exec.main()` 发射 `round(score, 4)`（code_exec.py:127）⇒
`|round(x,4) − x| ≤ 5e-5`（1e-4 量子的半宽，恰取到）。读侧自洽闸容差取
**5e-5 + 1e-12**：对 4 位发射器与精确发射器同过；翻转一个向量元素的偏差
（≥ 1/n_tests）远超此值。⚠️ 闸必须先排 **bool/非数值/NaN/inf** 再比差
（NaN 的比较恒 False，会从闸里漏过去）。

### 0.4 传输面长度闸的位置（与读侧形态闸的分工）

`ExternalScorer.score()` 手里有 `task` ⇒ 能校验 `len(vec) == len(task["tests"])`。
读侧账本行**没有测试条数** ⇒ 长度不齐只能靠自洽闸间接发现。
⇒ **长度闸只在传输面**；读侧只有形态闸（bool 列表、非空）+ 自洽闸。

### 0.5 T3（注册定理，契约钉）：账本行自洽 ⇒ 同行为同分是算术必然

若池中每行 `score == mean(vec)`（容差内），则同 vec ⇒ 同 mean ⇒ 同 score
⇒ `behavioral()` 的冲突闸（BEHAVIOR_SCORE_CONFLICT）对**适配器喂入**的池
**结构上不可达**（与 V175 覆盖定理同型的「实测到不了」）。
冲突闸对**跨源池**（vec 与 score 来自不同测量）仍然有效 —— 拆闸撤回必须两头都验：
自洽池不弹（负对照）、跨源池照弹（闸没被删）。

⚠️ 推论（决定闸的位置）：per-row 自洽闸**不许**放进 `behavioral()` ——
V177 的冲突夹具恰恰是「同行为异分」的逐行组合（AZ139c/d：
`("a",(T,F),0.5),("b",(T,F),0.7)` 中 b 行自身 `0.7 ≠ mean=0.5`），
分类器里加逐行自洽闸会把冲突检测**拆掉**。⇒ 自洽闸只住读入口
（账本行的 vec 与 score 是**同一测量的两种渲染**，行内矛盾=损坏）；
`behavioral()` 保持纯分类器（跨源观测的矛盾检测器）。

### 0.6 真 e2e 的取数路径（罐头 HTTP，真 CLI）

`run.py --bench` 的 solver 里 **只有 `llm-direct` 路径**能离线出「带代码的答案」
（null/oracle/partial 被账本写侧排除，run.py:2539；auto 离线走内核 StubLLM，
罐头响应不带代码 ⇒ 无代码答案 ⇒ 不发 vec）。`llm-direct` 要求
`s.llm.usable()`（run.py:2321）⇒ 探针起本地 `http.server` 罐头端点
（HTTPLLM 协议 mw/llm.py:91–126：`POST {base_url}/chat/completions`，
响应 `{"choices":[{"message":{"content": ...}}]}`）+ 环境变量
`V1_LLM_BASE_URL/API_KEY/MODEL`，用真 CLI、真写侧、真打分器跑 sys_small
（尺子自证材料 oracle 6/6=1.00 / partial 中段 —— V176 已准入）。
隔离照 run_tests.sh 步骤 5a：`V1_STATE_DIR` + `V1_BENCH_DIR` + `--ledger` 全指 tmp。
不同罐头答案连跑 ⇒ 同格多实现进池（P6 要的池深度）。

⚠️ goal_obs 写侧（`--goals` 路径）功能 e2e 本轮**不做**（路径重、需 goals-act
全链）—— AZ140d 用 AST 锚钉住两处写点，功能验证登记为缺口（§4）。

### 0.7 时间线登记（按 mtime 回填）

| 时刻 | 事件 |
| --- | --- |
| 17:18 | 本 PREREG 落盘（主实验于此之后方可跑） |

---

## §1 判据唯一来源（装置定义 —— 实现只许照抄，不许改语义）

### 1.1 发射面（code_exec，score 公式一字不动）

- 有可执行代码：输出 JSON 增加 `"vec": [bool, ...]`，`len == len(tests)`，
  逐元素即 `run_one` 的通过与否；`score` 仍是 `passed/len(tests)` 再
  `round(,4)`（**T1 发射自洽**：`score == round(mean(vec), 4)`，恒真，因为
  score 本来就是 mean）。
- 无可执行代码（`extract_code` 为空）：**不发 `vec` 键** —— 测试根本没跑，
  「没量到」不许拿全零冒充「测过了全挂」（契约③：缺失不得被读作否定）。
- `exit 2`（无 tests）路径不变。

### 1.2 传输面（ExternalScorer，照 `detail` 先例）

- `vec` 缺席 ⇒ `Scored.vec = None`（老打分器合法，0 行为）。
- `vec` 在场 ⇒ 必须是 list/tuple、元素全 `bool`、`len == len(task["tests"])`；
  违者 **ScoreError**（fail-closed —— 静默丢 = 一句"我不对"又变回没有理由）。
- `score/why/detail` 的解析行为**一字不变**（旧协议回归）。
- `Scored` 增字段 `vec: tuple[bool, ...] | None = None`（frozen 尾部追加，
  位置构造兼容）。

### 1.3 跑题链（run_suite → per_task 行）

`Scored.vec is not None` ⇒ 行带 `vec`（原值）；`None` ⇒ **键不在**
（与 impl_sha 同款：缺席是缺席，不是 None 值）。

### 1.4 写侧落账（run.py 两处，impl_sha 同款「捕到才写键」）

- 分数账本行（run.py:2552 起）：`vec` 捕到才写（JSON 列表）。
- goal_obs 行（run.py:3017/3021）：同款。
- ⛔ 不改 `passed`/`score`/`caliber` 任何字段（T1 落账不改值）。

### 1.5 读入口 `beh_pool_from_rows(rows, *, cell=None, ...) -> (pool, counts)`

- 出池元素：`(impl_sha: str, vec: tuple[bool, ...], score: float)`。
- 逐行闸（**顺序固定**，先红先计，一行只进一个桶）：
  1. 非 dict ⇒ `n_bad_row`（仅 `cell=None` 全局口径计；逐格无从归属不计）；
  2. `cell` 给定且 `task_id != cell` ⇒ 跳过不计；
  3. 缺 `impl_sha`/缺 `score` 键 ⇒ `n_no_impl`（与 `pool_from_rows` 的 n_no 同义）；
  4. `vec` 键缺席或 None ⇒ `n_no_vec`；
  5. vec 非 list/tuple / 元素非全 bool / 空 ⇒ `n_bad_vec`；
  6. 分数非数值/bool/NaN/inf，或 `|score − mean(vec)| > 5e-5 + 1e-12`
     ⇒ `n_inconsistent`（分数坏到没法对账也算对不上账 —— 细分归因属
     `behavioral()` 的 RULER_BROKEN，本适配器只保证「过闸的行可信」）；
  7. 过闸 ⇒ 入池，`n_kept += 1`。
- **fail-open**：任何坏行只计数排除，绝不抛。
- 纯函数：无全局状态、同入同出。
- ⛔ 适配器**不校验** `len(vec) == 测试条数`（行里没有测试条数，§0.4）。

### 1.6 注册定理（契约钉）

- **T1（发射自洽）**：code_exec 发射的每行 `score == round(mean(vec), 4)`
  （无代码路径无 vec，不适用）。
- **T3（冲突闸对适配器池不可达）**：适配器出池的行逐行自洽 ⇒ 同 vec 必同分
  ⇒ `behavioral()` 的 BEHAVIOR_SCORE_CONFLICT 对此类池结构上不触发；
  跨源池（行内 vec 与 score 不同源）仍触发。⚠️ 这是「测不到」定理，
  撤回态两侧都要测（自洽池不弹 = 负对照；跨源池弹 = 闸还在）。

---

## §2 判据（probe_v178，逐条先注册后跑）

- **P1 发射面**（子进程真跑 code_exec）：
  (a) 2-test 任务 + 含代码答案 ⇒ 输出含 `vec`、恰 2 元素全 bool、
      `score == round(mean(vec), 4)`（T1）；
  (b) 无可执行代码答案 ⇒ **无 `vec` 键**、score=0.0；
  (c) 逐测试独立性：code 只过第一条 ⇒ `vec == [true, false]`、score==0.5。
- **P2 传输面**（ExternalScorer × `python3 -c` 罐头打分器）：
  (a) 合法 vec ⇒ `Scored.vec` 逐元素还原；
  (b) 缺 vec ⇒ `None`（旧打分器合法）；
  (c) 非列表/元素非 bool/长度≠len(tests) ⇒ ScoreError（fail-closed）；
  (d) 旧协议回归：score/why/detail 行为逐字不变。
- **P3 跑题链**（run_suite × stub scorer × 1-task 套件）：
  vec 在 ⇒ per_task 行带 vec 原值；vec=None ⇒ 键不在。
- **P4 真 e2e**（run.py --bench --solver llm-direct × 罐头 HTTP × sys_small
  × 隔离 tmp）：
  (a) 账本行含 `vec` 且与 score 自洽（逐行过 §1.5 闸）；
  (b) 写前写后 score 逐字不变（与罐头答案直接判分对拍，T1 落账不改值）；
  (c) 三种罐头答案连跑 ⇒ 同格多实现/多行为进池（池深度 ≥2 行为的格 ≥1）。
- **P5 读闸**（`beh_pool_from_rows` 变异臂）：no_impl / no_vec / bad_vec
  （非 bool、空）/ inconsistent（翻一元素、NaN、bool 分数）各计数精确、
  fail-open 不抛；容差边界：差恰 5e-5 收、5e-4 拒。
- **P6 真池回环 + T3**：P4 账本行 → `beh_pool_from_rows` → `behavioral()`
  分类，与探针侧直接收（run_one 逐条 + ExternalScorer 对拍）分类**一致**；
  T3 两侧：自洽池 CONFLICT 零触发（负对照）、跨源池照弹（闸没被删）。
- **P7 确定性**：同罐头答案双跑 ⇒ 两轮账本行除 session 号外逐字相同；
  `beh_pool_from_rows` 双读相同。

⛔ 判据跑后不许改装置迁就（V174 P3 教训）；读数落 `readings_v178.json`。

---

## §3 契约与撤回态（AZ140a~g / R17，单因变异、精确转红）

| 契约 | 钉什么 | 撤回（单因） |
| --- | --- | --- |
| AZ140a | 发射面 + T1：有代码发 vec（bool 列表、len 对、自洽）；无代码不发 | 删发射键 ⇒ 红 |
| AZ140b | 传输面四臂（合法/缺席/坏形态/长度错）+ 旧协议回归 | 删格式校验 ⇒ 红 |
| AZ140c | run_suite 行：vec 在带值、None 不在键 | 行构造删 vec ⇒ 红 |
| AZ140d | 写侧两处各恰 1 处落 vec（AST 现算）+ ScoreLedger.accept 含 vec 行 | 删 run.py 落账 ⇒ 红 |
| AZ140e | 读闸四桶计数精确 + fail-open + 容差边界 | 删自洽闸 ⇒ inconsistent 行进池 ⇒ 红 |
| AZ140f | 向后兼容：旧行混读 movability 逐位不变、beh_pool 只收新行 | 对无 vec 行抛 ⇒ 红 |
| AZ140g | `beh_pool_from_rows` 纯函数 | 掺随机 ⇒ 红 |

COUPLED 如实登记（不许改实现躲耦合）；R17 定向跑 AZ140a~g + 受影响邻居复核。

---

## §4 边界（⛔ 不许放大）

1. **不动 `behavioral()`**（V177 纯分类器）：per-row 自洽闸只住读入口
   （§0.5 推论）；behavioral() 的输入契约 = (impl, vec, score) 裸观测。
2. **不动 `movability()`/`repair_axes()`**：L3 自省消费行为级读数是**下一刀**
   （§245.7-②/③），本轮只供读入口。
3. **assertion.py 打分器不发 vec**（一把尺子一种量）⇒ 语言域账本行无 vec
   （键缺席 = 「没量到」）。
4. **无代码答案不发 vec**（§1.1）；读侧 `n_no_vec` 计数它们。
5. **读侧不校验长度 == 测试条数**（§0.4）；长度闸只在传输面。
6. **goal_obs 写侧功能 e2e 本轮不做**（§0.6）—— AST 锚钉住，功能验证是缺口。
7. `repeat_n>1` 的逐 rep 行各自带 vec；账本 (session,task_id) 去重现语义不改。

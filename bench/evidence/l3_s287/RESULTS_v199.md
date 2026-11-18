# V199 结果：3059 条 episode 里，**0 条**能到达 prompt

预注册 `PREREG_v199.md`（写于任何新读数之前）。
装置 `arms_v199.py`（**0.6s**，零模型：StubLLM + `sys.settrace` 行级 + 库副本）。
读数 `readings_v199.json`（含 `called_mw_fns` 全部执行行）/ `states_v199.json`（state 原文）。
⛔ 未改内核。⚠️ **原库被写过一次**（不是「未动」—— 初稿这么写是错的，见自踩 6）：
`23:04:42` 第一次用 `--offline --goal … --memory-dir /tmp/v199_state` 试探时，
`--memory-dir` **在 `--goal` 路径下根本不生效**（它只在 `_bench_main` 里被处理，`run.py:2078`）
⇒ 那次把 4 条（id **3056~3059**：goal / outcome / failure / finish，内容都是 t03）写进了原库。
此后所有读数一律走 `/tmp/v199_state` 副本，原库再未变动。

---

## 1. 押注核对：**5 ✅ / 1 ❌**

| 号 | 押 | 实测 | 判 |
|---|---|---|---|
| P1 | `failure/goal/finish/mismatch`（2888 条）活读点 = 0 | **0** | ✅ |
| P2 | `outcome` 活读点 ≤ 2 且 limit ≤ 40 | **0 个活** | ✅（更强） |
| P3 | `reward` 读点存在但库里 0 条 ⇒ 永远读空 | 唯一活读点就是它，读到 **0 条** | ✅ |
| P4 | `abstractor.py:213` 部署闭包 **0 引用** | `mw.abstractor` **在** `sys.modules`（`core.py:37` import 了 `abstract_only`） | **❌** |
| P5 | state 里字面出现的 episode ≤ 45 | **0** | ✅ |
| P6 | facts 4 条里领域知识 = 0 | **0**（全是动作偏差/编码类元知识） | ✅ |

**P4 的形式押错了，结论是对的**：那个读点（`_source_episodes`，读 500 条全量）
**在整个 3 题 × 15 轮里 0 次执行**（见 §3 行级证据）—— 因为 `distill` 全库零调用点。
我押的是「闭包 0 引用」，而实际是「模块被 import（为了 `abstract_only`）、那条路径 0 执行」。
⇒ 这条押注**不成立要照实记**，不能拿「结论对」抵掉「押错」。

---

## 2. 写入 vs 可用

| 表 / kind | 写入 | 活读点 | 上限 | 实际到达 prompt |
|---|---|---|---|---|
| `episodes.failure` | **777** | 0 | — | **0** |
| `episodes.goal` | **738** | 0 | — | 0（state 里 9 次 `goal` 命中全是**当前题面**的历史重复） |
| `episodes.finish` | **737** | 0 | — | **0** |
| `episodes.mismatch` | **639** | 0 | — | **0** |
| `episodes.outcome` | **168** | 0 活 | — | **0** |
| `episodes.reward` | **0** | **1 活**（`worldmodel.py:626`，`limit=300`） | 300 | 0（表里没有） |
| **`facts`** | **4** | 1（`recall`） | `k=6` | **1 条** |
| `critique.jsonl` | 288 | — | — | **0**（`--critique-scope` 默认 off） |

> **可用量 = 1 / 3059 = 0.033%。**

⚠️ 分母口径：**3059** = `777+737+738+639+168`，分项与总数一致（`db_counts()` 在副本上、run 之前取）。
其中 id **3056~3059** 正是那次误跑写进去的 4 条 —— 已算在分母里，**不剔除**（剔除会让分母与库对不上）。
副本跑完 3 题后涨到 **3071** ⇒ ⛔ 拿 3071 当分母是错的。

---

## 3. L1：4 个读点，**1 个活**

判据 = **那一行真的被执行到**（行级 `settrace`，不是「模块被 import」，也不是「函数被调用」）。
tracer 自检：3 题共记录 **1258 行** mw 代码（`core.py` 725 / `planner.py` 122 / `verify.py` 86 /
`memory.py` 66 / `worldmodel.py` 63 …）⇒ 不是哑火。

| 读点 | 位置 | 判 | 为什么 |
|---|---|---|---|
| ✅ | `worldmodel.py:626` `episodes(kind="reward", limit=300)` | **活** | `reward_guidance()` 走到这里（**但前提是传了 `features`**，见自踩 4）；表里 0 条 |
| ⛔ | `abstractor.py:213` `episodes(limit=500)` | 死 | `distill` 全库零调用点；`abstractor.py` **一行都没执行** |
| ⛔ | `core.py:2837` `episodes(kind="outcome", limit=1)` | 死 | 在 `if not best:` **兜底分支**里；本次 `best` 非空（收尾调用给了 2 字符） |
| ⛔ | `worldmodel.py:434` `episodes(kind="outcome", limit=40)` | 死 | `_predict_by_retrieval()` 没被调用（预演走另一条路） |

⭐ 注意 `memory.py` 的 `episodes()` **方法体确实执行了**（行 405~420 命中）——
它就是被 `worldmodel.py:626` 那一次调进来的，`kind="reward"`，表里 **0 条**於是读到空 list。

---

## 4. L3：state 原文里到底有什么

3 题 × 5 轮 = 15 轮，正文 **6370 字符**。按内核的**实际渲染口径**匹配
（`[工作记忆]` 用 `content[-200:]`、`[语义记忆]` 用 `text[:120]`）：

| 通道标记 | 出现次数 | 内容 |
|---|---|---|
| `[工作记忆]` | **12** | 会话内工作记忆（`working_view`），**不跨题** |
| `[语义记忆]` | **5** | **1 条** fact（`t04_merge_ranges` 那 5 轮都召回同一条） |
| `[轨迹]` | 12 | 本题内历史 |
| `[外部评审]` | **0** | `--critique-scope` 默认 off |
| `[经验]` | 0 | `memory_mode=abstract` 才用 |
| `已知易错点` | 0 | `wm.guidance()` 全空（`recall` 里没有 `source=worldmodel` 的命中） |

逐 kind 字面命中：`failure 0 / finish 0 / mismatch 0 / outcome 0 / goal 9`。
**`goal` 那 9 次不是知识** —— 是 3 道题的题面在库里各有 3 条历史重复（同一题跑过 3 次），
匹配到的是**当前这题的题面本身**。

### 4.1 那 1 条 fact 是什么

```
python 写文件时应显式指定 encoding=utf-8，否则默认编码会在非 ASCII 上静默截断   （source=selftest）
```

- 它是**通用编码知识**，与 `merge_ranges` 无关；
- 它能被召回，是因为 `t04` 题面里有「写回/文件」类词，**FTS 词面偶然命中**；
- 另外两题 `recall` 返回 **0** 条。

⇒ 端到端能到达 prompt 的跨跑知识，**总共 1 条，且是偶然**。

---

## 5. 判决

1. **`episodes` 表是纯日志，不是累积。** 3059 条对上 0 条可达。
   ⇒ ⛔ 「累积 ✅ 闭」这句话必须加限定：**闭的是 `facts` + `critique` 这两条，不是 episodes**。
2. **本装置上跨跑累积的真实载体只有两处**：`facts`（4 条，1 条可召回）+ `critique.jsonl`（288 条，
   本采样里投喂 0）。而 `facts` 里 **0 条领域知识**。
3. ⭐ **这给 V101「换题面一格不剩」提供了一个结构性解释**（待验，⛔ 本轮不下结论）：
   `critique` 带 `task_id`，题面一换就召不回；`facts` 那 4 条与题目无关。
   ⇒ 若 L4 的 84% 来自 critique，那它测的是**同题记忆**，不是「累积变强」。
   ⛔ 本轮**没有**验证这条，只登记为可检验假设。
4. **`wm.guidance()` 在 3 题上全空**：`facts` 里 3 条 `worldmodel` 记录（动作偏差类）一条都没被召回；
   `wm.learn()` 在采样里 **0 次调用**（`worldmodel.py:learn` 未出现在执行行里）。

---

## 6. 自踩 7 条（同一轮里连踩；1~5 由正例对照逮住，6~7 由复核逮住）

1. **⛔ 模块级判据太宽**：第一版判「调用点所在模块 ∈ `sys.modules`」⇒ `mw.abstractor` 因为
   `core.py:37` import 了 `abstract_only` 而算「活」⇒ 4 个读点全报✅。
   改成**行级**才对（那一行真被执行到）。
2. **⛔ 匹配口径错**：第一版拿 `content` **整条**去 state 里找 ⇒ facts 命中 0。
   真口径是 `[语义记忆]` 用 `text[:120]`、`[工作记忆]` 用 `content[-200:]`（`_render`）。
   改对之后 facts 命中 **1**。⇒ 纪律「比两个字符串前先写清口径」的又一次落地。
3. **⛔ 函数级仍太宽**：改成「函数被调用」之后，`_close_out()` 算了活 —— 但那行读点在
   `if not best:` 兜底分支里，本次没走。⇒ 只有**行级**能回答「有没有执行到」。
4. **⛔⛔ 装置少传 `features`**（最重的一条）：第一次跑没给 `features`，`reward_guidance()`
   在 `if not features: return ""` 就返回了 ⇒ 我把**唯一活的读点**判成了死。
   补上 `feature_set(task)` 之后它才活。⇒ 与纪律 75「测量臂关掉的功能，先查部署里有没有」同型：
   **不传参数等于把通道关掉，然后去量它死没死**。
5. **正例对照选错行**：第一版拿函数**定义行**（`memory.py:464`）当对照 ⇒ 永远「没执行到」
   （定义行只在 import 时跑一次）。改成「按文件统计被记录行数」才证明 tracer 没瞎。
6. **⛔⛔ 原库被我写脏了，而初稿写的是「原库未动」。** 机制：`--memory-dir` 只在 `_bench_main`
   里处理（`run.py:2078`），**`--goal` 路径忽略它** —— 于是那次「在副本上试探」实际写进了
   `state/memory.db`（id 3056~3059）。⚠️ 这是本轮**唯一改了产品状态**的操作。
   教训：**验证「隔离生效」要看目标文件 mtime，不是看命令行有没有那个参数**（纪律 77 同型）。
7. **⛔ 分母写成旧数**：初稿写 3055（那是更早一次只读 `facts` 时顺手看的数），真值 **3059**。
   分项 `777+737+738+639+168 = 3059` 自洽 —— 我甚至在正文里列了分项却没做加法。
   ⇒ 纪律「分项要与总数对一遍」：**列了分项就要加一次**。

---

## 7. 作用域（⛔ 越界即作废）

只覆盖：StubLLM × 3 题 × 15 轮 × 部署默认（`raw` 模式、`critique-scope=off`）× 本机这份 3059 条的库。
⛔ 不覆盖真模型（路径更长、可能触发兜底）、⛔ 不覆盖 `memory_mode=abstract`、
⛔ 不覆盖 `--critique-scope=prev/all`（打开后 `[外部评审]` 会活，那是另一条通道的账）。

## 8. 下一刀（交用户）

① 把「episodes 是日志、累积在 facts+critique」写成约束，并量 **critique 通道**在真 bench 下的投喂量；
② 验 §5.3 那条假设：L4 的 84% 到底来自 critique 还是 facts（需要区分实验）；
③ 判「episodes 写端」是否该关（3059 条、0 可得，占 5.5 MB）。

## 9. 门禁

契约 **781/0**、锚层 **97/0**、撤回态 **339**、`check_counts` 0 漂移、`run_tests.sh` EXIT=0。
（零代码改动，只加证据与文档。）

# knowledge_s37 —— §17-7i 证据自述

**这一轮没有跑对照臂，也没有产生任何 Δ。** 它只做一件事：
把 7h 记在明面上的那条缺陷（`recall` 工具成功时返回**全部** facts）的根拔掉，
并把"拔掉之后各条通道到底给几条"量清楚。

冻结时间：2026-10-19 20:26–20:30（GMT+8）

---

## 文件清单

| 文件 | 怎么来的 | 说明 |
|---|---|---|
| `probe_channels_new.txt` | `python3 bench/probe_7i.py --seed bench/evidence/knowledge_s34/seed_H.db --heldout bench/heldout/g7e_hint_heldout.jsonl --facts 7f` | **新口径**（只 FTS+LIKE）的三段读数 |
| `probe_channels_old.txt` | 同上 + `--with-fallback` | **旧口径** A/B：`--with-fallback` 只在探针进程内把 `recall()` 换回三级版本，**不改仓库实现** |
| `probe_state_r1.txt` | `python3 bench/probe_state_r1.py --states smoke_states.jsonl --heldout suite.jsonl --facts 7f` | 真模型冒烟的 state 两层审计（三态：送达 / 零命中 / 泄漏） |
| `smoke.log` | `run.py --bench --solver llm … --state-dump` | 2 题 / qwen2.5:7b / T=0 / 7f 种子库 |
| `smoke_runs.jsonl` | `run.py` 落盘的原始 runs | 分数、逐题 `elapsed_s`、`kernel_transport` |
| `smoke_states.jsonl` | 同上 `--state-dump` | 每次 `_state()` 一行 |
| `suite.jsonl` | 从 `bench/heldout/g7e_hint_heldout.jsonl` 取 `g7d2_dedupe_keep` + `g7e2_truncate_tail` | 与 run 用的**同一份** |

R13 收口日志另存 `R13_closeout.log`（全量 90 条撤回态）。

---

## 三组读数（本轮的结论全在这里）

### ① 题面到底命中几条

| 题 | 新（仅 FTS+LIKE） | 旧（含兜底） |
|---|---|---|
| `g7d2_dedupe_keep` | **0** | 4 |
| `g7d3_fmt_amount` | **0** | 4 |
| `g7e1_parse_flag` | 1 | 1 |
| `g7e2_truncate_tail` | 1 | 1 |

⇒ 7d–7g 记的 `4/4/1/1` 里，**两个 4 全部来自第三级全表兜底**；
前两题的题面与四条 facts **FTS 零交集**。那个族应改名为**零命中族**。

### ② 短 query 对照（反直觉的一格）

"去重"/"金额"/"开关"/"截断" 在**新旧口径下都是 1 条**。
⇒ 兜底只在**长 query（题面）**上触发 —— 短 query 是 facts 的连续子串，FTS 本来就命中。
这也解释了 7h 为什么只在"工具通道"看到全表：模型调 `recall` 时传的是**题面**。

### ③ 逐题逐步的 state 审计（StubLLM，零模型调用）

必须把「**非本题**漏入」与「本题自己重复出现」分开数：

| 族 | 口径 | 第 2–4 步非本题漏入 |
|---|---|---|
| 零命中族 `g7d2`/`g7d3` | 旧（含兜底） | **有**（`g7d2` 每步 2 条、`g7d3` 每步 1 条） |
| 零命中族 `g7d2`/`g7d3` | 新（仅 FTS+LIKE） | **无** |
| 1 条族 `g7e1`/`g7e2` | 旧（含兜底） | **无**（只有它**自己**那条重复出现） |
| 1 条族 `g7e1`/`g7e2` | 新（仅 FTS+LIKE） | **无**（同样只有自己那条重复） |

第 1 步在两种口径下都干净 —— 泄漏发生在**模型调过 `recall` 之后的第 2 步**。
「本题重复」不是邻域：那是同一条事实在 `[语义记忆]` 与 `[工作记忆]` 各出现一次。

---

## 真模型冒烟（2 题）

| 题 | 语义块 | 第 2 步起非本题漏入 | 分数 | 墙钟 |
|---|---|---|---|---|
| `g7d2_dedupe_keep` | **空**（零命中，合法） | 无 | 0.2 | 53.35 s |
| `g7e2_truncate_tail` | 1 条 | 无 | 0.2 | 43.40 s |

传输层：`抖动=0 放弃首发=0/重试=0 上限被裁短=9`。

⛔ **这两个 0.2 不能当"记忆无效"的证据**：零命中族的语义块本来就是空的。
⛔ 也不能当"通道有效"的证据：n=1，且 7d–7g 的可比性已被登记为不成立。

---

## 复现

```bash
cd /Users/linshaojie/V1
python3 bench/probe_7i.py --seed bench/evidence/knowledge_s34/seed_H.db \
    --heldout bench/heldout/g7e_hint_heldout.jsonl --facts 7f
python3 bench/probe_7i.py --seed bench/evidence/knowledge_s34/seed_H.db \
    --heldout bench/heldout/g7e_hint_heldout.jsonl --facts 7f --with-fallback
python3 tests/contract_test.py          # 515/0
python3 bench/withdraw_check.py         # 90 条逐条撤回
```

---

## 如实登记的四件事

1. **事前登记的预期被自己的实测推翻**：写死"四题仍是 4/4/1/1"，实测 0/0/1/1。
   因为预期写在**动手之前**，所以能当场改判，而不是事后圆场。
2. **两条老断言原本站在兜底上**（AN6 / AY3）：判据一个字没改，改的是喂进去的 query。
   AN6 另外把判据从"整段 state"挪到"`[经验]` 块"——新 goal 本身就含两条原文，
   用整段判等于把"目标里写了什么"当成"记忆送了什么"（AZ19 同宗）。
3. **探针第一版把两件事合成一个布尔**：`probe_state_r1` 把"没送到"与"泄漏"都判红，
   于是 `g7d2` 被报成"跨题残留仍在"，方向刚好相反。已改成三态。
4. **本轮的读数被重算过一次**：`Planner._fallback` 的 `{"query": "goal"}` 是**字面量**
   （第三处根因），修掉它之后工具通道的 query 才真的等于目标文本。第一版判读写的是
   修之前的数据（"四题都有邻域"），已按修之后的数据改写为"**只有零命中族有邻域**"。
   ⛔ 这条记录保留，因为它说明"读数必须在装置定稿之后取"。

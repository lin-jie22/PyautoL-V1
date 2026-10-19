# §17-7f 冻结证据（第三十四轮，2026-10-19）

**一句话**：把三条**未被采用**的项目惯例改成「原句 + 一个输入→输出例子」，
**采用集合零重叠**地换了（7e={g7d2} → 7f={g7e2}），Δ_H 仍是 +0.20；
用 7e 原样装置重跑证明 `g7d2` 的 1.00 可复现
⇒ 7f 里它的 0.20 是**邻域效应**：同一处理、同一题面、T=0，只把邻域 182 → 291 字，
**单题极差 0.80、均值 0.20 = 信号本身**。⇒ §17-7f **记 0（不认领）**。

## 装置（可复现）

```bash
# 1) 建三臂种子（7f 变体）+ R1 双层前置
python3 bench/build_g7d_seed.py --out-dir /tmp/seed \
  --heldout bench/heldout/g7e_hint_heldout.jsonl --variant 7f
python3 bench/probe_g7d_state.py --seed /tmp/seed/memH/memory.db \
  --heldout bench/heldout/g7e_hint_heldout.jsonl --facts 7f

# 2) 三臂 × 3 repeats（题面 = 7e 的 hint suite，逐字节相同）
V1_SUITE="$PWD/bench/heldout/g7e_hint_heldout.jsonl" \
  bash bench/run_g7d.sh /tmp/seed /tmp/out

# 3) 判读（判据/阈值与 7d、7e 一字未改）
python3 bench/probe_g7d.py --evidence /tmp/out \
  --heldout bench/heldout/g7e_hint_heldout.jsonl --facts 7f

# 4) 复现检查（7e 原样装置、H 臂单次）
python3 bench/build_g7d_seed.py --out-dir /tmp/repro/seed \
  --heldout bench/heldout/g7e_hint_heldout.jsonl --variant 7d
bash bench/repro_g7e_arm.sh /tmp/repro/seed /tmp/repro/out H
```

## 读数

| 题 | H（3 次） | P（3 次） | E（3 次） | 7f H 交付写法 | 7e H（对照） |
|---|---|---|---|---|---|
| `g7d2_dedupe_keep` | 0.2 / 0.2 / 0.2 | 0.2 ×3 | 0.2 ×3 | `seen_add` 精确匹配（**与 E 臂逐字相同**） | **1.0 ×3**（`x_normalized`） |
| `g7d3_fmt_amount` | 0.2 ×3 | 0.2 ×3 | 0.2 ×3 | `"{:,.2f}"` | 0.2 ×3 |
| `g7e1_parse_flag` | 0.2 ×3 | 0.2 ×3 | 0.2 ×3 | `('true','yes','1')` 为真 | 0.2 ×3 |
| `g7e2_truncate_tail` | **1.0 ×3** | 0.2 ×3 | 0.2 ×3 | `'…' + s[-n:]` | 0.2 ×3（`s[:n]+'...'`） |

mean H=0.4000 / P=0.2000 / E=0.2000；Δ_H=+0.2000、Δ_P=+0.0000、R-noise=0.0000、R7 过。
**复现检查**：7e 原样装置 H 单次 ⇒ `g7d2` **1.0**、其余 0.2（逐位等于 7e）。

## 文件

| 文件 | 是什么 |
|---|---|
| `H_r{1,2,3}_runs.jsonl` / `P_*` / `E_*` | 九次原始读数（`per_task` 逐题分数） |
| `H_out_r{1,2,3}.log` / `P_*` / `E_*` | 会话日志 |
| `seed_{H,P,E}.db` | 起跑库（R1 重放用；sha256 见 `sha256.txt`） |
| `final_{A}_r{R}.db` | 跑后库（`episodes(kind='finish')` 里有交付原文 ⇒ 机制证据） |
| `repro7e_*` | 7e 原样装置的复现检查（种子/跑后库/读数/日志/它自己的 sha256） |
| `suite.jsonl` + `sha256.txt` | 三臂共用的**同一个**题面文件及其 sha256（`696d812d…`） |
| `probe_state.txt` | state 级送达探针输出（惯例逐字在 `[语义记忆]` 块） |
| `probe_out.txt` | 判读探针原始输出 |
| `R13_closeout.log` | 全量 R13（76 条）收口日志：逐条精确转红、还原后基线 501/0 |

`FILES.sha256` 是本目录 41 份文件的逐份 sha256（不含它自己）。

## 一次未被定位的干扰（如实登记）

第一次全量 R13（15:18–15:37）**被驱动器自己的 guard 判为作废**：
`tests/contract_test.py` 在跑动期间被改动。复查发现文件里多出一批**语义等价**的重构
（`_AP13_HITS` / `_AP14_COUNT` / `_AP15_*` / `_AS22_*` / `_AS24_LANDED` 之类的
"先把 f-string 里的表达式算成变量"），**不是本轮任何脚本写的**：
`withdraw_check.py` 的 CASES 里没有一条指向 `tests/contract_test.py`
（76 条的目标文件分布：`mw/core.py` 27、`bench/build_g7d_seed.py` 9、…），
并且用 `sys.addaudithook` 监听 open/copyfile/rename 复跑单条，**未见任何写操作**。
⇒ 来源**未定位**；guard 的判定是对的（"边跑边改 ⇒ 读数作废"），所以按纪律重跑：
第二次全量（15:50–16:09）**76/76 通过、文件 sha 前后一致（`f81c939c…`）**。
⚠️ 这不是"重试一次就绿"：作废的那次已经写在上面，重跑的合法性来自
"文件在整轮中没有变化"，不是来自"结果更好看"。


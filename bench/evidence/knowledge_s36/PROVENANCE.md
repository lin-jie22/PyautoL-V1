# knowledge_s36 —— 第三十六轮（§17-7h）**修装置**的证据包

这一轮**没有跑对照臂**，所以这里没有 `*_runs.jsonl` 的格子读数可读。
本轮要证明的是"**装置能不能用**"，不是"通道有没有效"。
⛔ 不许把本目录的任何数字读成 Δ、增益或能力结论。

## 本目录里有什么

| 文件 | 是什么 | 怎么复算 |
|---|---|---|
| `smoke.log` | 一次**真模型**（qwen2.5:7b / T=0 / budget 1x）两题冒烟的内核日志 | 见下"复算" |
| `smoke_runs.jsonl` | 同一次冒烟落盘的 `runs.jsonl`（含 `kernel_transport`、逐题 `elapsed_s`） | 同上 |
| `smoke_states.jsonl` | 同一次冒烟的 `--state-dump`（每步一行，共 2 行 = 每题 1 步） | `python3 bench/probe_state_r1.py --states …` |
| `suite.jsonl` | 冒烟用的两题（从 `bench/heldout/g7e_hint_heldout.jsonl` 里按 id 取的**逐字节原样**） | `shasum -a 256` |
| `sha256.txt` | 种子库与 suite 的 sha256（起跑前） | `shasum -a 256 -c` |
| `probe_state_r1.txt` | R1 **第二层**在冒烟 dump 上的判定：第 1 步跨题残留 = 0 | `python3 bench/probe_state_r1.py --states bench/evidence/knowledge_s36/smoke_states.jsonl --heldout bench/evidence/knowledge_s36/suite.jsonl --facts 7f` |
| `probe_g7d_state.txt` | 两层状态探针在 **7f 种子库** 上的判定（4 题全绿） | `python3 bench/probe_g7d_state.py --seed bench/evidence/knowledge_s34/seed_H.db --heldout bench/heldout/g7e_hint_heldout.jsonl --facts 7f` |
| `transport_s35.txt` | 运输层签名在 **s35** 冻结证据上的重算：**恰好 3 格**路径不同 | `python3 bench/transport.py --root bench/evidence/knowledge_s35` |
| `probe_7h_control.txt` | **反证组**：同装置、同种子、只把清空换成 no-op ⇒ 第 2 题第 1 步立刻脏 | `python3 bench/probe_7h_control.py` |
| `R13_closeout.log` | 全量撤回态（**86 条**，含本轮新增 AZ17–AZ21） | `python3 bench/withdraw_check.py` |

## 冒烟读数（原样，不作任何归因）

| 项 | 值 |
|---|---|
| 模型 / 温度 / 预算 | `qwen2.5:7b` / `0.0` / `1x`（`max_wall_s=120`） |
| 逐题墙钟 | **31.29 s**（`g7d2_dedupe_keep`）、**21.15 s**（`g7e2_truncate_tail`） |
| 整轮 `elapsed_s` | 52.45 s（两题之和 52.44 ≤ 52.45） |
| 分数 | 0.2 / 0.2 —— ⛔ 与 7e/7f 不可比：题不同、种子只 2 条、n=1，**不是**复现也不是反证 |
| 题边界清空 | `🧹 题边界：清掉上一题残留的工作记忆 1 条` |
| 传输层 | `抖动重试=0 重试后仍失败=0 上限被裁短=6 墙钟放弃首发=0/重试=0` |

## 两条必须一起读的事实

1. **跨题残留已修**：第 2 题**第 1 步**的 state 里，`[语义记忆]` 之外没有任何惯例文本
   （`probe_state_r1.txt`）；反证组证明这份干净**来自清空**，不是"恰好没调 recall"。
2. **同一题内的第二条路仍在**：只要本题调过 `recall`，第 2 步往后的 `[工作记忆]`
   就会出现另外几条惯例（`probe_7h_control.txt`：4 步的题里稳定 2/3 条）。
   它不是跨题污染，但它是"1 条族的上下文里没有邻域"这句话的**真正反例** ——
   7i 必须先决定（改 `recall` 口径 / 把 1-条族从"无邻域"里划掉），否则读数仍然不可归因。

## 复算冒烟（需要本机 ollama）

```bash
cd /Users/linshaojie/V1
rm -rf /tmp/v1_7h_ev && mkdir -p /tmp/v1_7h_ev/mem /tmp/v1_7h_ev/bench
cp bench/evidence/knowledge_s34/seed_H.db /tmp/v1_7h_ev/mem/memory.db
cp bench/evidence/knowledge_s36/suite.jsonl /tmp/v1_7h_ev/suite.jsonl
V1_BENCH_DIR=/tmp/v1_7h_ev/bench V1_STATE_DIR=/tmp/v1_7h_ev/mem \
V1_LLM_BASE_URL=http://localhost:11434/v1 V1_LLM_API_KEY=ollama \
V1_LLM_MODEL=qwen2.5:7b NO_PROXY=127.0.0.1 no_proxy=127.0.0.1 \
python3 run.py --bench --solver llm --suite /tmp/v1_7h_ev/suite.jsonl \
  --no-external --memory-dir /tmp/v1_7h_ev/mem --budget 1 \
  --state-dump /tmp/v1_7h_ev/states.jsonl
```

⛔ **不要**把这一次冒烟的分数与任何历史读数相比：题数、种子内 facts 数、n 都不同。

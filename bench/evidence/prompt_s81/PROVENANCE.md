# 证据：prompt 是确定性开关（DESIGN §44，第八十一轮）

## 一、这一发在问什么

promote 之后想回答「这套尺子上还剩什么可动」，于是先跑 direct 参照臂。得到：

```
direct（--solver llm-direct）：0.2738 = 23/84
内核（verify_static=2 默认）  ：0.6905 = 58/84
```

看起来「内核大胜 +0.4167」。**但一个内核外的预演对不上**：同一模型做 `sg06` 拿到 12/14，
direct 臂只有 1/14，同一模型、同一道题、同样 T=0。

⇒ 于是逐层剥：**这不是「有没有内核」的差，是「两条链路的 system prompt 不同」的差。**

## 二、命令（可重放）

```bash
# ① direct 参照臂，5 次独立进程（同口径：--fresh --no-external -T 0）
export V1_LLM_BASE_URL=http://127.0.0.1:11434/v1 V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b
for i in 1 2 3 4 5; do
  V1_BENCH_DIR=/tmp/v1_direct_s80 V1_STATE_DIR=/tmp/v1_direct_state \
  python3 run.py --bench --suite bench/specgen/tasks_v1.jsonl --solver llm-direct \
    --fresh --no-external --temperature 0 --repeat 1 --no-ledger > r$i.log 2>&1
done

# ② 单变量探针（只换 system prompt 一句话，每题每个 prompt 各调两次）
python3 bench/evidence/prompt_s81/s81_prompt_probe.py | tee probe_run.log
```

`V1_BENCH_DIR` / `V1_STATE_DIR` 指到 `/tmp` 是刻意的：**direct 臂的记录不该进主台账**，
否则会与内核臂混在一个 `sig` 池里（`Caliber` 不含 solver，两臂 `sig` 相同 —— 见 §44.6-3）。

## 三、读数

| 题 | 内核 `2` | `DIRECT_SYSTEM` | `PLAIN_SYSTEM` | 逐题 max |
|---|---|---|---|---|
| `sg01` | **12** | 6 | 1 | 12 |
| `sg02` | **10** | 0 | 7 | 10 |
| `sg03` | **14** | 1 | **14** | 14 |
| `sg04` | **14** | **14** | 1 | 14 |
| `sg05` | 0 | 1 | **14** | 14 |
| `sg06` | 8 | 1 | **14** | 14 |
| **合计 /84** | **58** | 23 | 51 | **78** |
| 均值 | 0.6905 | 0.2738 | 0.6071 | **0.9286** |

- **只换 system 一句话**：`sg03` 1↔14、`sg05` 1↔14、`sg06` 1↔14、`sg04` 14↔1 ⇒ 单题 13 格翻转。
- **可复现**：每个 prompt 每题跑两次，**12 次调用全部同分**（`probe_run.log` 里 `两次同分 = True`）；
  direct 臂 5 次也逐字相同（`r1..r5.log` 各 2939 字节，`mean` 全 0.2738）。
- **不是噪声**：T=0 下它是**确定性开关**，不是采样抖动。
- **选择器值 20 格**：逐题 max 78 vs 最好的单一链路 58 ⇒ 0.2381。这是"上界"，
  挑中需要 oracle（尺子，不是能力）。

## 四、三个必须一起引用的否定

1. **第七十二轮 §35.1 的 0.2857 不是「裸模型能力」**，是「`DIRECT_SYSTEM` 下的读数」。
   那条 Δ=+0.3095 应读作「两条特定 prompt 的差」。原读数保留，引用必须带这一条（§35.1 已补注）。
2. **第八十轮 `KEEP` 的可迁移性未知**（不是被否定）：+11 格的机制是「多一轮 replan」= 换 prompt，
   而 prompt 对单题的效应（±13 格）大于那 11 格。`KEEP` 收窄为「在 `sg01` 上有效、T=0 可复现」。
3. **「内核 vs 裸模型」在现有尺子上无法回答**：两个臂都是「某个 prompt」。

## 五、边界

1. 单模型（qwen2.5:7b 本地）、单温度（T=0）、6 题 × 14 用例。
2. 只测了**两个** system prompt —— 两个点证不了曲线，只证「差 28 格」这个事实。
3. 内核臂的逐题格数取自 `bench/evidence/evolve_s80b/pool_cand.jsonl` 的 10 次（逐字相同，取一次）。
4. 本目录**不改内核、不改 `DIRECT_SYSTEM`**。改它就是 prompt 工程，
   而 `run.py:144` 明确写着「一旦开始教解题，量到的就不是模型能力，而是提示词工程的水平」。

## 六、文件

| 文件 | 内容 |
|---|---|
| `s81_prompt_probe.py` | 单变量探针（Q1 三臂 / Q2 可复现 / Q3 选择器价值） |
| `probe_run.log` | 探针完整输出（12 次调用，全部两次同分） |
| `r1..r5.log` | direct 参照臂 5 次完整 stdout（各 2939 字节，逐字相同） |
| `direct_arm_runs.jsonl` | direct 臂 5 条原始台账记录 |
| `direct_arm_answers.jsonl` | direct 臂答案全文（**出错点可复核**，§42 的通道） |
| `PROVENANCE.md` / `FILES.sha256` | 本文件与指纹 |

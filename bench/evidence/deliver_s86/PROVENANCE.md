# 交付面探针证据（第八十六轮，`bench/probe_delivery.py`）

## 这是什么

量**通道**，不量能力：同一对消息（`run.py:1067` 的 `[system=DIRECT_SYSTEM, user=task.prompt]`，
逐字节复现，`DIRECT_SYSTEM` 由 AST 从 `run.py` 取出而非手抄）、同一 `temperature=0.0`、
**只换一个车身字段**，看「答案能不能被送到打分器」。

## 文件

| 文件 | 内容 |
|---|---|
| `raw.jsonl` | 15 条真请求记录（5 题 × 3 臂），含 `body_sha` / `finish_reason` / **完整 `usage` 原样** / 重试次数 / 耗时 / cells |
| `control.jsonl` | 1 条极小控制实验（`max_tokens=8`）：证明思考会吃满输出预算 |
| `summary.json` | 上面两者的机械汇总（按臂的交付数/截断数/重试数/token 序列） |
| `run.log` | 探针 stdout 全文（逐格读数 + 按臂汇总 + 假设裁决） |
| `report.log` | 逐格 TSV（探针自己写的那份，便于 diff） |

## 来源与可复现命令

```bash
ZKEY=$(python3 -c "import json;print(json.load(open('/Users/linshaojie/V5/config/vault.json'))['ZHIPU_API_KEY'])")
V1_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4 V1_LLM_API_KEY="$ZKEY" \
V1_LLM_MODEL=glm-4.7-flash python3 -u bench/probe_delivery.py --run \
  --tasks sg05,sg06,sg07,sg10,sg02 --arms base,off,base8k \
  --out bench/evidence/deliver_s86 --timeout 500 --retries 6 --backoff 8

# 控制实验（20 秒）
V1_LLM_BASE_URL=... V1_LLM_API_KEY=... V1_LLM_MODEL=glm-4.7-flash \
  python3 -u bench/probe_delivery.py --control --out bench/evidence/deliver_s86 \
  --timeout 300 --retries 3 --backoff 5
```

## 臂定义（预注册，单变量）

| 臂 | `thinking` | `max_tokens` | 与 `base` 差什么 |
|---|---|---|---|
| `base` | 不发该字段（= §49 实际口径） | 4096 | — |
| `off` | `{"type":"disabled"}` | 4096 | 只差 `thinking` |
| `base8k` | 不发该字段 | 8192 | 只差 `max_tokens` |

⚠️ `--self-check` 的 (a) 组把这条钉住了：`base→off` 的差异键集合必须**恰好**是
`{"thinking"}`、`base→base8k` 必须**恰好**是 `{"max_tokens"}` —— 否则"单变量"这句话就是假话。

## 口径对齐（为什么这些 cells 可以采信）

`--self-check` 的 (h) 组：用本探针的 `score_of` 重算 `bench/evidence/zhipu_s85/` 里
**已冻结**的答案，6 道 recorded 题必须**逐题逐格**与台账相同（实测 6/6 相同、0 题被丢）。
⇒ 本探针没有另造一把尺子，它用的就是 `bench/scorers/code_exec.py` 的那条。

⛔ **仍然不许**把这里的 cells 与 qwen / 内核臂的分数并列：模型不同、`sig` 不同、
且本轮每格只有 **1 次**采样。cells 在这里只有一个用途 —— 区分"交了一个能打分的答案"与"没交"。

## 边界（明写）

1. **消息对是直连臂的**，不是内核臂的。内核臂的 prompt 长得多（带工具 schema 与 JSON 协议），
   所以本轮的截断数**不能**直接外推到内核路径；能外推的是**机制**（思考占输出预算）。
2. **每格 1 次采样**（`temperature=0.0`）。同 prompt 的确定性在 §81 已实证，但
   通道层的 429 与超时**不是确定性的** ⇒ 交付率有采样噪声，5 题的分辨率只能看"整条臂塌不塌"。
3. **控制实验只证明预算归属**，不证明任何与答题能力有关的事。
4. 云端服务端行为可能变化；`control.jsonl` 的形态可随时用同一命令复核。

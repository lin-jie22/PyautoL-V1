# ★7o PROVENANCE —— 知识 vs 握手（第四十四轮，2026-10-20）

## 目的
7n 实锤交付路径双态后，把「惯例出现在 propose 响应」（K）与「最终交付」（D）
拆开量，HS=直答握手。判据在 DESIGN ★7o（跑之前写死）。

## 装置（全部复用，零内核改动）
- 捕获代理：`/tmp/llm_proxy.py`（请求+响应逐字节落盘，受控后台任务）
- 真实 loop：`run.py --bench --solver llm --temperature 0 --max-tokens 1024
  --budget 1 --no-external --suite bench/heldout/g7j_{n0,n4}_heldout.jsonl
  --memory-dir <fresh memH copy> --state-dump ...`
- env：`V1_LLM_BASE_URL=http://127.0.0.1:8399/v1 V1_LLM_API_KEY=ollama
  V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0 V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0
  NO_PROXY=127.0.0.1`
- 种子：`build_g7d_seed.py --variant 7j --heldout g7j_{n0,n4}` 现建
  （零模型，R1 预检双过；`build_n0.log`/`build_n4.log`）
- 跑次：n0/n4 各 2 repeat（7n 实测 T=0 跨时刻可翻，2 次做复现性观察）

## 量具（bench/probe_7o.py，分析零模型调用）
- K = propose 响应（sys 含'规划器'且 user 含 [语义记忆]）里全部字符串
  递归收集后逐片喂 `code_exec`（权威判据）的最高分
- HS = 1 ⟺ 无补救请求（user 只含 goal、无 [语义记忆]）且 propose 响应
  含非空 ANSWER 段（分隔符用内核 `_MARK_RE`，口径零漂移）
- D = final db 交付（goal 后第一个 finish，可隔 outcome 行）喂 code_exec

## ⛔ 量具病（如实登记，两处都在分析侧、不在内核）
1. **捕获文件名错位一格**：跑臂循环里 `mv` 在 run 之前执行 ⇒ cap 文件
   全部后移一格。按 ZK 钥匙计数逐文件验证后重命名修正
   （n0=28、n4=136/144，与请求结构自洽）。
2. **ANSWER 分隔符两版**：模型把分隔符写成 `---\n ANSWER---`（换行+空格），
   内核 `_MARK_RE` 容忍、探针首版按字面 `---ANSWER---` 切分 ⇒ g7d3 被
   误判 HS=0（但 D=0.4 且无补救，矛盾暴露）。改用内核正则后 16 格自洽。

## 读数（khd_triplets.txt；两 rep 逐位相同）
| 格 | K | HS | D |
|---|---|---|---|
| n0 g7d2 | 1.0 | 1 | 1.0 |
| n0 g7d3 | 0.4 | 1 | 0.4 |
| n0 g7e1 | 0.0 | 0 | 0.2 |
| n0 g7e2 | 0.0 | 0 | 0.2 |
| n4 ×4 任务 | 0.0 | 0 | 0.2 |

**16/16 格零例外**：K>0 ⇔ HS=1；HS=1 ⇒ D=K；HS=0 ⇒ D=0.2（基线）。

## 文件
- `cap_{n0,n4}_r{1,2}.jsonl` —— 四次 loop run 的请求/响应逐字节捕获
- `run_{n0,n4}_r{1,2}.log` —— 对应 run.py 输出
- `build_{n0,n4}.log` —— 种子构建 + R1 预检
- `khd_triplets.txt` —— K/HS/D 三元组（probe_7o.py 直出）
- `FILES.sha256` —— 指纹

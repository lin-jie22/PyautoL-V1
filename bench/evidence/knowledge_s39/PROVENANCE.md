# §17-7k 证据自述（第三十九轮，2026-10-19 23:05 – 10-20 00:15）

**一句话**：7j 可控 suite（钥匙命中 1 条 / 4 条）在真模型上的第一次三臂读数 ——
n0 档 Δ_H=+0.2500、Δ_P=0.0000 ⇒ R3a 容量主张成立；n4 档 Δ_H=+0.0000 ⇒ R3c 不认领。

## 事前登记

`DESIGN.md` **★7k 事前登记**（跑臂之前写死）：2 suites × 3 臂 × 3 repeats = 18 cells；
判读链 R1 → R4 → R7 → R3（判据一字不动，沿用 `probe_g7d.py`）；
作废规则：transport 路径逐格比对 + 种子 sha + R1 任一不过整组作废。

## 复现命令

```bash
# ① 种子（R1 预检内建；两份 facts 内容级逐字节相同，见下）
python3 bench/build_g7d_seed.py --out-dir /tmp/v1_7k/seed_n0 \
    --heldout bench/heldout/g7j_n0_heldout.jsonl --variant 7j
python3 bench/build_g7d_seed.py --out-dir /tmp/v1_7k/seed_n4 \
    --heldout bench/heldout/g7j_n4_heldout.jsonl --variant 7j

# ② 跑臂（真模型 qwen2.5:7b / T=0 / 写通道双关 / 总耗时 1h06m34s）
V1_SUITE=bench/heldout/g7j_n0_heldout.jsonl bash bench/run_g7d.sh \
    /tmp/v1_7k/seed_n0 bench/evidence/knowledge_s39/ev_n0
V1_SUITE=bench/heldout/g7j_n4_heldout.jsonl bash bench/run_g7d.sh \
    /tmp/v1_7k/seed_n4 bench/evidence/knowledge_s39/ev_n4

# ③ 读数（判据/阈值零改动；--facts 7j 是本轮唯一新增，纯防呆 choices）
python3 bench/probe_g7d.py --evidence bench/evidence/knowledge_s39/ev_n0 \
    --heldout bench/heldout/g7j_n0_heldout.jsonl --facts 7j
python3 bench/probe_g7d.py --evidence bench/evidence/knowledge_s39/ev_n4 \
    --heldout bench/heldout/g7j_n4_heldout.jsonl --facts 7j
```

## 文件清单

| 文件 | 内容 |
|---|---|
| `ev_n0/` `ev_n4/` | 原始跑臂产物各 9 格：`{A}_r{R}_runs.jsonl`、`states_{A}_r{R}.jsonl`、`seed_{A}.db`、`final_{A}_r{R}.db`、`{A}_out_r{R}.log`、`suite.jsonl`、`sha256.txt` |
| `probe_g7d_n0.txt` | n0 读数（R1/R5/R4/R7/R2/R3 全链）：Δ_H=+0.2500 ⇒ **R3a** |
| `probe_g7d_n4.txt` | n4 读数：Δ_H=+0.0000 ⇒ **R3c** |
| `audit_7k.txt` | state 三态审计 + transport 逐格签名汇总（n0 54/54、n4 63/63、0 格作废） |

## 有效性三件套（全过）

1. **R1 送达**：两 suite 各 13 项 ✅；facts 跑后 = 种子（运行期写入 0）。
2. **transport 逐格签名**：18 格全部 `(0, 0, 0, 0)`（refused_first, refused_retry, retries, exhausted）⇒ 无一格作废。
3. **state 三态审计**（`bench/state_audit.py`，判据出自内核之外）：H 臂每步 own 在 `[语义记忆]`、P/E 臂零泄漏、零 own_elsewhere。

## 如实登记

1. **效应只许按组合效应措辞**：条数与长度不可分离（`f.text[:120]`），"条数效应"禁用。
2. n4 抹掉（不是稀释）n0 效应的机制**未测量**，候选（注意力分散/上下文挤占/钥匙干扰）都只是候选。
3. 自踩 ①：Bash heredoc 偶发静默不执行（exit 0 零输出）—— 与 BSD grep `\|` 静默返 0 同宗，重试改 `-c` 单行。
4. 自踩 ②：state 审计首跑 15 条 bad 是映射喂错（`facts.id` 整型主键 ≠ 任务 id，须从 `VARIANTS['7j']` 取）。
5. 逐题墙钟：n0 mean 46.8s/题（total 1686s）、n4 mean 62.2s/题（total 2238s）—— n4 更慢，与 state 更长一致。
6. 种子两份文件 sha 不同（SQLite 页序）但 facts **内容级**逐字节相同（`SELECT id||':'||text ORDER BY id` 三臂 shasum 一致，E 臂空表）。

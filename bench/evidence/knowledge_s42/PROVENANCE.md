# §17-7m-β 证据自述（第四十一轮，2026-10-20 11:00 – 11:10）

**一句话**：7m-β 拆 A/E 判读 —— **E 实锤**。语义块 = n0 形状（1 条真惯例
43 字、R1 own ✅、注入保真 100%）+ 题面 = n4 形状（4 钥匙，逐字节复用
7j_n4 题面）⇒ 9 格全 0.2、H′−P′ = +0.00 ≤ +0.20 闸 ⇒ **光题面
"按（ZK1）（ZK2）（ZK3）（ZK4）惯例处理"的多指向本身，就足以把
7k n0 的 +0.80 抹除到 0**。候选 A（语义块 4 条形状/邻居在场）被排除出
抹除者的必要条件地位——4 条不是必须的，1 条 + 4 钥匙题面就归零。

## 复现命令

```bash
# ① 单题 suite（g7j_n4_heldout 过滤 g7d2 一行，逐字节不动；sha 见 suite_sha256.txt）
# ② 种子（--variant 7j：main() 只种 suite 内题的惯例 ⇒ 种子天然只有 1 条）
python3 bench/build_g7d_seed.py --out-dir /tmp/v1_7m/seed \
    --heldout bench/heldout/g7m_g7d2_heldout.jsonl --variant 7j
#    ⇒ build_r1_precheck.log：H=1条（本条✅）P=1条 E=0条 注入字符 H=43 P=43
# ③ 跑臂 9 格（真模型 qwen2.5:7b / T=0 / 写通道双关 / 5m56s）
V1_SUITE=bench/heldout/g7m_g7d2_heldout.jsonl bash bench/run_g7d.sh \
    /tmp/v1_7m/seed bench/evidence/knowledge_s42
# ④ 读数（判据零改动；--facts 7j 的 g7d2 条 = 种入条，逐字节同）
python3 bench/probe_g7d.py --evidence bench/evidence/knowledge_s42 \
    --heldout bench/heldout/g7m_g7d2_heldout.jsonl --facts 7j
```

## 文件清单

| 文件 | 内容 |
|---|---|
| `{H,P,E}_r{1..3}_runs.jsonl` / `states_*` / `seed_*` / `final_*` / `*_out_*` | 9 格原始产物 |
| `suite.jsonl` / `sha256.txt` | 跑臂用的 suite 副本 + 种子冻结指纹 |
| `build_r1_precheck.log` | 种子构建 + 内建 R1 预检（H=1条 本条✅、H=P=43 等长、E 空） |
| `probe_g7d_7m.txt` | 读数全链：R1/R5/R4/R-noise/R2/R7/R3 |
| `audit_7m.txt` | state 三态审计 9/9 + transport 逐格签名 0 不一致 |
| `suite_sha256.txt` | n4 原套件与单题套件的 sha（单题 = 原套件子集逐字节） |
| `run_arms.log` | 跑臂逐格 rc 记录 |

## 读数（全部可 grep probe_g7d_7m.txt）

- `g7d2_dedupe_keep  H=[0.2, 0.2, 0.2]  P=[0.2, 0.2, 0.2]  E=[0.2, 0.2, 0.2]`
- R-noise max_range=0.0000；Δ_H=+0.0000、Δ_P=+0.0000。
- **E′ 同构性哨兵 ✅**：E 单题 = 0.20 = 7k 套件内 E-g7d2 读数。
- transport 9 格 (0,0,0,0)；state ok=9 bad=0（facts_map 喂全 4 条，多 3 条出现即泄漏）。

## 判读（按 DESIGN ★7m-β 执行登记写死的闸，未移动）

| 格 | 读数 | 闸 |
|---|---|---|
| H′−P′ | **+0.00** | ≤ +0.20 ⇒ **E 实锤** ✅ |
| E′ 哨兵 | 0.20（= 7k 套件内） | 相等 ⇒ 单题套件同构 ✅ |
| R-noise | 0.0000 | < 0.05 ✅ |

## 与 7k 的对照（⛔ 只做方向性，不做数值合并）

| 格 | 语义块 | 题面 | g7d2 H |
|---|---|---|---|
| 7k n0 | 1 条 43 字 | 1 钥匙 120 字 | **1.0**（H−P=+0.80） |
| 7m-β H′ | 1 条 43 字（逐字节同） | **4 钥匙 135 字** | **0.2**（H′−P′=+0.00） |
| 7k n4 | 4 条 202 字 | 4 钥匙 135 字 | 0.2 |

7m-β H′ 与 7k n0 唯一差异 = 题面多 3 把钥匙（+15 字）⇒ 抹除归因于
**题面多指向（E）**。机制面（模型为什么被多指向抹掉：缺失感/注意力/
指令歧义）未测，是下一轮的题。

## 如实登记

1. 本轮 suite 是新 suite（单题子集），读数只与本轮 P′/E′ 比；上表 7k
   两行只作方向性参照。
2. n4 题面与 n0 题面除钥匙数外逐字相同（7j rewrite 构造保证）——
   "钥匙数"与"题面长度"（+15 字）仍耦合，但 15 字等长中性扰动在
   7g 实测 ≤0.40 下限之下？⛔ 不，7g 的等长中性扰动实测 ≥0.40——
   严格说 E 实锤包含"+15 字扰动"的可能贡献。缓解：R7 同轮实测
   |Δ_P|=0.0000（P′ 臂同题面同长度的扰动下无效应），但 P′ 无惯例
   可用，测不出"多指向对**有惯例者**"的扰动。⇒ E 实锤的精确措辞：
   "题面从 1 钥匙换 4 钥匙（+15 字）抹除了效应"——钥匙数与 15 字
   在本题面构造里不可分离，与 7k 的"组合效应"措辞同款纪律。

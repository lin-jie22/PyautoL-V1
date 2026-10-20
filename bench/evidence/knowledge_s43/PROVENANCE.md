# §17-7m-γ 证据自述（第四十一轮再续，2026-10-20 11:15 – 11:30）

**一句话**：7m-γ 两段判读 ——
**(ii) 交付文本取证（零模型）**：所有 0.2 格（7m-β 9 格 + 7k n4 H 3 格）
的 finish 交付**逐字节 = E 臂基线写法**（`seen_add` 版，无 strip/lower）；
1.0 格（7k n0）才是惯例逐字采用（`strip().lower()`）。抹除形态 = **读而不取**：
惯例在上下文里完整在场，行为上当作不在——不是被邻居带偏、不是部分采用。
**(i) n0+15 字中性填充跑臂（9 cells，6m35s）**：H″−P″ = **+0.00** ≤ +0.20 闸
⇒ **"+15 字中性追加"本身足以抹除** ⇒ E（钥匙多指向）不是抹除的必要条件。
**总判读：1.0 只在逐字节的 n0 原题面下出现**；n4 钥匙、中性填充——
题面的任何非语义变化都把行为打回基线。

## 复现命令

```bash
# (ii) 零模型：读 final_*.db episodes（脚本见 delivery_forensics.txt 口径）
# (i)  跑臂：
python3 bench/build_g7d_seed.py --out-dir /tmp/v1_7mg/seed \
    --heldout bench/heldout/g7m_g7d2_pad15_heldout.jsonl --variant 7j
V1_SUITE=bench/heldout/g7m_g7d2_pad15_heldout.jsonl bash bench/run_g7d.sh \
    /tmp/v1_7mg/seed bench/evidence/knowledge_s43
python3 bench/probe_g7d.py --evidence bench/evidence/knowledge_s43 \
    --heldout bench/heldout/g7m_g7d2_pad15_heldout.jsonl --facts 7j
```

## 文件清单

| 文件 | 内容 |
|---|---|
| `delivery_forensics.txt` | (ii) 交付文本取证：9+3 格 finish sha 与 strip/lower 检测 + 代表性全文 |
| `{H,P,E}_r{1..3}_*` | (i) 9 格原始产物 |
| `build_r1_precheck.log` | 种子构建 R1 预检（H=1条 本条✅、H=P=43） |
| `probe_g7d_7mg.txt` | 读数全链（H″=[0.2×3] P″=[0.2×3] E″=[0.2×3]） |
| `audit_7mg.txt` | state 审计 12/12 零泄漏 + transport 0 不一致 |
| `suite_sha256.txt` | pad15 单题套件 sha |

## 判读（按 ★7m-γ 写死的闸，未移动）

| 检验 | 读数 | 结论 |
|---|---|---|
| (i) H″−P″ | **+0.00** | ≤ +0.20 ⇒ +15 字中性追加足以抹除 |
| (i) E″ 哨兵 | 0.20 | 单题同构 ✅ |
| (ii) 0.2 格交付 | = E 臂基线逐字节 | 读而不取 |
| (ii) 1.0 格交付 | strip().lower() 惯例采用 | 唯一例外 = 逐字节 n0 题面 |

## 对 §17-7 整体图景的修正（如实登记）

1. **7m-β 的"E 实锤"收窄**：E（4 钥匙多指向）是抹除的充分条件之一，
   ⛔ 不是必要的——中性 15 字追加同样抹除（本轮 (i) 实证）。
2. **R3a（7k n0 的容量主张）边界显形**：判据当时登记、读数真实、不撤回；
   但 7m-γ 证明效应的**存在域 = 逐字节 n0 原题面**。"记忆通道能携带
   题面缺失的判别条件"这个能力对题面扰动极端脆弱（一次中性追加归零）
   ⇒ 它更像 **prompt 形状匹配现象**，不是功能性取用。
3. **与 7e/7f/7g 收敛**：7e 的 +0.20 被邻域复制撤回、7f 双稳、7g 曲线平、
   7g 等长扰动 ≥0.40、7m-γ 中性追加抹除 ⇒ "惯例采用"是 prompt 形状
   敏感的、脆弱的。g7d2 的 +0.80 也只在逐字节配置下出现。
4. **两个并存解释（行为侧已到分辨力边界）**：扰动敏感 vs 基线吸引子
   （任务对 T=0 模型太熟，任何变化都掉回强基线写法）——不互斥，
   行为侧无法再拆；机制侧（注意力/梯度流）不在本装置分辨范围内。

## 如实登记

1. (i) 的新题面把 PAD15 插在"只输出代码"之前（指令结尾原位）——
   若插在尾部会引入"指令位置"第二变量，已规避。
2. 自踩：`audit_state` 首跑 12 行是单题 2 步格的正常展开，非泄漏。

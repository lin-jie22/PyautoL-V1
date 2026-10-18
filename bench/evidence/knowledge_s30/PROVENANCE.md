# §17-7b 阳性对照（同族训练）—— 证据档案

日期：2026-10-19　｜　模型：本地 ollama `qwen2.5:7b`（T=0）
判读：**R3c —— Δ_H = Δ_P = +0.0000，不认领；§17-7 仍记 0**
（★7b **不构成** 7a 翻案：7a 问跨族，7b 问同族，两个独立命题）

## 一、本轮要回答什么

7a 的 Δ=0 有两种互斥解释：**(i) 族失配**（可迁移内容=0）、**(ii) 通道本身不工作**。
7a 的读数在这两种解释下**长得一样** ⇒ 它没有分辨力。★7b 是**阳性对照**：
只换训练题（换成与考试同族），考试一个字节不动，看 Δ 是否离开 0。

## 二、装置（事前登记见 DESIGN §17-7 ★7b）

| 项 | 值 |
|---|---|
| 考试题 | `bench/heldout/g7_heldout.jsonl`（g7s2 0.4 / g7s4 0.6，E=0.5）**与 7a 同一份** |
| 训练题 | `bench/tasks/g7_family_train.jsonl` 4 题（同族不同函数，断言零重合） |
| 三臂 | H = 蒸馏 facts 纯库（episodes=0）/ P = 逐条等长安慰剂 / E = 空表 |
| 判定参数 | T=0 / max_tokens 1024 / budget 1x / `--no-external` / `V1_ALLOW_NOTE=0` / `V1_ALLOW_LEARN=0` |
| 重复 | 每臂 3 次，每次各自的种子拷贝（sha256 跑前核对） |
| 训练相 | 2x / 2048 / `--use-reward`（自然失败史唯一来源） |

## 三、训练史（如实，含早退）

- 1x/1024 校准：`t1 0.40 / t2 0.40 / t3 1.00 / t4 0.00`（`calib_runs.jsonl`）
- 2x/2048 训练：`t1 0.60 / t2 0.60 / t3 1.00 / t4 早退`（`train_memory.db`）
- 失败史：`reward=3`，其中**「未通过」2 条**（t1 括号内逗号、t2 单引号内 `;`）
- ⚠️ t4 `skipped_no_answer=1`（模型输出不是合法 JSON）⇒ **不产生失败史，也不等于答对**
- ⚠️ 装置修正⑦：训练会话 `rc=1` 的原因是上面这条早退（`run.py:1157`
  `return 0 if rep.trustworthy() else 1`），**不是**零失败史。原 builder 把 rc 当
  "零失败史"判据 = **判据错位**，已改为直接数失败史条数并在 rc≠0 时打印早退条目。
- 蒸馏：输入 18 → 采纳 10 → H 臂 **9 条**（16–31 字），P 臂 9 条等长，E 臂 0 条

## 四、判读（`python3 bench/probe_g7.py --evidence bench/evidence/knowledge_s30`）

```
R1   九组全过（H/P 种子 9 facts、E 0；跑后逐库一致；两 goal 重放 H=6/P=6/E=0）
R5   facts 9 条全 16–31 字，>120 字 0 条，注入保留 100%（排除 120 字截断）
R4   g7s2 H=[0.4,0.4,0.4] P=[0.4,0.4,0.4] E=[0.4,0.4,0.4]
     g7s4 H=[0.6,0.6,0.6] P=[0.6,0.6,0.6] E=[0.6,0.6,0.6]
R-noise  0.0000
R2   mean H=P=E=0.5000  ⇒  Δ_H=Δ_P=+0.0000
R3c  ⇒ 不认领
```

## 五、机制链（三段，全部可复算）

| 诊断 | 装置 | 读数 |
|---|---|---|
| **D0** 注入存活 | `bench/probe_inject.py`（零模型调用，直调真实 `Core._state()`） | 48/48 格保留 `[语义记忆]`（state ≤ 1312 < cap 1600）⇒ **不是被 cap 挤掉** |
| D0b 送达内容 | 同上 + `recall()` 重放 | 只送 9 条中的 **6 条**，两题送达集合相同；**最贴题的「嵌套引号先特殊处理」没进前 6** |
| **D1** 灵敏度上界 | 9 条**全拼进题面**（绕过通道），空库单次 | `g7s2` **0.40→0.00**（0/5，基础用例也挂）、`g7s4` 0.60→0.60 |

- D1 的日志显示模型**确实采纳**了建议：它写出了 `in_quotes` / `quote_char` 状态机
  —— 但把**无引号的简单情形一起改坏了**。
- 归因：不是"没送到"，是"**送到了、也照做了、但用错了**"。
  ⇒ 「Δ 的天花板」新登记**第四个决定因素**：模型把建议落成实现的能力。

## 六、文件清单

| 文件 | 内容 |
|---|---|
| `seed_{H,P,E}.db` | 三臂冻结种子（跑前快照） |
| `final_{A}_r{R}.db` | 每臂每 repeat 跑后库（完整性核对用） |
| `{A}_r{R}_runs.jsonl` | 9 次会话的逐题读数 |
| `{A}_out_r{R}.log` | 9 次会话的完整日志 |
| `train_memory.db` | 训练相记忆库（蒸馏原料，sha256 `3da56742…`） |
| `calib_runs.jsonl` | 训练题 1x 校准读数（不进判定） |
| `d1_suite.jsonl` / `d1_runs.jsonl` / `d1_out.log` | D1 灵敏度上界诊断（改了题面，**不进判据**） |
| `sha256.txt` | 全部种子与新增证据的 sha256 |

## 七、边界（不许越界引用）

1. **不是 7a 翻案**：7a 仍记 0，它问的是跨族迁移；本轮问同族。两者不许合并。
2. D1 **改了题面**，它的读数**不能**拿来说"知识有用/无用"，只能回答"下一步修哪一层"。
3. 单模型（qwen2.5:7b）、2 道考试题、单次 D1 ⇒ 结论只在这个范围内成立。
4. ⛔ 在换模型之前**不许**写成"知识通道无效" —— 那是把模型的局限冒充成机制的性质。

## 八、重跑

```bash
# 1) 训练相 + 三臂种子（约 10 分钟）
PYTHONPATH=bench python3 bench/build_g7_seed.py --out-dir /tmp/s30 \
  --train-suite bench/tasks/g7_family_train.jsonl \
  --heldout bench/heldout/g7_heldout.jsonl
# 2) 三臂 × 3 repeats（约 21 分钟）
bash bench/run_g7.sh /tmp/s30 bench/evidence/knowledge_s30
# 3) 判读 + 注入诊断（零模型调用）
python3 bench/probe_g7.py --evidence bench/evidence/knowledge_s30
python3 bench/probe_inject.py --seed bench/evidence/knowledge_s30/seed_H.db \
  --goal-file bench/heldout/g7_heldout.jsonl
```

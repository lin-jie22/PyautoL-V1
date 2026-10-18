# knowledge_s28 —— §17-6 6c 三臂实验（第二十八轮，2026-10-18，第三跑为有效组）

事前登记：DESIGN §17-6 ★6c（R0–R4 跑臂之前写死，含三次装置修正的登记）。
判读探针：`bench/probe_knowledge_exec.py`（零模型调用，只读本目录）。
种子构建：`bench/build_6c_seed.py`（H=桩失败史+真蒸馏 6 条；P=逐条等长异域；E=空）。

## 三跑史（前两跑按登记规则当场作废）
1. 第一跑：完整性核对（跑后库）发现 note 运行期写入 ⇒ E 臂自我增殖 ⇒ 作废
   ⇒ 装置修正②：`allow_note` 开关（AW1–AW5）。
2. 第二跑：facts 仍长（P 6→8、E 0→2），写入者 `worldmodel.learn()` ⇒ 作废
   ⇒ 装置修正③：`allow_learn` 开关（AX1–AX4）。
3. 第三跑（本目录）：R1 全绿，判读成立。

## 口径
- heldout 7 题（k01–k04 + h01–h03）、T=0、max_tokens 1024、budget 1x、--no-external、
  `V1_ALLOW_NOTE=0` + `V1_ALLOW_LEARN=0`、每臂独立 --memory-dir、每臂 1 次。
- 每臂两次 --suite 调用（k 先 h 后，共享同一记忆目录），三臂对称。
- H 臂种子 sha256 = 4aa63504…（P: a121a1b7…、E: 9694ea99…），跑前冻结、跑后核对一致。

## 判读（探针输出，R3c）
mean H=P=0.7714、E=0.6571；Δ_H = Δ_P = +0.1143 < 0.15 ⇒ 不认领，§17-6 保持 0。
H 与 P 逐题完全相同 ⇒ 唯一翻转（E 的 k01=0.0）是**存在性效应**，内容无关。

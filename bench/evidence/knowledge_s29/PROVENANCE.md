# knowledge_s29 —— §17-7 三臂 × 3 repeats 实验（第二十九轮，2026-10-19）

事前登记：DESIGN §17-7 ★7a（跑臂之前写死，含装置修正①～⑥）。
判读探针：`bench/probe_g7.py`（零模型调用，只读本目录）。
种子构建：`bench/build_g7_seed.py`（训练=真模型自然失败史 + `--use-reward`
外部奖励通道 → distill 3 条；P=逐条等长安慰剂；E=空表）。
题池：`bench/tasks/g7_struct.jsonl` + `bench/tasks/g7_numeric.jsonl`
（8 道新题，gen_tasks 自检过：oracle 满分 / partial 严格居中 / null 零分）。

## 校准 → 选题（校准读数不进判定）
- 校准（空库 T=0 单次跑题池）：g7s2=0.4、g7s4=0.6 → **heldout**；
  其余 6 题 1.0 → 剔除；E=0.0 训练候选为空 → 装置修正④
  （训练改用既有 struct_convert z01–z05，与 heldout 不同函数、同领域）。
- heldout = `bench/heldout/g7_heldout.jsonl`（g7s2_parse_headers、
  g7s4_xml_escape_attrs）。

## 训练史（三改其装置，均有登记）
1. 修正⑤：budget 1x 下会话全走收尾通道、verify 一次不跑 ⇒ 0 条 failure
   episode（z02 真实答错 0/5 但错误没走到闸口）→ 训练改 2x/2048。
2. 修正⑥：2x 下仍 0 条 failure —— **内核 verify 只查四项浅判据
   （非空/函数名/语言标签/可编译），语义性错误零痕迹** → 训练加
   `--use-reward`（§17-5 只读通道），外部得分 + 没过的 assert 原文以
   kind=reward episode 落库（5 条，其中 2 条未通过）。
3. 蒸馏（T=0 真模型）：输入 17、采纳 3、点名拒 1、空 13。
   H 臂 = 纯 facts 库（episodes=0，隔离掉原始 episode）。

## 口径
- 三臂 × 3 repeats；T=0、max_tokens 1024、budget 1x、--no-external、
  `V1_ALLOW_NOTE=0` + `V1_ALLOW_LEARN=0`、每（臂，repeat）独立种子拷贝
  与 --memory-dir、每次起跑前 sha256 比对一致。
- 种子 sha256：H=e92936fe…、P=46a46ae9…、E=9694ea99…（见 sha256.txt）。

## 判读（探针输出）
- R1 九组全过（facts 种子==跑后；recall 重放 H=3/P=3/E=0）。
- R-noise：max_range=0.0000（T=0 三连跑逐位可复现）。
- R4：三臂逐位相同 —— g7s2 三臂均 0.4、g7s4 三臂均 0.6。
- R2：mean H=P=E=0.5000 ⇒ Δ_H=Δ_P=+0.0000 ⇒ **R3c：不认领，§17-7 记 0**。
- 连存在性效应都没有（6c 的 k01 翻转未复现）。
- 机制归因：训练失败族（NameError / percent-decode）与 heldout 失败族
  （引号感知切分 / 单次转义表）零交集 ⇒ 可迁移内容=0。
  「Δ 天花板由族间可迁移度决定」第三次同型。

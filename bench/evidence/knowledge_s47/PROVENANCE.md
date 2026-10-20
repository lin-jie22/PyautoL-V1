# ★7q PROVENANCE —— 改内核轮（第四十五轮，2026-10-20）

## 目的
7o 定律（K>0⇔HS=1；HS=1⇒D=K；HS=0⇒D=0.2）的内核侧根因 = 记忆盲补救
（answer() prompt 只有 goal）。本轮修三处，见 DESIGN §18（跑之前写死）。

## 修复（内核改动，契约 525→530）
1. `Planner.answer(goal, state="")`：propose 定向补救传 state；`core._close_out`
   现算 `_state()` 传入（防 ledger None）。AZ36/AZ37。
2. `catalog_sha(..., allow_note=True)` 转发；run.py 口径签名传
   `s.limits.allow_note`。AZ38/AZ39（COUPLED: AZ39 撤回连带 AZ38+AT1 ——
   同一 forwarding 行的三个观测点，实测认定合理连带）。
3. `--max-tokens` 默认 None：显式值在 settings() 前写入 env（唯一配置入口；
   Limits frozen，首版直接赋值实测 FrozenInstanceError）；caliber/llm-direct
   读生效值。AZ40。

## 真模型冒烟（机制级判定；⛔ 行为 Δ 不认领，n=1 单臂无安慰剂）
- 装置与 7o 完全同款：/tmp/llm_proxy.py 8399 捕获；n4 heldout；
  memH 独立拷贝 sha256=24b7c57d5ac3…；env 同 s46（V1_ALLOW_NOTE=0 等）。
- **判定：补救/收尾请求 5/5 全部含 [语义记忆]**（7o 基线同类请求 8/8 全无
  —— 修复前后直接对撞）。smoke_verdict.txt。
- ★口径签名实测 `tool_catalog_sha=c89aa901bfb1` = AZ38 的 allow_note=False
  指纹 ⇒ 口径签名如实反映活注册表（修复前恒 8decb60895d4 = True 形状）。
- 观察性读数（不认领）：n4 mean=0.5500（7o 基线 0.2）；transport 干净
  （抖动=0 放弃=0）。

## R13
- 首跑 103/105：AZ37 红=无（夹具病：goal 含 az37conv，断言锚在 goal 上
  ⇒ 改锚 fact 独有正文）；AZ39 多红 AT1（真实连带，进 COUPLED）。
- 修复后 AZ36–AZ40 选择性全绿；契约 530/0。全量 105 条复跑结果见
  r13_full.log。

## 文件
- run_smoke.log / cap_n4_smoke.jsonl / state_dump.jsonl / build_n4.log
- smoke_verdict.txt（analyze_smoke.py 直出）
- FILES.sha256 —— 指纹

## R13 收口（补记，18:2x 完成闸门）
- 全量复跑①（r13_full.log）：102/105。三条失败与修复：
  1. **AT1 锚失效**：7q 给 catalog_sha 加转发后，旧锚第二行
     `workspace=workspace)` 不再匹配 ⇒ 更新锚为
     `workspace=workspace, allow_note=allow_note)`（变异语义不变：memory=None）。
  2. **AW1/AW2 多红 AZ38**（真实连带）：撤"note 按 allow_note 注入"⇒ note 恒不注册
     ⇒ catalog_sha on/off 指纹必然相等 ⇒ AZ38 崩。COUPLED 并入原条目：
     AW1={I6,I7,AZ38}、AW2={AW5,AZ38}。
  3. **AT1 连带 AZ38+AZ39**：catalog_sha 注册表 memory=None ⇒ recall/note 从默认
     指纹消失 ⇒ AZ38（on/off 相等）+ AZ39（默认指纹与手工 _StubMemory 注册表脱节）。
     新增 COUPLED：AT1={AZ38,AZ39}。反向核对：AZ38 撤回态单红、AZ39 已登记
     {AZ38,AT1} —— 单向成立。
- ⛔ 自踩：往 COUPLED dict 追加新键与原键重复（"AW1" 原有 {I6,I7}），dict 字面量
  后者覆盖前者 ⇒ 首次选择性复跑仍报多红。教训：**加键前先 grep 键名，存在则合并**。
- **最终闸门（r13_full2.log）：105/105 全绿、还原后基线 530/0、无 Traceback**。
- README 计数同步 525→530、撤回态 100→105。

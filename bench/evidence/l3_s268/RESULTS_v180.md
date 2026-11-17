# V180 结果 —— 让 COARSE 也给轴：**哪一层**的 COARSE 算数

**预注册**：`PREREG_v180.md`（v1，落盘时间见文件 mtime，早于 STAGE0 首跑）
＋ `PREREG_v180_v2.md`（追加 P8）＋ `PREREG_v180_v3.md`（追加 P9）。
**装置**：`probe_v180_stage0.py`（STAGE0，先于任何产品代码改动）、
`v180.py`（判据）、`_axes_worker.py`（两臂工作进程，自报导入的 `goals.py` sha）。
**代码改动**：`mw/goals.py`（`repair_axes` + `escalations` overlay + `render_escalations`），
`tests/contract_test.py`（AZ141a~f），`bench/withdraw_check.py`（2 条撤回态）。

---

## §1 STAGE0：先量再动（6 PASS / 0 FAIL）

| # | 命题 | 读数 |
|---|---|---|
| P1 | 池可重建 | 6 格 / 32 行，与 V179 那批**独立重打**逐行相同 ✔ |
| P2 | `repair_axes` 产品调用点 | **恰 1 处**（`mw/goals.py:666`） |
| P3 | 两条规则逐格对照 | 见 §2 表 |
| P4 | `R_score` 假正例 ≥4 | **5/6** |
| P5 | `R_beh` 集合 == 直接数出的集合 | 都是 `['m02_vfs']`；`R_score` 真包含它（6 ⊋ 1）✔ |
| P6 | 部署上触发 0 格 | 账本 36 行 / 24 格 / **带 vec 0 行** ⇒ 0 格 ✔ |
| P7 | overlay 的 claim 值域 | 强制进 esc 后全为 `None`（**永远不是** `BEHAVIOR_UNRESOLVED`） |

### 死分支取证（P2 + P7 合起来才是完整证据）

`repair_axes` 的 `claim in (SCORE_UNMOVABLE, BEHAVIOR_UNRESOLVED)` 那条分支，
**产品路径上只能收到前者**：唯一调用点的实参来自 `scoreinfo.movability()`，
而它的 `claim` 值域是 `{SCORE_UNMOVABLE, None}`。

⇒ V179 说的「轴丢掉了哪一层坏」，本轮定位到更具体的一句：
**行为层的读数从来没有到达过轴** —— 不是粒度不够，是**少一段线**。

## §2 逐格读数（真池 6 格）

| 格 | 分数级 | 行为级 | n_beh | 直接数出异行为同分 | R_score | R_beh |
|---|---|---|---|---|---|---|
| m01_records | COARSE | INJECTIVE | 3 | 否 | 会 | 不会 |
| **m02_vfs** | COARSE | **COARSE** | 5 | **是**（`0.2`←2 种行为） | 会 | **会** |
| m03_tiered | COARSE | INJECTIVE | 4 | 否 | 会 | 不会 |
| s01_kv_expiry | COARSE | INJECTIVE | 3 | 否 | 会 | 不会 |
| s02_limiter | COARSE | INJECTIVE | 3 | 否 | 会 | 不会 |
| s03_tx_journal | COARSE | INJECTIVE | 3 | 否 | 会 | 不会 |

**分数级 COARSE 的两个来源**（本轮才分清）：

1. **同行为多实现撞分** —— 多个实现行为一模一样、只是写法不同。这占真池 6 格里的 5 格。
   ⇒ 尺子没有问题，是**模型没动**（该给 `MODEL`）。
2. **异行为同分** —— 两种不同行为拿同一个分。只有 `m02_vfs` 这样。
   ⇒ 这才是「分辨力不足」（该给 `RULER`）。

若按分数级判（`R_score`）：6 格全给 `RULER`，**5 格是假正例**。
⇒ 分辨力是相对于**行为**说的，不是相对于实现。

## §3 判据 7 PASS / 2 FAIL（FAIL 全部有解释）

| # | 命题 | 结果 |
|---|---|---|
| P1 | `R_score` 假正例 ≥4、`R_beh` 集合 == 直接数、假正例 0 | ✅（5 / `{m02}` == `{m02}` / 0） |
| P2 | 门限同源：`n_behaviors=2` 不给、`3` 给 | ✅ `('MODEL',)` / `('MODEL','RULER')` |
| P3 | 旧输入逐个不变（V175 七式 + 行为 claim） | ✅ 7/7 |
| P4 | 去重：claim 与 beh 同时要 `RULER` ⇒ 只一次 | ✅ `('MODEL','RULER')` |
| P5 | overlay 带 beh；无 vec ⇒ **没有** beh 键 | ✅ 6/6 有、无 vec 格无 |
| P6 | 两臂真跑：部署轴逐格相同 + 带 vec 池恰 `m02` 不同 | ✅ 部署 0 格不同｜池 1 格不同 |
| P7 | 两个变异体都让判据转红 | ❌ **A 转红（P1、P2）、B 哑火** |
| P8 | 「3 行为类 + 全 1.0」格上真规则与 B 可分 | ❌ **那个格构造不出来** |
| P9 | 账本路径上 B 与真规则**等价** | ✅ 6 池 × 2 reason 差集 **0** |

### P6 的两臂读数（真跑，不是推理）

```
两臂自报：新 33b8e9c106a7 / 旧 4229b070940d｜还原后 sha 一致=True
部署账本：36 行 / 24 格 / esc 2 格 ⇒ 轴不同 0 格
带 vec 池：esc 6 格 ⇒ 轴不同 1 格
  m02_vfs   旧 ['CRITERION'] → 新 ['CRITERION','RULER']
```

## §4 三条自踩（都留档，⛔ 不删痕迹）

1. **P7 的变异体 B 哑火**：我以为「非 INJECTIVE ⇒ RULER」会误伤，实测在真池上与真规则
   **逐格相同**。真池 6 格只有 `COARSE`(1) 与 `INJECTIVE`(5)，没有 `CONSTANT`；而
   `CONSTANT` 的格早被分数级 `SCORE_UNMOVABLE` 送了 `RULER`。
2. **P8 的构造根本不存在**：我押「造一个『3 行为类 + 全 1.0』的格就能分开两者」。
   实测过不了行内自洽闸 —— 账本路径要求 `score == mean(vec)`（V178），
   `score=1.0` ⟹ `vec` 全 True ⟹ **只有一个行为类**。想要「多行为类同分」，
   向量只能同权重不同位置 ⟹ 分数必 `< 1.0` ⟹ 分数级 claim 必然触发。
   这条不是「池子恰好没有」，是**结构上不可能**（见 PREREG_v180_v3 §1）。
3. **AZ141e 的夹具写错**：第一版构造用了「3 个不同分数」以为能造出分数级 COARSE，
   实测那是 `INJECTIVE`（3 实现 3 档）。分数级 COARSE 必须靠**同分**，
   而建这条夹具时正好暴露了 §2 的那个区分（同分可能来自同行为）。

## §5 边界（⛔ 不许放大）

1. **部署上触发 0 格**：现账本 36 行的键只有
   `{caliber, caliber_sig, candidate, family, passed, score, session, task_id}`
   —— **既无 `impl_sha` 也无 `vec`**（它们是 V175/V178 之后才有的字段，这 36 行写在之前）
   ⇒ 行为级池组不起来（`n_no_impl`/`n_no_vec` 全额），overlay 为 `None`。
   ⚠️ 这不是「接线缺一段」：`run.py` 的两处写点（`:2558` 套件行、`:3019` 观测行）
   已经在写这两个键；缺的是**一段新账本**。本轮改动在部署上**一格轴都没变**（P6 实测）。
2. **只有 1 格拿到了新轴**（`m02_vfs`），而且只在带 `vec` 的账本上。
3. ⛔ **不说「消费做完了」**：轴仍然只被印出来，没有任何代码路径拿它决定动作。
   本轮补的是「诊断那一层接到了轴」，不是「轴接到了动作」。
4. ⛔ **不新增轴名**：值域仍是 `CRITERION`/`MODEL`/`RULER`（AZ141b）。
5. **P9 的等价性只在账本路径成立**：`behavioral()` 本身不吃逐行自洽闸
   （V178 §0.5：那是传输面的事），裸三元组上 B 与真规则**可以**不同。

## §6 门禁

契约 **733/0**（+AZ141a~f）｜锚层 **97/0**｜撤回态 **297**（+AZ141c、AZ141e，
各自演练：红=只目标、多红=无、还原后 733/0）｜文档计数 11 处 0 漂移。

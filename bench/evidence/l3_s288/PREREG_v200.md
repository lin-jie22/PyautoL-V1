# V200 预注册：critique 通道的**可用量**（写于任何新读数之前）

V199 把账算到「累积可用 1/3059」，并留下两条载体：`facts`(4) 与 `critique`(288)。
`facts` 已量过（1 条可召回、0 条领域知识）。**`critique` 288 条一次都没量过投喂。**

本轮只问一件事：

> **把投喂开关打开，288 条里到底有几条能进 prompt？进去的那几条里，有几个字符是「可迁移的知识」？**

⛔ 不包装成新装置；⛔ 不给旧记录补字段；⛔ 不造「可信度分数」。
⛔ 不跑真模型（本轮量的是**读通道**，与模型无关；真模型另起一轮）。

---

## 1. 押注（六条，逐条可判）

| 号 | 押 | 依据（写在押之前） |
|---|---|---|
| **P1** | 288 条存量对**当前在册 9 套件 × 2 打分器 = 18 种口径**的可达条数 = **0** | 288 条摊在 **6 个 `scorer_sha` × 40 个 `suite_sha`** 上；`admissible()` 要求两把 sha **逐字相等**（`critique.py:118,121`）。套件内容一改 `suite_sha` 就变 ⇒ 存量天然跨口径 |
| **P2** | 同一套件连跑：R1(off) 写 6 条 ⇒ R2(prev) 每题 admissible=**5**（排除同题），进 prompt 的行数=**3**（`crit[-3:]`，`core.py:2265`） | 硬编码窗口。**可达 ≠ 送达**，这一步就是要量出两者差 |
| **P3** | R2 投喂的行里，`answer` 字段（288 条全都存了）出现 **0** 次 | `Critique.as_line()`（`critique.py:61-63`）只渲染 `family/task_id` + `score` + `why[:120]`，**不渲染 answer** |
| **P4** | R2 投喂的 3 行里，`why` 为纯通过语（形如「通过 N/N」）的 ≥ **2/3** | 288 条里 `score=1.0` 有 **200 条**（69.4%），而满分条的 `why` 是「通过 5/5」 |
| **P5** | `prev-self`（`allow_self=True`）与 `prev` 的**行数相同**（3），但第一行的 `task_id` = **本题** | `pick = [self_c[-1]] + others[-2:] if self_c else crit[-3:]`（`core.py:2265`）⇒ 开自评只换内容、不换行数 |
| **P6** | `--goal` 路径下 `served` **恒 0**，且 `rejected` 三项**全 0**（不是被挡，是签名不全直接 return） | `_admissible_critiques` 首个分支：`if not task_id or not scorer_sha or not suite_sha: return []`（`core.py:2030`）；`_build()` 不传签名 |

**主判据行（唯一）**：
```
可用知识载荷 = R2 投喂的 3 行里「含任务解法与错误定位的字符数」
预注册口径：why 既非「通过 N/N」、且不含 assert 期望值 ⇒ 才计为知识字符。
```

## 2. 装置

```
A 静态（不跑）：9 个 suite 文件 × 2 个打分器 = 18 种 (scorer_sha, suite_sha)，
              与 288 条存量求交 ⇒ 每条存量是否可达；报「可达条数 / 288」。
B 动态（跑）：V1_STATE_DIR=/tmp/v200_state（副本），真入口 `run.py --bench --offline`：
              R1  --critique-scope=off        写账本
              R2  --critique-scope=prev       读账本（排除同题）
              R3  --critique-scope=prev-self  读账本（放行同题）
              R4  --critique-scope=all        轮内也投（接线验证档）
              逐轮取：stdout 的「载入/赠送」行、`评审 落盘/投喂` 行；
              另用真 `Core` + `state_log` 取**逐题逐行的 `[外部评审]` 原文**。
```
隔离自证（V199 纪律 99）：跑前跑后记 `state/critique.jsonl`、`state/memory.db` 的
**mtime + sha256**；任一变化 ⇒ 本轮读数作废。

## 3. 会推翻结论的条件（先写）

1. 若 P1 不为 0（存量真能跨口径到达）⇒ 「口径隔离」这条解释作废，改为「存量本身可用」。
2. 若 P2 的「进 prompt 行数」> 3 ⇒ 我对 `crit[-3:]` 的理解错了，全表重算。
3. 若 `[外部评审]` 在 state 里出现 0 次而 `served` > 0 ⇒ **送达断在渲染**，
   这是比「没东西可送」更严重的结论，必须单列。
4. 若隔离自证失败 ⇒ 读数作废，不解释。

## 4. 作用域（⛔ 越界即作废）

StubLLM × `coding_small`（6 题）× 单轮 × 本机 `state/` 副本 × 部署默认（`raw` 模式、
`state_chars` 默认、`recall_k` 默认）。
⛔ 不覆盖真模型、⛔ 不覆盖 `memory_mode=abstract`、⛔ 不覆盖跨套件累计跑、
⛔ 不覆盖 `--critique-drop` 消融。

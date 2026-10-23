# reseek_s68 —— 「别重复」为什么一直不起作用（第六十八轮，2026-10-23）

## 这份证据要回答的三件事

| 问题 | 证据文件 | 结论 |
|---|---|---|
| 同一个（已被外部拒绝的）任务，内核会不会**重复执行**？ | `run_before_fix_repeats.log` | **会**：`agent:code_agent` 执行 **2 次**，`repeats_skipped=0`、`attempted=2`，最后 `cascade` 交零 |
| 修好之后同一条路径重演了吗？ | `run_after_fix.log` | **没有重演** —— 模型第一步就写了 `finish`（见下「两条不能读成的东西」③） |
| 那"修好了"靠什么证明？ | 契约 `AZ62–AZ65`（`tests/contract_test.py`） | 确定性复现：同一 `task`、不同剩余墙钟 ⇒ 外部能力**只被调用一次**（撤回态 ⑮ 回到 2 次） |

## 病根（不是"提示词不够重"）

`action_signature()` 是为了回答「**这是不是同一个动作**」而存在的。它当时把
`dict(args)` 全量排序算进签名。第六十七轮把答案型外部能力的入参统一成
`answer_task(goal, deadline_s)` 之后，`deadline_s` 是**现算的剩余墙钟** ——
于是同一件事每轮的签名都不一样，`attempted_sigs` 再也认不出重复：

```
R1  agent:code_agent({'task': <原文>, 'deadline_s': 74.9})  → 外部拒绝
R2  agent:code_agent({'task': <原文>, 'deadline_s': 50.4})  → 判成"新动作" → 又执行一次
R3  agent:code_agent({'task': <原文>, 'deadline_s': 30.0})  → 额度不足，未注入
⇒ ⛔ 全部候选被拦 (3 次连续失败) ⇒ ✋ cascade ⇒ 交付为空
```

★ **闸还在，判据被污染了。** 这是比"提示词不管用"更硬的一层：它不需要模型配合，
纯机器判据就已经失效。修法是给签名加一条规则（`_NON_IDENTITY_ARGS`），
**不是**不传 `deadline_s`（那是预算，不是上限）。

## 复现命令

```bash
# 探针（真模型 + 确定性拒绝的外部能力；外部能力的描述与被替换的真能力一致）
V1_LLM_BASE_URL=http://127.0.0.1:11434/v1 V1_LLM_API_KEY=ollama \
V1_LLM_MODEL=qwen2.5:7b python3 bench/probe_reseek.py

# 确定性判据（不需要模型、不需要网络）
python3 tests/contract_test.py          # AZ62–AZ65
python3 bench/withdraw_check.py AZ62 AZ63 AZ64 AZ65
```

`run_probe_v1_misdirected.log` 是探针**第一版**的读数：它一次都没触发拒绝 ——
因为我把"这个能力只会拒绝"写进了工具描述，模型读到就绕开了。
**这不是模型不听话，是我把答案写在目录里了**，留档防止再写一遍。

## 两条不能读成的东西

① 它**不是**能力读数。题面故意不含『函数名必须是 X』，外部能力必然拒绝；
   这里量的是「内核会不会把同一件事做第二遍」，与做题水平无关。
② 它**不是**"修好了就能多拿分"。`cascade` 与 `stalled` 在计分上都是 0；
   这一轮换掉的是"为什么是 0"的可归因性，不是 0 本身。
③ `run_after_fix.log` **没有**重演重复路径：那一次模型第一步就写了 `finish`（1 步完成）。
   ⚠️ 所以**不许**用"修后这一跑成功了"来当修复证据 —— T=0 也不保证跨请求确定性，
   而状态里含**墙钟**（`wall=12.3s/120s`），提示词每次都不一样。
   修复证据只有一条：契约 AZ64（同 task、不同 deadline ⇒ 只调用一次），
   以及撤回态 ⑮（去掉过滤 ⇒ 回到调用两次）。

## 文件指纹

见 `FILES.sha256`（`shasum -a 256 -c FILES.sha256` 可校验）。
`bench/probe_reseek.py` 的 sha 在 `FILES.sha256` 里同样登记 —— 探针自己也会被改，
不登记就等于"证据来自一个已经不存在的量具"。

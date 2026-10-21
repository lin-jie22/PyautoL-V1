#!/usr/bin/env python3
"""7y 附证（零模型）：**题面字面**与**打分器真值**在 g7e2 上是互相矛盾的。

用法：
    python3 bench/evidence/knowledge_s60/recheck_oracle_vs_prompt.py

为什么必须单独留一份：

  probe_7y.py 里 F2 那条 `len(truncate_tail(s, n)) <= n` 是从题面原话
  「把过长的字符串截短到**最多 n 个字符**」读出来的 —— 一个字没加。
  而它在 40/40 份满分答案上**全部误判**。所以必须回答一个二选一：

      ① 是我读错了；② 是题面自己站不住。

这一份脚本证明是 ②：`tasks['oracle']` 跑出来的公认正确解本身就违反那句话。
⇒ ⛔ 结论写进 DESIGN：**在这类"惯例不在题面里"的任务上，题面字面不可当作判据来源。**
   它与 §16.9「判据的来源比判据的松紧更重要」是同一件事的第二面：
   来源不是越"贴题面"就越安全 —— 题面可能自己含着与真值冲突的字面。
"""
import importlib.util
import json
import os

ROOT = "/Users/linshaojie/V1"
SPEC = importlib.util.spec_from_file_location(
    "code_exec", os.path.join(ROOT, "bench/scorers/code_exec.py"))
CE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CE)

tasks = {json.loads(l)["id"]: json.loads(l)
         for l in open(os.path.join(ROOT, "bench/heldout/g7j_n4_heldout.jsonl"),
                       encoding="utf-8")}
t = tasks["g7e2_truncate_tail"]
print("题面原话：", [s for s in t["prompt"].split("。")
                if "最多" in s][0] + "。")

g = {}
exec(t["oracle"], g)
for s, n, want in (("abcdef", 3, "…def"), ("0123456789", 4, "…6789"),
                   ("abcdefg", 2, "…fg")):
    got = g["truncate_tail"](s, n)
    print(f"  oracle({s!r},{n}) = {got!r}  len={len(got)}  "
          f"len<=n ? {len(got) <= n}   （打分器要求 == {want!r}）")

print()
print("打分器全部 5 条断言在这份 oracle 上的通过情况：")
for tt in t["tests"]:
    ok, why = CE.run_one(t["oracle"], tt)
    print(f"  {'✓' if ok else '✗'} {tt}" + (f"  ⇒ {why}" if not ok else ""))
print()
print("⇒ oracle 拿 5/5 满分，同时逐条违反题面里那句『最多 n 个字符』。")
print("  这是题面自身的不一致，不是我漏读了某个字。")

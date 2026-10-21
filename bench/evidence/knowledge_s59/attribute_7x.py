#!/usr/bin/env python3
"""★7x 归因（零模型）：把每条臂**最终交付的答案**拿去真跑外部打分器，
逐条列出 5 个断言里哪几条过、哪几条不过。

为什么要有这一步：`runs.jsonl` 只给 0.2 / 0.4 / 0.6 / 1.0，而 0.4 = 2/5。
"哪 2 条"才是机制证据 —— 例如 g7e1 的 0.4 如果是
`'y'→True` 与 `'maybe'→None` 过、`'true'/'TRUE'/'on'` 全不过，
那它用的就是**常识真值表**而不是记忆里的 ZK3（ZK3 说 true/on 一律算假）。

⛔ 不调用模型；只跑本地打分器（与 bench 同一份 code_exec.py）。
"""
import glob
import importlib.util
import json

SPEC = importlib.util.spec_from_file_location(
    "code_exec", "/Users/linshaojie/V1/bench/scorers/code_exec.py")
CE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CE)

D = "/tmp/v1_7x"
ARMS = ("A", "R1", "R2", "M0")
TASKS = {"g7d2_dedupe_keep": "dedupe_keep", "g7d3_fmt_amount": "fmt_amount",
         "g7e1_parse_flag": "parse_flag", "g7e2_truncate_tail": "truncate_tail"}
SUITE = {json.loads(l)["id"]: json.loads(l)
         for l in open("/Users/linshaojie/V1/bench/heldout/g7j_n4_heldout.jsonl",
                       encoding="utf-8")}


def final_answers(path):
    """把 cap 里 request/response 配对，返回 {task_id: 最后一次**答案调用**的正文}。

    ⛔ 只认 `answer()` 那条通道（system 里带"只输出最终答案本身"）：
       规划器那条的响应是 JSON，把它当答案会让每题都变成 0.00
       （第一次这么写就踩了：A 的 g7d2 报 0.00，而 runs.jsonl 是 1.0）。
    """
    out, pend = {}, None
    for line in open(path, encoding="utf-8"):
        ev = json.loads(line)
        if "#resp" in ev["path"]:
            if pend is None:
                continue
            try:
                body = json.loads(ev["body"])
                txt = body["choices"][0]["message"].get("content", "") or ""
            except (KeyError, ValueError, TypeError):
                pend = None
                continue
            out[pend] = txt
            pend = None
            continue
        if not ev["path"].endswith("/chat/completions"):
            continue
        try:
            msgs = json.loads(ev["body"]).get("messages", [])
        except ValueError:
            continue
        if not msgs or "只输出最终答案本身" not in msgs[0].get("content", ""):
            pend = None
            continue
        u = msgs[-1].get("content", "")
        pend = next((t for t, f in TASKS.items() if f in u), None)
    return out


print(f"{'臂':3s} {'rep':3s} {'题':22s} {'分数':>5s}  逐条断言")
print("=" * 100)
for arm in ARMS:
    for rep in ("r1", "r2", "r3"):
        p = f"{D}/cap{arm}_{rep}.jsonl"
        ans = final_answers(p)
        for tid in TASKS:
            task = SUITE[tid]
            code = CE.extract_code(ans.get(tid, ""))
            marks, passed = [], 0
            for t in task["tests"]:
                ok, _msg = CE.run_one(code, t)
                marks.append("✓" if ok else "✗")
                passed += ok
            print(f"{arm:3s} {rep:3s} {tid:22s} {passed / len(task['tests']):5.2f}  "
                  f"{' '.join(marks)}")

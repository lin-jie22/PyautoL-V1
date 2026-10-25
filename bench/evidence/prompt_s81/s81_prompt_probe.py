"""第八十一轮探针：prompt 是确定性开关（DESIGN §44）。

⛔ 内核外测量。零内核 diff、不改 `DIRECT_SYSTEM`。
回答三个问题：
  Q1 只改 system prompt 的一句话，同一模型同一题能摆多少格？
  Q2 这个摆动可复现吗（T=0 两次是否逐字相同）？
  Q3 三条链路合起来的「逐题 max」是多少 —— 即「选择器」值多少格？

用法（必须本地 ollama，T=0）：
    python3 bench/evidence/prompt_s81/s81_prompt_probe.py
读数直接打印，不写台账（它不是内核读数，不该进 `runs.jsonl`）。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

FENCE = re.compile(r"```python[ \t]*\r?\n(.*?)```", re.S)

#: 与 `run.py:146` **逐字相同**（⛔ 不许在这里"顺手改好一点"）。
DIRECT_SYSTEM = (
    "你是代码助手。只输出一个 python 代码块，不要任何解释、不要示例调用。"
    "函数名与签名必须与题目要求完全一致。"
)
#: 对照组：只换这一句，user 内容完全相同。
PLAIN_SYSTEM = "你是一个严谨的 Python 程序员。"


def call(system: str, user: str, model: str = "qwen2.5:7b") -> str:
    body = json.dumps({
        "model": model, "stream": False,
        "options": {"temperature": 0.0, "num_predict": 2048},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["message"]["content"]


def extract(a: str) -> str:
    b = FENCE.findall(a or "")
    return "\n\n".join(x.strip() for x in b if x.strip()) if b else (a or "").strip()


def score(code: str, tests: list[str], fn: str) -> int:
    ns: dict = {}
    try:
        exec(compile(code, "<answer>", "exec"), ns)               # noqa: S102
    except Exception:                                             # noqa: BLE001
        return 0
    if fn not in ns:
        return 0
    ok = 0
    for t in tests:
        try:
            exec(t, ns)                                           # noqa: S102
            ok += 1
        except Exception:                                         # noqa: BLE001
            pass
    return ok


def main() -> int:
    tasks = {json.loads(l)["id"]: json.loads(l)
             for l in open("bench/specgen/tasks_v1.jsonl") if l.strip()}
    # 内核臂：`bench/evidence/evolve_s80b/pool_cand.jsonl` 的 10 次逐字相同，取一次
    kernel = {"sg01": 12, "sg02": 10, "sg03": 14, "sg04": 14, "sg05": 0, "sg06": 8}

    print("=" * 66)
    print("Q1 三臂逐题（Q2 的两次调用就埋在下面：每个 prompt 调两次）")
    print("=" * 66)
    print(f"{'题':7}{'内核(2档)':>11}{'DIRECT':>9}{'DIRECT×2':>10}"
          f"{'PLAIN':>8}{'PLAIN×2':>9}{'逐题max':>9}")
    tot = {"k": 0, "d": 0, "p": 0, "m": 0}
    stable = True
    for tid in sorted(tasks):
        t = tasks[tid]
        tests = t["tests"] if isinstance(t["tests"], list) else json.loads(t["tests"])
        fn = f"norm_{tid}"
        d1 = score(extract(call(DIRECT_SYSTEM, t["prompt"])), tests, fn)
        d2 = score(extract(call(DIRECT_SYSTEM, t["prompt"])), tests, fn)
        p1 = score(extract(call(PLAIN_SYSTEM, t["prompt"])), tests, fn)
        p2 = score(extract(call(PLAIN_SYSTEM, t["prompt"])), tests, fn)
        stable = stable and d1 == d2 and p1 == p2
        mx = max(kernel[tid], d1, p1)
        tot["k"] += kernel[tid]; tot["d"] += d1
        tot["p"] += p1; tot["m"] += mx
        print(f"{tid:7}{kernel[tid]:>11}{d1:>9}{d2:>10}{p1:>8}{p2:>9}{mx:>9}")

    n = sum(len(t["tests"] if isinstance(t["tests"], list)
                else json.loads(t["tests"])) for t in tasks.values())
    print("-" * 66)
    print(f"{'合计':7}{tot['k']:>11}{tot['d']:>9}{'':>10}{tot['p']:>8}{'':>9}{tot['m']:>9}"
          f"   (/ {n})")
    print(f"{'均值':7}{tot['k']/n:>11.4f}{tot['d']/n:>9.4f}{'':>10}"
          f"{tot['p']/n:>8.4f}{'':>9}{tot['m']/n:>9.4f}")

    print()
    print("=" * 66)
    print("Q2 可复现性（T=0，每个 prompt 跑两次是否逐字同分）")
    print("=" * 66)
    print(f"  全部题两次同分 = {stable}")
    if not stable:
        print("  ⛔ 有不稳定的题 ⇒ 本节结论必须写成「读数不可复现」而不是「prompt 是开关」")

    print()
    print("=" * 66)
    print("Q3 选择器的货币价值")
    print("=" * 66)
    best_single = max(tot["k"], tot["d"], tot["p"])
    print(f"  最好的单一链路 = {best_single}/{n} = {best_single/n:.4f}")
    print(f"  逐题 max       = {tot['m']}/{n} = {tot['m']/n:.4f}")
    print(f"  ⇒ 差 {tot['m'] - best_single} 格 = {(tot['m']-best_single)/n:.4f}")
    print("  ⛔ 这是**上界**：它要求每题都挑中最好的那条，而挑需要 oracle（尺子，不是能力）")
    return 0 if stable else 1


if __name__ == "__main__":
    sys.exit(main())

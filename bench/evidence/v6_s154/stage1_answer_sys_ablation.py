"""V65 第二刀：`_ANSWER_SYS` 的**精确变体**消融（改内核之前先量）。

为什么不能用上一轮的结论直接改：
  上一轮探针的 system 是 `DIRECT_SYSTEM`（"只输出一个 python 代码块…"），
  而 `_ANSWER_SYS` 是另一句：「只输出最终答案本身：不要 JSON、不要解释、不要道歉、
  不要代码块围栏。如果是代码，直接输出代码原文。」
  ⇒ 前半句「只输出最终答案**本身**」本身就在要求不解释，删了「不要解释」**可能没用**。
  纪律 33：改载荷之前先量它到底有没有用。

prompt 用 `Planner.answer()` 的**真实模板**（`目标：{goal}` + 状态块 + `请直接给出最终答案。`），
state 传空（隔离变量；真实路径带状态，这一点写在边界里）。

⛔ 判据仍是题集自带的 6 条 `tests`（不另造）。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

os.environ["no_proxy"] = "127.0.0.1"
os.environ["NO_PROXY"] = "127.0.0.1"
BASE = "http://127.0.0.1:11434/v1/chat/completions"
MODEL = os.environ.get("PROBE_MODEL", "qwen2.5:7b")
MAXTOK = int(os.environ.get("PROBE_MAXTOK", "1024"))   # 与 Planner.answer 的 max_tokens 同档

TASK = next(json.loads(x) for x in
            open("/Users/linshaojie/V1/bench/tasks/coding_small.jsonl", encoding="utf-8")
            if "t04_merge_ranges" in x)

#: `mw/planner.py:158` 的原文，逐字相同
_V0 = ("只输出最终答案本身：不要 JSON、不要解释、不要道歉、不要代码块围栏。"
       "如果是代码，直接输出代码原文。")

_V = {
    "v0_orig": _V0,
    # v1：只删「不要解释、」四个字 + 顿号
    "v1_del_noexp": ("只输出最终答案本身：不要 JSON、不要道歉、不要代码块围栏。"
                     "如果是代码，直接输出代码原文。"),
    # v2：把「最终答案本身」也松掉（怀疑前半句同样有害）
    "v2_relax_head": ("直接给出答案：不要 JSON、不要道歉、不要代码块围栏。"
                      "如果是代码，直接输出代码原文。"),
    # v3：只留形态（最极端，但**文本题会没有指令**）
    "v3_shape_only": "如果是代码，直接输出代码原文。",
    # v4：正向、且对代码/文字都成立
    "v4_pos_both": "直接输出最终答案：代码就给代码原文，文字就给正文。",
    # v5：最短正向
    "v5_pos_min": "直接输出最终答案本身。",
    # v6：分支式正向
    "v6_pos_if": "如果是代码，直接输出代码原文；如果是文字，直接输出正文。",
}

PROMPT = f"目标：{TASK['prompt']}\n请直接给出最终答案。"


def ask(sysmsg: str) -> tuple[str, float]:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": sysmsg},
                     {"role": "user", "content": PROMPT}],
        "max_tokens": MAXTOK, "temperature": 0.0, "stream": False,
    }).encode()
    req = urllib.request.Request(BASE, data=body,
                                headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode())
    return d["choices"][0]["message"]["content"], time.time() - t0


def extract(text: str) -> str:
    text = re.sub(r"</?think>.*?</think>", "", text, flags=re.S)
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, flags=re.S)
    return (blocks[-1] if blocks else text).strip()


def score(code: str) -> tuple[int, str]:
    ok, first = 0, ""
    for a in TASK["tests"]:
        try:
            p = subprocess.run([sys.executable, "-c", code + f"\n\n{a}\n"],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            first = first or "TIMEOUT"
            continue
        if p.returncode == 0:
            ok += 1
        elif not first:
            first = (p.stderr.strip().splitlines() or ["?"])[-1][:100]
    return ok, first


print(f"模型 {MODEL}  max_tokens={MAXTOK}  T=0  prompt=Planner.answer 模板（state 空）")
print(f"任务 {TASK['id']}（6 条断言）\n")
rows = []
for name, sysmsg in _V.items():
    txt, dt = ask(sysmsg)
    code = extract(txt)
    ok, first = score(code)
    rows.append((name, ok, dt, len(txt), first))
    print(f"  {name:16s} {ok}/6   {dt:5.1f}s  {len(txt):4d}字符  {first[:52]}")

print("\n" + "=" * 70)
base = rows[0][1]
for name, ok, dt, n, first in rows:
    d = "" if ok == base else f"  ← {'+' if ok > base else ''}{ok - base}"
    print(f"  {name:16s} {ok}/6{d}")
print("=" * 70)
print("\n判读：")
print("  · 只有 v2/v3 能到 6/6 ⇒ 前半句「只输出最终答案本身」同样是病因，v1 不够")
print("  · v1 就到 6/6 ⇒ 只要删「不要解释、」，改动可以最小")
print("  · 全部 4/6 ⇒ 这一句不是内核路径的病根，别改它（去查 _SYS:95 与 :118）")

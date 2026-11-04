"""**形状噪声下限**：同一内核、同一题集、同一 system —— 只改 user prompt 的"形状"。

为什么要量它（这是本轮唯一还值得做的事）
========================================
`probe_state_run3.txt` 在 t04 上读出三档分数，输入之间的差别**语义上无关**：

    state=""                6/6  515 字符
    state="[第 4/12 步]"     2/6  489 字符    ← 10 个字符
    state=无关填充 400 字     4/6  352 字符    ← 不含题面/代码/报错

⇒ 单题单次的 1 比特读数，可以被**任意**的极短扰动翻转，而且不是单调的。
   那么"某个改动 Δ=0"就不再是"改动无用"，而是**这把尺子分辨不出来**。

于是本轮要交的是一个之前欠了十轮的量：**噪声下限**。
没有它，任何 Δ 都不可解释（纪律 1 的四项前提里，"噪声下限"就排在第二）。

三档"形状"（⛔ 都不改 system、不改题面、不改判据）
================================================
  S0  state=""                —— 最短：`目标：X` + `请直接给出最终答案。`
  S1  state="[第 4/12 步]"     —— 真实路径**必然**会带的最小非空块
  S2  state=无关填充 400 字     —— 长度控制（无任何语义）

⛔ 判据仍是每题自带的 `tests`；⛔ 不另造题；⛔ 不挑题（6 题全跑）。
⚠️ 单次（T=0 下确定性，`RUNS=2` 已在前两刀验过同码）；本文件只报**总分差**，
   不报单题显著性 —— 一题一比特，那正是要暴露的问题本身。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

os.environ.setdefault("no_proxy", "127.0.0.1")
os.environ.setdefault("NO_PROXY", "127.0.0.1")

ROOT = "/Users/linshaojie/V1"
sys.path.insert(0, ROOT)

from mw.planner import Planner, _ANSWER_SYS   # noqa: E402

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:11434/v1/chat/completions")
MODEL = os.environ.get("PROBE_MODEL", "qwen2.5:7b")
MAXTOK = int(os.environ.get("PROBE_MAXTOK", "1024"))

TASKS = [json.loads(x) for x in
         open(f"{ROOT}/bench/tasks/coding_small.jsonl", encoding="utf-8") if x.strip()]

_FILLER_UNIT = "今天天气一般，路上有点堵，出门记得带伞。清单上还剩两件事没做。"


def filler(n: int) -> str:
    s = ""
    while len(s) < n:
        s += _FILLER_UNIT
    return s[:n]


SHAPES = {
    "S0_empty": "",
    "S1_step": "[第 4/12 步]",
    "S2_filler400": filler(400),
}


class _Rec:
    timeout_s = 120

    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []

    def complete(self, messages, **kw):   # noqa: ANN001, ANN003
        self.msgs = [(m.role, m.content) for m in messages]
        return ""


def kernel_user(goal: str, state: str) -> str:
    rec = _Rec()
    Planner(rec, None, max_tokens=MAXTOK, state_chars=2400, salvage=0).answer(
        goal, state=state)
    return rec.msgs[1][1]


def ask(user: str) -> str:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": _ANSWER_SYS},
                     {"role": "user", "content": user}],
        "max_tokens": MAXTOK, "temperature": 0.0, "stream": False,
    }).encode()
    req = urllib.request.Request(BASE, data=body,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"]


def extract(text: str) -> str:
    text = re.sub(r"</?think>.*?</think>", "", text, flags=re.S)
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, flags=re.S)
    return (blocks[-1] if blocks else text).strip()


def score(code: str, tests: list[str]) -> int:
    ok = 0
    for a in tests:
        try:
            p = subprocess.run([sys.executable, "-c", code + f"\n\n{a}\n"],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            continue
        if p.returncode == 0:
            ok += 1
    return ok


print("=" * 78)
print(f"model={MODEL} max_tokens={MAXTOK} T=0 system=当前内核（{len(_ANSWER_SYS)} 字符）")
print(f"题数={len(TASKS)}；三档形状只差 user prompt 的尾部块，system/题面/判据全同")
print("=" * 78)

rows: dict[str, dict] = {}
tot = {k: 0 for k in SHAPES}
den = 0
for t in TASKS:
    den += len(t["tests"])
    line = f"{t['id']:22s}"
    cells = {}
    for name, st in SHAPES.items():
        txt = ask(kernel_user(t["prompt"], st))
        ok = score(extract(txt), t["tests"])
        cells[name] = (ok, len(t["tests"]), len(txt))
        tot[name] += ok
        line += f"  {name}={ok}/{len(t['tests'])}"
    rows[t["id"]] = cells
    flip = "  ← 三档不全同" if len({v[0] for v in cells.values()}) > 1 else ""
    print(line + flip)

print("\n" + "=" * 78)
print(f"{'形状':16}{'总分':>12}{'题级均值':>12}")
for name in SHAPES:
    print(f"{name:16}{f'{tot[name]}/{den}':>12}{tot[name] / den:>12.4f}")
xs = list(tot.values())
print(f"\n三档极差 = {(max(xs) - min(xs)) / den:.4f}（分母 {den} 条断言）")
print("⚠️ 这个极差就是**形状噪声下限**：任何 Δ 小于它，本装置都分辨不出。")
print("\nJSON:", json.dumps({"tot": tot, "den": den,
                            "per_task": {k: {n: v[0] for n, v in c.items()}
                                         for k, c in rows.items()}},
                           ensure_ascii=False))

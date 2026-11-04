"""V65 追查第五刀：**state 是"内容"在起作用，还是"非空"本身在起作用**。

前两刀的读数（都在这份证据目录里）
==================================
`probe_state_run1.txt`（真实 state 下改 system / 删 fidelity）：

    A 旧 system + 真实 state      2/6  489
    B 新 system + 真实 state      2/6  489   ← 与跑批 `📦 收尾交付 489 字符` 逐字对上
    C 新 system + state−fidelity  2/6  489
    D 新 system + state=""        6/6  515
    E 旧 system + state=""        2/6  396

`probe_state_run2.txt`（把真实 state 逐段删）：

    H 原样                     2329 字  3 处代码  2/6  489
    F 删两处重复载荷           1325 字  1 处代码  2/6  489
    G 删整段 [轨迹]             444 字  0 处代码  2/6  487
    J 三处载荷全换占位           823 字  0 处代码  2/6  487
    K 删报错原文               2247 字  3 处代码  2/6  489

⇒ **state 里一个字都不含代码，照样 2/6**。上一刀"复制旧代码"的推断被否证。
   现在只剩一个假设没验：**非空状态块本身**（而不是它写了什么）。

六臂（判据仍是题集自带的 6 条 `tests`）
======================================
  D   state=""                                   正对照，应 6/6
  P0  `[第 4/12 步]` 一行（14 字）
  P1  `[目标] …` 一行（= 目标重复一次）
  P2  目标行 + 步号行
  P3  `[工作记忆] …` 原样（含两处 error 原文）
  P4  **无关填充 400 字**（与题目毫无关系的文字）
  P5  **无关填充 2300 字**（与"原样 state"等长，控制长度变量）

⛔ P4/P5 是**纯填充**：它们不含任何题面、代码、报错。若它们也 2/6，
   那么「state 内容有害」这个方向整条作废 —— 变的是**非空**这件事本身。
⚠️ 这六臂全是**派生输入**，只回答"哪一段是变量"，不够格当"改内核就会变好"的证据。
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
RUNS = int(os.environ.get("PROBE_RUNS", "2"))
STATE_FILE = os.environ.get("PROBE_STATE", "/tmp/v65sd/state.jsonl")

TASK = next(json.loads(x) for x in
            open(f"{ROOT}/bench/tasks/coding_small.jsonl", encoding="utf-8")
            if "t04_merge_ranges" in x)
_ROWS = [json.loads(x) for x in open(STATE_FILE, encoding="utf-8") if x.strip()]
REAL_STATE = [r for r in _ROWS if r.get("task") == "t04_merge_ranges"][-1]["state"]

#: 从真实 state 里**原样切出**各块（不重写、不摘要）
_LINES = REAL_STATE.split("\n")
GOAL_LINE = _LINES[0]                                   # `[目标] …`
WM_LINE = next(x for x in _LINES if x.startswith("[工作记忆]"))
STEP_LINE = _LINES[-1]                                  # `[第 4/12 步]`

#: 与题目无关的填充：只由两句日常话循环构成，**不含任何**题面/代码/报错
_FILLER_UNIT = "今天天气一般，路上有点堵，出门记得带伞。清单上还剩两件事没做。"


def filler(n: int) -> str:
    s = ""
    while len(s) < n:
        s += _FILLER_UNIT
    return s[:n]


STATES = {
    "D_empty": "",
    "P0_step": STEP_LINE,
    "P1_goal": GOAL_LINE,
    "P2_goal+step": GOAL_LINE + "\n" + STEP_LINE,
    "P3_wm": WM_LINE,
    "P4_filler400": filler(400),
    "P5_filler2300": filler(2300),
}


class _Rec:
    timeout_s = 120

    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []

    def complete(self, messages, **kw):   # noqa: ANN001, ANN003
        self.msgs = [(m.role, m.content) for m in messages]
        return ""


def kernel_user(state: str) -> str:
    rec = _Rec()
    Planner(rec, None, max_tokens=MAXTOK, state_chars=2400, salvage=0).answer(
        TASK["prompt"], state=state)
    return rec.msgs[1][1]


def ask(user: str) -> tuple[str, float]:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": _ANSWER_SYS},
                     {"role": "user", "content": user}],
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


print("=" * 78)
print(f"model={MODEL} max_tokens={MAXTOK} T=0 runs/臂={RUNS} "
      f"判据={len(TASK['tests'])} 条 tests  system=当前内核（{len(_ANSWER_SYS)}）")
print(f"真实 state={len(REAL_STATE)} 字符（只用来**原样切块**，不重写）")
print("=" * 78)

res: dict[str, dict] = {}
for name, st in STATES.items():
    user = kernel_user(st)
    print(f"\n--- {name}  state={len(st)}字符 user={len(user)}字符 ---")
    per = []
    for k in range(RUNS):
        txt, dt = ask(user)
        code = extract(txt)
        ok, first = score(code)
        per.append({"ok": ok, "chars": len(txt), "sec": round(dt, 1),
                    "first": first, "code": code})
        print(f"  run{k + 1}  {ok}/{len(TASK['tests'])}  {dt:5.1f}s  "
              f"{len(txt):4d}字符  {first[:52]}")
    same = (len({p["code"] for p in per}) == 1) if per else None
    res[name] = {"per": per, "same": same, "state_chars": len(st)}
    print(f"  ⇒ 两次输出代码{'逐字相同' if same else '**不同**'}")

print("\n" + "=" * 78)
print("汇总")
print("=" * 78)
print(f"{'臂':18}{'分':>8}{'字符':>16}{'state':>8}{'同码':>6}  首错")
for name, d in res.items():
    print(f"{name:18}{'/'.join(str(p['ok']) for p in d['per']):>8}"
          f"{'/'.join(str(p['chars']) for p in d['per']):>16}"
          f"{d['state_chars']:>8}{str(d['same']):>6}  "
          f"{(d['per'][0]['first'][:40] if d['per'] else '(未跑)')}")
print("\nJSON:", json.dumps({k: {"ok": [p["ok"] for p in v["per"]],
                                "chars": [p["chars"] for p in v["per"]],
                                "state": v["state_chars"],
                                "same": v["same"]}
                            for k, v in res.items()}, ensure_ascii=False))

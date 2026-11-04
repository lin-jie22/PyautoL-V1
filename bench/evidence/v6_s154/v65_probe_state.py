"""V65 追查第三刀：**真实 state 下的 `_ANSWER_SYS` / `_ANSWER_FIDELITY` 消融**。

为什么必须再切一刀（上一刀的边界写错了）
========================================
上一刀（`v65_probe_answersys.py`，**当时只在 /tmp**）用的是 `Planner.answer()` 的真实
模板，但 `state=""` —— 也就是**状态块与 `_ANSWER_FIDELITY` 都被跳过了**。
那一刀读出：原文 2/6、纯正向 6/6，于是改了内核。

真实跑批却**一分没动**（`mean 0.8611→0.8611`），而 `kernel_closeout.chars`
792→978（+186 = 2 rep × 93 字符）⇒ **载荷确实换了，只是换出来的东西仍然错**。

本刀要回答的就是这一格：**同一份真实 state 下，两档 system 与那句 fidelity 各值多少。**

输入从哪来（⛔ 不许编）
======================
`state` 取自 `--state-dump` 的**真实落盘**（`dump_state_v65.sh`，同一份 env、同一套
`limits`），取 `t04_merge_ranges` 的**最后一条**（= `_close_out` 里
`_state(goal, history, steps_used)` 的快照）。⛔ 不是手写的样例。

四臂（判据仍是题集自带的 6 条 `tests`，⛔ 不另造尺子）
=====================================================
  A `v0_orig` + 真实 state      —— 改之前的内核
  B `v4_pos`  + 真实 state      —— **当前内核**（应当复现跑批那一格）
  C `v4_pos`  + 真实 state − fidelity 行
  D `v4_pos`  + state=""        —— 复现上一刀的 6/6（正对照）
  E `v0_orig` + state=""        —— 复现上一刀的 2/6（正对照）

⚠️ C 臂是**派生**输入（从 B 的 prompt 里删掉那一行），不是内核真能发出的 prompt
   —— 它只回答"那句话值不值得留"，不回答"改了就更好"。
⚠️ 每臂跑 `RUNS` 次（默认 2）：T=0 下两次不同 ⇒ 同构假设不成立，读数要降级。
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

from mw.planner import Planner, _ANSWER_FIDELITY, _ANSWER_SYS   # noqa: E402

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:11434/v1/chat/completions")
MODEL = os.environ.get("PROBE_MODEL", "qwen2.5:7b")
MAXTOK = int(os.environ.get("PROBE_MAXTOK", "1024"))   # 与 Planner.answer 同档
RUNS = int(os.environ.get("PROBE_RUNS", "2"))
STATE_FILE = os.environ.get("PROBE_STATE", "/tmp/v65sd/state.jsonl")

TASK = next(json.loads(x) for x in
            open(f"{ROOT}/bench/tasks/coding_small.jsonl", encoding="utf-8")
            if "t04_merge_ranges" in x)

_ROWS = [json.loads(x) for x in open(STATE_FILE, encoding="utf-8") if x.strip()]
_T4 = [r for r in _ROWS if r.get("task") == "t04_merge_ranges"]
REAL_STATE = _T4[-1]["state"]

#: 改之前的内核原文（`mw/planner.py` §132 的注释里逐字存档）
V0_ORIG = ("只输出最终答案本身：不要 JSON、不要解释、不要道歉、不要代码块围栏。"
           "如果是代码，直接输出代码原文。")


# ---------------------------------------------------------------- prompt 构造
class _Rec:
    """记录器：**什么也不生成**，只把内核真正要发的 system/user 留下来。

    ⛔ 不用手抄模板 —— 模板一改，手抄的副本就会静默过期，而那正是本文件要追的
       那类病（同一个事实两处实现）。
    """

    timeout_s = 120

    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []

    def complete(self, messages, **kw):   # noqa: ANN001, ANN003
        self.msgs = [(m.role, m.content) for m in messages]
        self.kw = dict(kw)
        return ""


def kernel_messages(state: str) -> tuple[list[tuple[str, str]], dict]:
    rec = _Rec()
    p = Planner(rec, None, max_tokens=MAXTOK, state_chars=2400, salvage=0)
    p.answer(TASK["prompt"], state=state)
    return rec.msgs, rec.kw


def ask(sysmsg: str, user: str) -> tuple[str, float]:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": sysmsg},
                     {"role": "user", "content": user}],
        "max_tokens": MAXTOK, "temperature": 0.0, "stream": False,
    }).encode()
    req = urllib.request.Request(BASE, data=body,
                                headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode())
    return d["choices"][0]["message"]["content"], time.time() - t0


# ---------------------------------------------------------------- 判据
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


# ---------------------------------------------------------------- 组臂
_msgs_real, _kw = kernel_messages(REAL_STATE)
_msgs_blank, _kw2 = kernel_messages("")
_kernel_user = _msgs_real[1][1]
_fid_block = _ANSWER_FIDELITY + "\n"
assert _fid_block in _kernel_user, "真实 state 的 prompt 里没有 fidelity 行 ⇒ 前提不成立"
_user_no_fid = _kernel_user.replace(_fid_block, "", 1)

ARMS = {
    "A_orig+state": (V0_ORIG, _kernel_user),
    "B_pos+state": (_ANSWER_SYS, _kernel_user),
    "C_pos+state-nofid": (_ANSWER_SYS, _user_no_fid),
    "D_pos+nostate": (_ANSWER_SYS, _msgs_blank[1][1]),
    "E_orig+nostate": (V0_ORIG, _msgs_blank[1][1]),
}

print("=" * 78)
print(f"model={MODEL} max_tokens={MAXTOK} T=0 runs/臂={RUNS} 判据={len(TASK['tests'])} 条 tests")
print(f"真实 state={len(REAL_STATE)} 字符（取自 {STATE_FILE} 的 t04 最后一条）")
print(f"内核 prompt: user={len(_kernel_user)} 字符  system={len(_ANSWER_SYS)} 字符 "
      f"max_tokens={_kw.get('max_tokens')}")
print("★ 内核 user prompt 里 **重复出现** 了任务原文：先 `目标：` 一次，state 的 `[目标]` 再一次")
print("=" * 78)

res: dict[str, dict] = {}
for name, (sysmsg, user) in ARMS.items():
    print(f"\n--- {name}  system={len(sysmsg)}字符 user={len(user)}字符 ---")
    per = []
    for k in range(RUNS):
        txt, dt = ask(sysmsg, user)
        code = extract(txt)
        ok, first = score(code)
        per.append({"ok": ok, "n": len(TASK["tests"]), "chars": len(txt),
                    "sec": round(dt, 1), "first": first, "code": code})
        print(f"  run{k + 1}  {ok}/{len(TASK['tests'])}  {dt:5.1f}s  "
              f"{len(txt):4d}字符  {first[:56]}")
    #: ⚠️ `RUNS=0`（只验 prompt 构造）时必须写 None —— 空集的 `len(...)==1` 是 False，
    #:    会把"没跑"印成"两两次不同"（缺席冒充结论）。
    same = (len({p["code"] for p in per}) == 1) if per else None
    res[name] = {"per": per, "same_code": same}
    print(f"  ⇒ 两次输出代码{'逐字相同' if same else '**不同**（同构假设不成立）'}")

print("\n" + "=" * 78)
print("汇总")
print("=" * 78)
print(f"{'臂':22}{'分':>8}{'字符':>18}{'同码':>6}  首错")
for name, d in res.items():
    oks = "/".join(f"{p['ok']}" for p in d["per"])
    chs = "/".join(f"{p['chars']}" for p in d["per"])
    print(f"{name:22}{oks:>8}{chs:>18}{str(d['same_code']):>6}  "
          f"{d['per'][0]['first'][:44]}")
print("\nJSON:", json.dumps({k: {"ok": [p["ok"] for p in v["per"]],
                                "chars": [p["chars"] for p in v["per"]],
                                "same": v["same_code"]}
                            for k, v in res.items()}, ensure_ascii=False))

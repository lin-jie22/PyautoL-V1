"""V65 追查第四刀：**state 内部消融** —— 是哪一段钉住了答案。

上一刀（`v65_probe_state.py`）的读数（`probe_state_run1.txt`）：

    A 旧 system + 真实 state   2/6  489 字符
    B 新 system + 真实 state   2/6  489 字符   ← 与跑批 `📦 收尾交付 489 字符` 逐字对上
    C 新 system + state−fidelity 2/6 489 字符
    D 新 system + 无 state     6/6  515 字符
    E 旧 system + 无 state     2/6  396 字符

⇒ 句子不是变量（A/B/C 三臂同分同长），**state 才是**。
   于是本轮唯一该问的是：state 里**哪一段**把它钉住的。

真实 state（2329 字符）的结构与占比是**实测**的，不是猜的：

    [目标]        ~110 字
    [工作记忆]    两条（其中一条带 `[回看 原文98字 …]`）
    [轨迹]        step1..step4，其中 **三处** 完整贴了同一份错误代码（`finish({'answer': …})`）
    [第 4/12 步]

三处 = ① step2 那次真实交付 ② step3/③ step4 的「重复动作被跳过」——
后两处的载荷与 ① **逐字相同**（日志：`⏭ 打回之后重提的答案逐字相同`）。

五臂（判据仍是题集自带的 6 条 `tests`）
=====================================
  H 原样                                    正对照，必须复现 2/6 / 489
  F 只把「重复动作」两处的载荷换成 `<逐字相同，已省>`
  G 整段 `[轨迹]` 删掉
  I 只保留**第一次**交付、把三处载荷都换成占位（保留 step 行的意图文本）
  J 把三处载荷换成占位 + 保留 error 原文（若 F/G 都变对，用来分清"少贴代码"与"少了报错"）

⚠️ F/G/I/J 都是**派生 state**（从真实 state 里机械删改），不是内核能发出的那一份。
  它们只回答「哪一段在起作用」，**不**够格当"改了内核就会变好"的证据 ——
   要那一步，得改 `_state()` 再跑两臂全跑批。
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

#: `finish({'answer': '…'})` 的载荷 —— 只吃**被引号包住的整段**，不碰其它括号。
_FIN = re.compile(r"finish\(\{'answer': '.*?'\}\)", re.S)
_DUP = "重复动作被跳过："


def _nb(s: str) -> str:
    return f"<载荷 {len(s)} 字，与上一次逐字相同，已省>"


def v_orig(s: str) -> str:
    return s


def v_strip_dups(s: str) -> str:
    """F：只省掉「重复动作被跳过」那两处的载荷（保留第一次交付的原文）。"""
    out = []
    for seg in re.split(r"(?=%s)" % re.escape(_DUP), s):
        if seg.startswith(_DUP):
            seg = _FIN.sub(lambda m: f"finish({_nb(m.group(0))})", seg)
        out.append(seg)
    return "".join(out)


def v_no_traj(s: str) -> str:
    """G：整段 `[轨迹] …` 删掉（到 `[第 N/M 步]` 之前）。"""
    i = s.find("[轨迹]")
    if i < 0:
        return s
    j = s.find("[第 ", i)
    return s[:i] + (s[j:] if j > 0 else "")


def v_all_placeholder_keep_err(s: str) -> str:
    """J：三处载荷**全**换占位（连第一次也不贴），其余一字不动。"""
    return _FIN.sub(lambda m: f"finish({_nb(m.group(0))})", s)


def v_no_error(s: str) -> str:
    """K：只删 `[工作记忆]` 里的**报错原文**，三处代码一字不动。

    存在的意义：F/J/G 都在动"代码"，若它们全变对，仍分不清是"少贴代码"还是
    "少了那句 TypeError 提示"。K 把报错拿掉而代码全留 —— 两者分开。
    """
    return re.sub(r"\[工具失败\][^｜]*", "[工具失败]", s)


STATES = {
    "H_orig": v_orig,
    "F_strip_dups": v_strip_dups,
    "G_no_traj": v_no_traj,
    "J_all_placeholder": v_all_placeholder_keep_err,
    "K_no_error": v_no_error,
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
      f"判据={len(TASK['tests'])} 条 tests  system=当前内核（{len(_ANSWER_SYS)} 字符）")
print(f"真实 state={len(REAL_STATE)} 字符；各派生 state 长度见下")
print("=" * 78)

res: dict[str, dict] = {}
for name, fn in STATES.items():
    st = fn(REAL_STATE)
    user = kernel_user(st)
    n_payload = len(_FIN.findall(st))
    print(f"\n--- {name}  state={len(st)}字符 user={len(user)}字符 "
          f"finish载荷处数={n_payload} ---")
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
    res[name] = {"per": per, "same": same, "state_chars": len(st),
                 "payloads": n_payload}
    print(f"  ⇒ 两次输出代码{'逐字相同' if same else '**不同**'}")

print("\n" + "=" * 78)
print("汇总")
print("=" * 78)
print(f"{'臂':20}{'分':>8}{'字符':>16}{'state':>8}{'载荷':>5}{'同码':>6}  首错")
for name, d in res.items():
    print(f"{name:20}{'/'.join(str(p['ok']) for p in d['per']):>8}"
          f"{'/'.join(str(p['chars']) for p in d['per']):>16}"
          f"{d['state_chars']:>8}{d['payloads']:>5}{str(d['same']):>6}  "
          f"{(d['per'][0]['first'][:40] if d['per'] else '(未跑)')}")
print("\nJSON:", json.dumps({k: {"ok": [p["ok"] for p in v["per"]],
                                "chars": [p["chars"] for p in v["per"]],
                                "state": v["state_chars"],
                                "payloads": v["payloads"],
                                "same": v["same"]}
                            for k, v in res.items()}, ensure_ascii=False))

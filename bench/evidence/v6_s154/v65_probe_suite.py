"""V65 探针：**整题集 × 两档 system**，判据用题集自带 `tests`。

为什么要这一步：单题消融已经定位到「不要任何解释」这一句（6/6 vs 4/6，
失败原文逐字相同），但那是 1 题 6 条断言。整题集才能回答**它值多少分**。

⛔ 不造尺子：断言就是 `bench/tasks/coding_small.jsonl` 里每题自带的 `tests`，
    与 bench 的 scorer 同源（都是逐条 assert 计数）；只是不走 `run.py` 的
    五段流水线（那会把内核也带进来，本轮要隔离的是 **system prompt**）。
⛔ 入子进程跑代码，不在探针进程里 exec。
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
MAXTOK = int(os.environ.get("PROBE_MAXTOK", "2048"))
MODEL = os.environ.get("PROBE_MODEL", "qwen2.5:7b")

_TASKS = [json.loads(x) for x in
          open("/Users/linshaojie/V1/bench/tasks/coding_small.jsonl", encoding="utf-8")
          if x.strip()]

_SYS = {
    # 现状：`run.py:146` 的 DIRECT_SYSTEM，逐字相同
    "direct": ("你是代码助手。只输出一个 python 代码块，不要任何解释、不要示例调用。"
               "函数名与签名必须与题目要求完全一致。"),
    # 只删掉「不要任何解释」这五个字，其余一字不动
    "no_noexp": ("你是代码助手。只输出一个 python 代码块，不要示例调用。"
                 "函数名与签名必须与题目要求完全一致。"),
}


def ask(sysmsg: str, prompt: str) -> tuple[str, float]:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": sysmsg},
                     {"role": "user", "content": prompt}],
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


def score(code: str, tests: list[str]) -> tuple[int, str]:
    ok, first = 0, ""
    for a in tests:
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


res: dict[str, dict] = {}
for variant, sysmsg in _SYS.items():
    print(f"\n{'=' * 74}\n变体 {variant}  model={MODEL} max_tokens={MAXTOK} T=0\n{'=' * 74}")
    per = []
    t_all = 0.0
    for t in _TASKS:
        txt, dt = ask(sysmsg, t["prompt"])
        t_all += dt
        code = extract(txt)
        ok, first = score(code, t["tests"])
        n = len(t["tests"])
        per.append({"id": t["id"], "ok": ok, "n": n, "sec": round(dt, 1),
                    "first": first, "chars": len(txt)})
        print(f"  {t['id']:24s} {ok}/{n}  {dt:5.1f}s  {len(txt):4d}字符"
              f"  {first[:48]}")
    tot = sum(x["ok"] for x in per)
    den = sum(x["n"] for x in per)
    res[variant] = {"per": per, "tot": tot, "den": den, "sec": round(t_all, 1)}
    print(f"  ⇒ 合计 {tot}/{den} = {tot / den:.4f}   墙钟 {t_all:.0f}s")

print(f"\n{'=' * 74}\n对比\n{'=' * 74}")
a, b = res["direct"], res["no_noexp"]
print(f"  direct   {a['tot']}/{a['den']} = {a['tot'] / a['den']:.4f}   {a['sec']}s")
print(f"  no_noexp {b['tot']}/{b['den']} = {b['tot'] / b['den']:.4f}   {b['sec']}s")
print(f"  Δ = {(b['tot'] - a['tot']) / a['den']:+.4f}")
print("  逐题差（no_noexp − direct）：")
for x, y in zip(a["per"], b["per"]):
    flag = "" if x["ok"] == y["ok"] else "  ← 变了"
    print(f"    {x['id']:24s} {x['ok']}/{x['n']} → {y['ok']}/{y['n']}{flag}")
print("\nJSON:", json.dumps(res, ensure_ascii=False))

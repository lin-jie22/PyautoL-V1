"""V65 第三刀：`_ANSWER_SYS` 两档 × 整题集（answer 模板，与 Planner.answer 同源）。

要回答：把它换成 v3（只留正向形态）之后，
  · t04 是不是真到 6/6（不是单次运气）；
  · **其余五题会不会掉**（V64 的教训：改善一题、弄坏另一题 ⇒ 净增益为 0）。

⛔ 判据 = 题集自带 `tests`；⛔ 不改仓库；⛔ 入子进程跑代码。
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
MAXTOK = int(os.environ.get("PROBE_MAXTOK", "1024"))

TASKS = [json.loads(x) for x in
         open("/Users/linshaojie/V1/bench/tasks/coding_small.jsonl", encoding="utf-8")
         if x.strip()]

SYS = {
    # `mw/planner.py:158` 原文
    "v0_orig": ("只输出最终答案本身：不要 JSON、不要解释、不要道歉、不要代码块围栏。"
                "如果是代码，直接输出代码原文。"),
    # 纯正向、且对代码 / 文字两类任务都成立（v3~v6 输出同一份 515 字符，取最通用的一档）
    "v4_pos_both": "直接输出最终答案：代码就给代码原文，文字就给正文。",
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
            first = (p.stderr.strip().splitlines() or ["?"])[-1][:90]
    return ok, first


res: dict[str, dict] = {}
for variant, sysmsg in SYS.items():
    print(f"\n{'=' * 72}\n变体 {variant}  model={MODEL} max_tokens={MAXTOK} T=0\n{'=' * 72}")
    per, tsum = [], 0.0
    for t in TASKS:
        # 与 Planner.answer 的真实模板一致（state 空 ⇒ 不加 FIDELITY）
        prompt = f"目标：{t['prompt']}\n请直接给出最终答案。"
        txt, dt = ask(sysmsg, prompt)
        tsum += dt
        code = extract(txt)
        ok, first = score(code, t["tests"])
        n = len(t["tests"])
        per.append({"id": t["id"], "ok": ok, "n": n, "sec": round(dt, 1), "chars": len(txt)})
        print(f"  {t['id']:24s} {ok}/{n}  {dt:5.1f}s  {len(txt):4d}字符  {first[:46]}")
    tot, den = sum(x["ok"] for x in per), sum(x["n"] for x in per)
    res[variant] = {"per": per, "tot": tot, "den": den, "sec": round(tsum, 1)}
    print(f"  ⇒ 合计 {tot}/{den} = {tot / den:.4f}   墙钟 {tsum:.0f}s")

a, b = res["v0_orig"], res["v3_shape_only"]
print(f"\n{'=' * 72}\n对比\n{'=' * 72}")
print(f"  v0_orig       {a['tot']}/{a['den']} = {a['tot'] / a['den']:.4f}  题级均分 "
      f"{sum(x['ok'] / x['n'] for x in a['per']) / len(a['per']):.4f}")
print(f"  v4_pos_both   {b['tot']}/{b['den']} = {b['tot'] / b['den']:.4f}  题级均分 "
      f"{sum(x['ok'] / x['n'] for x in b['per']) / len(b['per']):.4f}")
drops = [(x["id"], x["ok"], y["ok"]) for x, y in zip(a["per"], b["per"]) if y["ok"] < x["ok"]]
print(f"  掉分的题：{drops if drops else '无'}")
print("  逐题：")
for x, y in zip(a["per"], b["per"]):
    flag = "" if x["ok"] == y["ok"] else (" ← 升" if y["ok"] > x["ok"] else " ← **降**")
    print(f"    {x['id']:24s} {x['ok']}/{x['n']} → {y['ok']}/{y['n']}{flag}")
print("\nJSON:", json.dumps(res, ensure_ascii=False))

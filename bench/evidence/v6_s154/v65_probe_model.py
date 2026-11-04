"""V65 单题探针：`merge_ranges` 在几个模型上第一次就能拿几分？

为什么先做这个（而不是直接整跑批）：
  ① 整跑批 12 格 × 2 rep 在 14b 上可能要 25min，而这一题就能回答"能不能写对"；
  ② 纪律 12：先量单次成本。14b 若撞 `V1_MAX_WALL_S=300`，读数会被墙钟污染
     （"模型不行"与"时间不够"分不开）—— 那比不知道更糟。

⛔ 不入库（与 V64 的 `/tmp/v64_probe_T.py` 同性质）：这是前置检查，不是新尺子。
判据直接用**题集里已有的 6 条断言**（`bench/tasks/coding_small.jsonl` 的 `tests`），
不另造题、不另造 oracle。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

os.environ["no_proxy"] = "127.0.0.1"
os.environ["NO_PROXY"] = "127.0.0.1"
BASE = "http://127.0.0.1:11434/v1/chat/completions"

TASK = None
for line in open("/Users/linshaojie/V1/bench/tasks/coding_small.jsonl", encoding="utf-8"):
    t = json.loads(line)
    if t.get("id") == "t04_merge_ranges":
        TASK = t
if TASK is None:
    raise SystemExit("找不到 t04_merge_ranges")

import os as _o
_SIMPLE = "你是程序助手。只输出代码，不要解释。"
#: 与 `run.py:146` 的 DIRECT_SYSTEM **逐字相同**（受控对比只差这一项）
_DIRECT = ("你是代码助手。只输出一个 python 代码块，不要任何解释、不要示例调用。"
           "函数名与签名必须与题目要求完全一致。")
_V = _o.environ.get("PROBE_SYS", "simple")
_AB = {
    "direct": _DIRECT,
    "simple": _SIMPLE,
    # 消融 B：去掉「不要示例调用」
    "no_noex": "你是代码助手。只输出一个 python 代码块，不要任何解释。"
                "函数名与签名必须与题目要求完全一致。",
    # 消融 C：去掉「不要任何解释」
    "no_noexp": "你是代码助手。只输出一个 python 代码块，不要示例调用。"
                 "函数名与签名必须与题目要求完全一致。",
    # 消融 D：只留形态约束
    "shape_only": "你是代码助手。只输出一个 python 代码块。",
    # 消融 E：去掉「函数名与签名必须与题目要求完全一致」
    "no_sig": "你是代码助手。只输出一个 python 代码块，不要任何解释、不要示例调用。",
}
SYS = _AB.get(_V, _SIMPLE)
MAXTOK = int(_o.environ.get("PROBE_MAXTOK", "2048"))


def ask(model: str, temperature: float = 0.0) -> tuple[str, float]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": TASK["prompt"]}],
        "max_tokens": MAXTOK,
        "temperature": temperature,
        "stream": False,
    }).encode()
    req = urllib.request.Request(BASE, data=body,
                                headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode())
    return d["choices"][0]["message"]["content"], time.time() - t0


def extract(text: str) -> str:
    # Qwen3.x 可能带  thinking…<｜end▁of▁thinking｜>；取**最后一个** python 代码块
    text = re.sub(r"</?think>.*?</think>", "", text, flags=re.S)
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, flags=re.S)
    code = blocks[-1] if blocks else text
    return code.strip()


def score(code: str) -> tuple[int, str]:
    """在**子进程**里跑 6 条断言（⛔ 不 exec 在探针进程里）。"""
    tests = TASK["tests"]
    src = code + "\n\n" + "\n".join(
        f"_r.append(({i}, {a!r}))" for i, a in enumerate(tests))
    # 逐条 try 太吵：让它自然抛出，看第一条失败
    ok = 0
    first = ""
    for i, a in enumerate(tests):
        prog = code + f"\n\n{a}\nprint('OK')\n"
        p = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                           text=True, timeout=30)
        if p.returncode == 0:
            ok += 1
        elif not first:
            first = (p.stderr.strip().splitlines() or ["?"])[-1][:120]
    _ = src
    return ok, first


for model in sys.argv[1:]:
    print(f"\n{'=' * 70}\n模型 {model}  sys={os.environ.get('PROBE_SYS','simple')}  max_tokens={MAXTOK}\n{'=' * 70}")
    try:
        txt, dt = ask(model)
    except Exception as e:                      # noqa: BLE001
        print(f"  ❌ 调用失败 {type(e).__name__}: {e}")
        continue
    code = extract(txt)
    ok, first = score(code)
    print(f"  T=0 耗时 {dt:5.1f}s  输出 {len(txt)} 字符  通过 {ok}/6")
    if first:
        print(f"  首条失败: {first}")
    print("  代码前 6 行：")
    for ln in code.splitlines()[:6]:
        print("   | " + ln[:100])
    if ok < 6:
        print("  ⚠️ 没全过 ⇒ 记下这次的具体错法（是不是又是 tuple 不可变）")

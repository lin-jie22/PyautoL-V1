#!/usr/bin/env python3
"""V255 —— 只给题面，让模型自己写测试，看它能不能抓住**自己那份错答案**。

用法：
  python3 run_v255.py --dry   # 只打印计划与提示 sha，0 次模型调用
  python3 run_v255.py         # 真跑：8 次生成 + 24 次本地执行

⛔ 预注册见 PREREG_v255.md（跑前落盘）。不写记忆、不写账本、不改生产代码、不写 state。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw.reflect import bare_code                       # noqa: E402

EV = ROOT / "bench/evidence/l19_s325"
SUITE = ROOT / "bench/evidence/l17_s323/suite_all39.jsonl"
ANSWERS = ROOT / "bench/evidence/l17_s323/answers_a.jsonl"
PRE = EV / "preflight_v255.json"

SYS = "你是测试工程师。只输出一个 python 代码块，不要任何解释。"
USER_TMPL = """题面：
{spec}

请只根据上面的题面，写一份自测脚本，用来检验一份实现是否满足题面要求。

格式要求：
- 输出一个 Python 模块，定义 `def check():`；函数体里用 assert 检查题面明确规定的行为与边界。
- 被测实现已保存在同目录的 `sol.py` 中，直接 `from sol import <题面要求的名字>`。
- 不读文件、不联网、不用 time / random。
- 测试脚本总行数不超过 60 行；挑最有代表性的用例，不要穷举。
- 只输出代码，不要解释。"""

EXEC_TIMEOUT = 15.0


def speed_gate() -> float:
    """三次测速取中位（纪律 116：<15 tok/s 不启动）。"""
    vals = []
    for i in range(3):
        body = json.dumps({"model": "qwen2.5:7b",
                           "prompt": "Count from 1 to 120, comma separated, nothing else.",
                           "stream": False,
                           "options": {"num_predict": 220, "temperature": 0}}).encode()
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as fh:
            d = json.load(fh)
        ec = int(d.get("eval_count") or 0)
        ed = float(d.get("eval_duration") or 0) / 1e9
        v = round(ec / ed, 2) if ed > 0 else 0.0
        vals.append(v)
        print(f"  speed#{i+1} {v:.2f} tok/s", flush=True)
    med = statistics.median(vals)
    print(f"  中位 {med:.2f}（门槛 15）")
    return med


def run_subject(tmp: pathlib.Path, test_src: str, sol_src: str) -> dict:
    """把 sol.py + selfcheck.py 放进独立目录跑一次，返回判决。"""
    (tmp / "sol.py").write_text(sol_src, encoding="utf-8")
    (tmp / "selfcheck.py").write_text(test_src, encoding="utf-8")
    try:
        p = subprocess.run([sys.executable, "-c", "import selfcheck; selfcheck.check()"],
                           cwd=str(tmp), capture_output=True, text=True, timeout=EXEC_TIMEOUT)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return {"rc": None, "verdict": "timeout", "err": "", "out": ""}
    tail = (err or "").strip().splitlines()
    return {"rc": rc, "verdict": "pass" if rc == 0 else "caught",
            "err": (tail[-1] if tail else "")[:300],
            "err_kind": (tail[-1].split(":")[0] if tail else "")[:60],
            "out": (out or "")[:200]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    pre = json.loads(PRE.read_text(encoding="utf-8"))
    d1 = list(pre["d1"])
    tasks = {json.loads(l)["id"]: json.loads(l)
             for l in SUITE.read_text(encoding="utf-8").splitlines() if l.strip()}
    ans: dict[str, str] = {}
    for line in ANSWERS.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("{"):
            continue
        r = json.loads(line)
        if r.get("task_id"):
            ans[r["task_id"]] = r.get("answer") or ""

    plan = {c: USER_TMPL.format(spec=tasks[c]["prompt"]) for c in d1}
    print(f"D1 = {len(d1)} 格：{d1}")
    for c in d1:
        print(f"  {c:24} prompt_sha="
              f"{hashlib.sha256((SYS + chr(0) + plan[c]).encode()).hexdigest()[:16]} "
              f"chars={len(plan[c])}")

    if args.dry:
        print("DRY OK（0 次模型调用）")
        return 0

    if speed_gate() < 15:
        print("NO_RUN: 速度闸未过（PREREG §4-1），不读数、不降门槛")
        return 5

    from mw.config import Settings
    from mw.llm import HTTPLLM, Message

    s = Settings()
    llm = HTTPLLM(s.llm)

    rows = []
    t0 = time.time()
    for i, c in enumerate(d1, 1):
        st = time.time()
        # ⚠️ PREREG §8：默认 2048 不够（首跑 g7s2 被截断）⇒ 6144 + 截断重试一次
        txt, err = "", ""
        for attempt in (1, 2):
            try:
                txt = llm.complete([Message("system", SYS), Message("user", plan[c])],
                                   max_tokens=2048, temperature=0.0)
                err = ""
                break
            except Exception as e:                       # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                if "截断" not in str(e):
                    break
                print(f"  ⚠️ {c} 第 {attempt} 次截断，重试", flush=True)
        dt = round(time.time() - st, 1)
        if not txt:
            rows.append({"cell": c, "secs": dt, "truncated": True, "err": err[:300],
                         "has_check": False, "verdicts": {}, "test_raw": ""})
            print(f"[{i}/{len(d1)}] {c:24} {dt:5.1f}s TRUNCATED {err[:80]}", flush=True)
            continue
        test_src = bare_code(txt)
        has_check = "def check(" in test_src
        # 被测对象 = V253 那份 A 臂答案（既有产物，本轮不再生成）
        sols = {"answer": bare_code(ans.get(c, ""))}
        verdicts = {}
        if has_check:
            for name, src in (("answer", sols["answer"]),
                              ("oracle", bare_code(tasks[c]["oracle"])),
                              ("partial", bare_code(tasks[c]["partial"]))):
                with tempfile.TemporaryDirectory(prefix=f"v255_{c}_") as td:
                    verdicts[name] = run_subject(pathlib.Path(td), test_src, src)
        rows.append({"cell": c, "secs": dt, "chars": len(txt), "truncated": False,
                     "sha": hashlib.sha256(txt.encode()).hexdigest()[:12],
                     "has_check": has_check, "test_raw": txt, "test_src": test_src,
                     "verdicts": verdicts})
        v = verdicts.get("answer", {})
        o = verdicts.get("oracle", {})
        print(f"[{i}/{len(d1)}] {c:24} {dt:5.1f}s chars={len(txt):4d} "
              f"check={'Y' if has_check else 'N'} "
              f"answer={v.get('verdict','-'):7} oracle={o.get('verdict','-'):7} "
              f"partial={verdicts.get('partial',{}).get('verdict','-')}", flush=True)

    print(f"总耗时 {time.time() - t0:.1f}s")
    out = {"model": s.llm.model, "temperature": 0.0, "d1": d1,
           "prompt_system": SYS, "prompt_user_tmpl": USER_TMPL, "rows": rows}
    (EV / "readings_v255.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"⇒ 写 {EV / 'readings_v255.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""V138 控制变异：往 mw/llm.py 末尾追加一行注释。

这行注释**永远不会执行**，但会改变文件字节 ⇒ `harness_sha`（mw/*.py 源码指纹）必变。

于是「这次轨迹里哪些函数变了」= **纯源码派生**的函数集合（SRC 集合）。
它们的变化不携带任何「代码跑没跑到」的信息，必须从「生效性」判据里剔除。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path("/tmp/v1_base")
EVID = Path(__file__).resolve().parent
W = Path("/tmp/v1_ctrl")
OUT = Path("/tmp/tr__control.json")


def main() -> int:
    if not (W / "mw").exists():
        subprocess.run(["/tmp/mkworker.sh", str(W)], check=True)
    for p in (BASE / "mw").glob("*.py"):
        shutil.copy2(p, W / "mw" / p.name)
    shutil.rmtree(W / "mw" / "__pycache__", ignore_errors=True)
    tgt = W / "mw" / "llm.py"
    tgt.write_text(tgt.read_text() + "\n# V138 控制：这行永不执行\n")

    bl = json.loads((EVID / "baseline.json").read_text())
    env = dict(os.environ, TRACE_NOISE=",".join(bl["noise"]), PYTHONDONTWRITEBYTECODE="1")
    pr = subprocess.run([sys.executable, str(EVID / "trace_run.py"), str(W), str(OUT)],
                        cwd=str(W), env=env, capture_output=True, text=True, timeout=900)
    d = json.loads(OUT.read_text())
    base = bl["base_per_fn"]
    noise = set(bl["noise"])
    src = sorted(k for k in d["per_fn"] if k not in noise and d["per_fn"][k] != base.get(k))
    print("判据:", "631/0" if pr.returncode == 0 else f"rc={pr.returncode}")
    print(f"SRC 集合（源码派生，{len(src)} 个）:")
    for k in src:
        print("   ", k)
    (EVID / "src_set.json").write_text(json.dumps(
        {"src": src, "verdict": pr.returncode, "calls": d["n_calls"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

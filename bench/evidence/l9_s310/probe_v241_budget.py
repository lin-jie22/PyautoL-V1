#!/usr/bin/env python3
"""V241 追加定向检查：2400 档到底有没有**触到**预算上限。

为什么必须做（纪律 72）：把参数从 900 调到 2400 不算实验，除非证明 2400 被用到过。
V241 三臂里 2400 档的 4B 一次截断都没有 —— 但那可能是「写完了」，也可能是
「120 s 客户端超时先到」。两种解释在现有读数上**同形**，必须单独拆开。

做法：同一份真实提示、同一模型、同一温度，`num_predict=2400`，但把**客户端超时**
放到 600 s（远大于 2400/20 ≈ 120 s）。这样唯一的约束就是预算本身。
- 若 `eval_count == 2400` 且 `done=length` ⇒ **预算仍是瓶颈**。
- 若 `eval_count < 2400` 且 `done=stop` ⇒ 预算够，**120 s 超时才是瓶颈**。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.request

D = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(D))
from speed_v241 import native, real_prompt, OLLAMA   # noqa: E402

N = 3          # 3 次：1 次不足以下结论（长度方差极大，V241 已证同提示一次成一次超时）


def main() -> int:
    prompt = real_prompt()
    print(f"提示 {len(prompt)} 字符；num_predict=2400；客户端超时放到 600 s")
    rows = []
    for k in range(N):
        t0 = time.time()
        try:
            r = native("Qwen3.5-4B", prompt, 2400, False)
        except Exception as e:                        # noqa: BLE001
            r = {"err": str(e)[:80], "wall_s": round(time.time() - t0, 1)}
        rows.append(r)
        print(f"  #{k+1} {json.dumps(r, ensure_ascii=False)}")
    ok = [r for r in rows if "eval_count" in r]
    hit = [r for r in ok if r["eval_count"] >= 2390]
    stop = [r for r in ok if r["eval_count"] < 2390]
    print(f"\n触到上限 {len(hit)}/{len(rows)}｜提前结束 {len(stop)}/{len(rows)}"
          f"｜失败 {len(rows)-len(ok)}/{len(rows)}")
    if hit:
        print("⇒ **预算仍是瓶颈**（2400 不够）")
    elif stop:
        ev = [r["eval_count"] for r in stop]
        print(f"⇒ 预算够（eval_count={ev}，全部提前结束）⇒ **120 s 超时才是瓶颈**")
    else:
        print("⇒ UNKNOWN：3 次全部失败，没有可用读数")
    (D / "probe_v241_budget.json").write_text(
        json.dumps({"prompt_chars": len(prompt), "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

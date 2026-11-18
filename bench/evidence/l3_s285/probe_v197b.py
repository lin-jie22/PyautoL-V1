#!/usr/bin/env python3
"""V197 probe-b —— 打回文本的**信息量曲线**（零模型）。

自踩：`probe_v197.py` 的 stub 叫 `_v197_stub`，于是 23 题**全部**只报「缺函数名」——
那测的是"函数名写错时说什么"，不是"实现错时说什么"。
本轮用**题面要求的真函数名**造三种失败，再跨 `verify_static = 0/1/2` 三档各跑一遍。

⛔ 不调模型、不改内核、不跑打分器。装置自报导入了谁。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
SUITE_DIR = ROOT / "bench" / "heldout"
EXTRA_SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"

import mw.verify as vfy                 # noqa: E402
from mw.tools import ToolResult         # noqa: E402

_HIDDEN_RE = re.compile(r"assert\s+[A-Za-z_][A-Za-z0-9_]*\s*\(")


def load_suites() -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for p in sorted(SUITE_DIR.glob("*.jsonl")) + [EXTRA_SUITE]:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        out[p.stem] = {str(r["id"]): r for r in rows}
    return out


def base_scores() -> dict[tuple[str, str], float]:
    d = json.loads((ROOT / "bench" / "evidence" / "l3_s282" / "readings_v194.json")
                   .read_text(encoding="utf-8"))
    return {(r["suite"], r["id"]): float(r["score"]) for r in d["rows"]}


#: 三种「函数名正确、实现有问题」的失败形态
STUBS = {
    "空返回": "def {fn}(*a, **k):\n    return None\n",
    "未定义名": "def {fn}(*a, **k):\n    return _v197_not_defined(a)\n",
    "语法错": "def {fn}(*a, **k)\n    return None\n",
}


def main() -> int:
    suites = load_suites()
    base = base_scores()
    pool = sorted(k for k, v in base.items() if v < 1.0)
    rows = []
    for suite, tid in pool:
        goal = str(suites[suite][tid].get("prompt") or "")
        # 函数名：优先用 derive 给出的 `defines` 判据（⛔ 不自己写正则猜）
        d0 = vfy.derive(goal, 2, 0)
        fn = next((c.arg for c in d0 if c.kind == "defines"), "")
        for stub_name, tpl in STUBS.items():
            code = tpl.format(fn=fn or "solution")
            for static in (0, 1, 2):
                checks = vfy.derive(goal, static, 0)
                v = vfy.run(code, checks)
                obs = ToolResult(ok=False, error=v.report).as_observation()
                rows.append({
                    "suite": suite, "id": tid, "fn": fn, "stub": stub_name,
                    "static": static, "ok": bool(v.ok),
                    "failed": list(v.failed), "report": str(v.report or ""),
                    "obs": obs, "obs_chars": len(obs),
                    "hidden_hits": len(_HIDDEN_RE.findall(obs)),
                })
    rd = {
        "deps": {"verify": vfy.__name__, "root": str(ROOT)},
        "rows": rows,
        "n": len(rows),
        "pool_n": len(pool),
        "functions_found": sum(1 for r in rows if r["fn"]) // (len(STUBS) * 3),
    }
    (OUT / "readings_v197_probe_b.json").write_text(
        json.dumps(rd, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"池 {len(pool)} 格；抽出函数名 {(rd['functions_found'])}；共 {len(rows)} 次闸")
    # 每种 (stub, static) 的观测：取长度中位与代表样本
    print(f"\n{'stub':10s} {'static':6s} {'过闸':6s} {'obs字数(集合)':18s} 代表样本")
    for stub in STUBS:
        for static in (0, 1, 2):
            g = [r for r in rows if r["stub"] == stub and r["static"] == static]
            passed = sum(1 for r in g if r["ok"])
            lens = sorted({r["obs_chars"] for r in g})
            # 代表样本：取字数最多的那条（信息量最大）
            rep = max(g, key=lambda r: r["obs_chars"])["obs"] if g else ""
            print(f"{stub:10s} {static:<6d} {passed}/{len(g):<4d} {str(lens[:6]):18s} {rep[:96]}")

    hid = sum(r["hidden_hits"] for r in rows)
    print(f"\n全部 {len(rows)} 次闸里，隐藏测试串命中：{hid}")
    # 正例对照
    from mw.worldmodel import _ERROR_CLASS_CHECK
    pos = "通过 2/5；失败: assert parse_headers('a: \"x;y\"') == {'a': 'x;y'} ⇒ ValueError"
    print(f"正例对照命中 {len(_HIDDEN_RE.findall(pos))}（必须 >0）；"
          f"类别映射泄题={any(_HIDDEN_RE.search(v) for v in _ERROR_CLASS_CHECK.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

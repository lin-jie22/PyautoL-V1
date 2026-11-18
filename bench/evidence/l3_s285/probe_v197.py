#!/usr/bin/env python3
"""V197 probe —— 零模型三层：诊断通道普查 + 打回文本实测 + 泄题检测器自检。

⛔ 不改内核、不调模型、不跑打分器。
装置自报导入了谁（纪律 65）：见下方 `deps` 打印。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))          # ⚠️ 显式插入，不靠 cwd

OUT = Path(__file__).resolve().parent
V196 = ROOT / "bench" / "evidence" / "l3_s284"
SUITE_DIR = ROOT / "bench" / "heldout"
EXTRA_SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"

import mw.verify as vfy                     # noqa: E402
from mw.tools import ToolResult             # noqa: E402

#: 隐藏测试串的判别式：`assert <名字>(` 与 `⇒ <异常>`。
#: ⛔ 不用编辑距离、不用子串模糊匹配（V197 口径：相等/包含两个谓词写清楚）。
_HIDDEN_RE = re.compile(r"assert\s+[A-Za-z_][A-Za-z0-9_]*\s*\(")


def load_suites() -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for p in sorted(SUITE_DIR.glob("*.jsonl")) + [EXTRA_SUITE]:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        out[p.stem] = {str(r["id"]): r for r in rows}   # ⚠️ stem，不是 name
    return out


def load_why() -> dict[tuple[str, str], str]:
    """V196 R 臂的 `why`（同池、同题、同模型的真诊断）。"""
    out: dict[tuple[str, str], str] = {}
    for l in (V196 / "cells_v196.jsonl").read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("arm") == "R":
            out[(r["suite"], r["id"])] = str(r.get("why") or "")
    return out


def load_baseline() -> dict[tuple[str, str], float]:
    d = json.loads((ROOT / "bench" / "evidence" / "l3_s282" / "readings_v194.json")
                   .read_text(encoding="utf-8"))
    return {(r["suite"], r["id"]): float(r["score"]) for r in d["rows"]}


#: 必失败实现：交付一定过不了 `nonempty`/`defines` 之外的任何判据，
#: 且**不含**任何隐藏测试（它的存在只是为了让闸有东西可拒）。
_STUB = "def _v197_stub(*a, **k):\n    return None\n"


def main() -> int:
    rd: dict = {"deps": {"verify": vfy.__name__, "import_path": str(ROOT)}}
    suites = load_suites()
    why = load_why()
    base = load_baseline()
    pool = sorted(k for k, v in base.items() if v < 1.0)
    rd["pool_n"] = len(pool)
    rd["pool"] = [list(k) for k in pool]

    # ── B1a：部署默认下，每题的判据种类（⛔ verify_static=2 / verify_exec=0）─────
    kinds_default: dict[str, int] = {}
    kinds_exec: dict[str, int] = {}
    per_task = []
    for suite, tid in pool:
        goal = str(suites[suite][tid].get("prompt") or "")
        c0 = vfy.derive(goal, 2, 0)      # 部署默认
        c1 = vfy.derive(goal, 2, 1)      # exec 打开（对照）
        for c in c0:
            kinds_default[c.kind] = kinds_default.get(c.kind, 0) + 1
        for c in c1:
            kinds_exec[c.kind] = kinds_exec.get(c.kind, 0) + 1
        per_task.append({
            "suite": suite, "id": tid,
            "n_default": len(c0), "kinds_default": [c.kind for c in c0],
            "n_exec": len(c1), "kinds_exec": [c.kind for c in c1],
            "ex_default": sum(1 for c in c0 if c.kind == "example"),
        })
    rd["B1a_kinds_default"] = kinds_default
    rd["B1a_kinds_exec"] = kinds_exec
    rd["B1a_per_task"] = per_task
    rd["B1a_tasks_with_example_default"] = sum(1 for r in per_task if r["ex_default"] > 0)
    rd["B1a_tasks_with_example_exec"] = sum(1 for r in per_task if "example" in r["kinds_exec"])

    # ── B1b：打回文本实测（真函数：vfy.run → ToolResult → as_observation）────
    hid = _HIDDEN_RE
    reject_rows = []
    for suite, tid in pool:
        goal = str(suites[suite][tid].get("prompt") or "")
        for tag, checks in (("default", vfy.derive(goal, 2, 0)),
                            ("exec_on", vfy.derive(goal, 2, 1))):
            v = vfy.run(_STUB, checks)
            obs = ToolResult(ok=False, error=v.report).as_observation()
            reject_rows.append({
                "suite": suite, "id": tid, "arm": tag,
                "ok": bool(v.ok), "failed": list(v.failed),
                "report": str(v.report or ""), "obs": obs,
                "obs_chars": len(obs),
                "hidden_hits": len(hid.findall(obs)),
                # 与**该格 R 臂 why** 的逐条 assert 前缀比对（真泄露面）
                "why_hits": sum(1 for m in hid.findall(why.get((suite, tid), ""))
                                if m in obs),
            })
    rd["B1b"] = reject_rows
    rd["B1b_obs_chars"] = sorted({r["obs_chars"] for r in reject_rows})
    rd["B1b_hidden_hits_total"] = sum(r["hidden_hits"] for r in reject_rows)
    rd["B1b_default_obs_sample"] = next(
        (r["obs"] for r in reject_rows if r["arm"] == "default"), "")

    # ── B2：检测器自检（正例对照 + 负例）────────────────────────────────
    samples = [w for w in why.values() if hid.search(w)]
    pos = samples[:1]
    rd["B2_detector"] = {
        "n_why_with_assert": len(samples),
        "positive_control_hits": sum(len(hid.findall(s)) for s in pos),
        # 负例：一段不含任何 `assert <name>(` 的普通文本
        "negative_control_hits": len(hid.findall("通过 4/6；失败了一条，没说是哪条")),
    }
    rd["B2_ok"] = rd["B2_detector"]["positive_control_hits"] > 0 and \
        rd["B2_detector"]["negative_control_hits"] == 0

    # ── B2b：真实注入路径再确认（reward_guidance 的输出长什么样）──────────
    from mw.worldmodel import _ERROR_CLASS_CHECK, _error_class   # noqa: E402
    cls_rows = []
    for s in samples[:8]:
        c = _error_class(s)
        cls_rows.append({"why_head": s[:60], "class": c,
                         "check": _ERROR_CLASS_CHECK.get(c, ""),
                         "check_has_assert": bool(hid.search(_ERROR_CLASS_CHECK.get(c, "")))})
    rd["B2b_class_map"] = cls_rows
    rd["B2b_class_never_leaks"] = not any(r["check_has_assert"] for r in cls_rows)

    (OUT / "readings_v197_probe.json").write_text(
        json.dumps(rd, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"deps: verify={vfy.__name__} root={ROOT}")
    print(f"池 {rd['pool_n']} 格")
    print(f"B1a 判据种类（部署默认 static=2 exec=0）: {kinds_default}")
    print(f"B1a 判据种类（exec=1 对照）           : {kinds_exec}")
    print(f"B1a 含 example 判据的题: 默认 {rd['B1a_tasks_with_example_default']}/{rd['pool_n']}"
          f"  exec开 {rd['B1a_tasks_with_example_exec']}/{rd['pool_n']}")
    print(f"B1b 打回观测长度集合: {rd['B1b_obs_chars']}")
    print(f"B1b 默认臂样本: {rd['B1b_default_obs_sample']!r}")
    print(f"B1b 隐藏测试串命中总数: {rd['B1b_hidden_hits_total']}  (含 why 前缀命中 "
          f"{sum(r['why_hits'] for r in rd['B1b'])})")
    print(f"B2 检测器: {rd['B2_detector']}  ⇒ ok={rd['B2_ok']}")
    print(f"B2b 类别映射是否泄题: never_leaks={rd['B2b_class_never_leaks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

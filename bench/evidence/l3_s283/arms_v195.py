#!/usr/bin/env python3
"""V195 —— ③ 多 agent 的第一格：中继（A 的输出当 B 的输入）断在哪一层。

四臂，全部不跑模型：
  A1 静态普查（AST）：`args["task"]` 的值构造点 / 覆写点 / schema 声明
  A2 物理管道（真 dispatch，两个 shell agent）：正例 + 负例
  A3 契约（真入口 + `_chat` 记录器）：5 种 payload，看拒答 / 进模型 / prompt 含不含上游
  A4 历史表达面（171 run 的 kernel_external）：calls / used_as_answer / err 残差

⛔ 本脚本不改内核一行，只读 + 建临时文件。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mw.tools import ToolRegistry, register_external_agents  # noqa: E402

READ: dict = {}


# ───────────────────────────────────────────────────────────────────────────
# A1 静态普查
# ───────────────────────────────────────────────────────────────────────────
def a1_static() -> dict:
    files = sorted(list((ROOT / "mw").glob("*.py"))) + [ROOT / "run.py"]
    task_dict_sites, arg_assigns, answer_task_calls, sub_input = [], [], [], []

    for f in files:
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        rel = f.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            # ① 字面量 {"task": ...}
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "task":
                        if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                                and v.func.id == "str"):
                            kind = "goal_value"      # str(goal)
                        elif isinstance(v, ast.Constant):
                            kind = "schema_or_doc"   # {"task": "交给该 agent 的任务"}
                        else:
                            kind = "other"
                        task_dict_sites.append(
                            {"file": rel, "line": node.lineno, "kind": kind,
                             "seg": ast.get_source_segment(src, node) or ""})
            # ② cand.args = ...
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Attribute)
                    and node.targets[0].attr == "args"):
                val = node.value
                fn = ""
                if isinstance(val, ast.Call) and isinstance(val.func, ast.Name):
                    fn = val.func.id
                arg_src = [ast.get_source_segment(src, a) for a in
                           (val.args if isinstance(val, ast.Call) else [])]
                arg_assigns.append({"file": rel, "line": node.lineno,
                                    "callee": fn, "args": arg_src,
                                    "seg": ast.get_source_segment(src, node) or ""})
            # ③ answer_task(...) 调用点
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "answer_task"):
                answer_task_calls.append(
                    {"file": rel, "line": node.lineno,
                     "args": [ast.get_source_segment(src, a) for a in node.args],
                     "seg": ast.get_source_segment(src, node) or ""})
            # ④ subprocess.run(..., input=...)  —— 物理通道
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "run"):
                for kw in node.keywords:
                    if kw.arg == "input":
                        sub_input.append({"file": rel, "line": node.lineno,
                                          "input": ast.get_source_segment(src, kw.value) or ""})

    # 覆写点的守卫条件：往上找最近的 If，取 test 源码
    guarded = []
    src_core = (ROOT / "mw/core.py").read_text(encoding="utf-8")
    tree_core = ast.parse(src_core)
    for node in ast.walk(tree_core):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Attribute)
                and node.targets[0].attr == "args"):
            continue
        for sub in ast.walk(tree_core):
            if not isinstance(sub, ast.If):
                continue
            body_lines = {n.lineno for n in ast.walk(sub) if hasattr(n, "lineno")}
            if node.lineno in body_lines:
                guarded.append({"line": node.lineno,
                                "guard": ast.get_source_segment(src_core, sub.test) or ""})
                break

    # 检测器自检：一个点都没找到 ⇒ 瞎了，不许把 0 当读数（假绿纪律 5）
    assert task_dict_sites, "A1 检测器哑火：一个 task 字面量都没找到"
    assert answer_task_calls, "A1 检测器哑火：连 answer_task 调用点都没找到"

    value_sites = [s for s in task_dict_sites if s["kind"] == "goal_value"]
    return {
        "task_dict_sites": task_dict_sites,
        "n_value": len(value_sites),
        "n_schema": sum(1 for s in task_dict_sites if s["kind"] == "schema_or_doc"),
        "n_other": sum(1 for s in task_dict_sites if s["kind"] == "other"),
        "arg_assigns": arg_assigns,
        "arg_assigns_guarded": guarded,
        "answer_task_calls": answer_task_calls,
        "subprocess_input": sub_input,
        "files_scanned": [f.relative_to(ROOT).as_posix() for f in files],
    }


# ───────────────────────────────────────────────────────────────────────────
# A2 物理管道：真 dispatch，两个 shell agent
# ───────────────────────────────────────────────────────────────────────────
def _mk_agent_scripts() -> tuple[Path, Path]:
    a = OUT / "relay_a.py"
    b = OUT / "relay_b.py"
    a.write_text(
        "import sys\n"
        "t = sys.stdin.read()\n"
        "print('A[' + t.strip().upper() + ']')\n", encoding="utf-8")
    b.write_text(
        "import sys\n"
        "t = sys.stdin.read()\n"
        "print('B[' + t.strip() + ']')\n", encoding="utf-8")
    return a, b


def relay_detector(task: str, priors: list[str], *, min_len: int = 8) -> list[int]:
    """口径（纪律 89 写清）：task 里**逐字包含**某个前序输出（长度 ≥ min_len）。

    ⛔ 不用编辑距离、不用子串跨边界之类的宽松口径 —— 只认相等子串。
    """
    hit = []
    for i, p in enumerate(priors):
        p = (p or "").strip()
        if len(p) >= min_len and p in (task or ""):
            hit.append(i)
    return hit


def a2_pipe() -> dict:
    a, b = _mk_agent_scripts()
    cfg = OUT / "agents_v195.json"
    cfg.write_text(json.dumps({"agents": {
        "a": {"type": "shell", "description": "大写器（上游）",
              "command": ["python3", str(a)], "timeout_s": 20},
        "b": {"type": "shell", "description": "包装器（下游）",
              "command": ["python3", str(b)], "timeout_s": 20},
    }}, ensure_ascii=False), encoding="utf-8")

    reg = ToolRegistry()
    added = register_external_agents(reg, cfg)

    goal = "把这句变成一个能通过检查的短语：V195 relay probe"
    r1 = reg.dispatch("agent:a", {"task": goal})
    up = r1.output.strip()
    # 正例：上游当 task
    r2 = reg.dispatch("agent:b", {"task": up})
    pos = relay_detector(r2.output, [up])
    # 负例：同样的下游、同样的上游在场，但 task 是 goal
    r3 = reg.dispatch("agent:b", {"task": goal})
    neg = relay_detector(r3.output, [up])
    # 对照：把上游塞进 goal 里（"保真的中继"）—— 下游仍应报中继
    r4 = reg.dispatch("agent:b", {"task": goal + "\n\n【上游】\n" + up})
    mid = relay_detector(r4.output, [up])

    assert r1.ok and r2.ok and r3.ok and r4.ok, (r1, r2, r3, r4)
    return {
        "registered": added,
        "upstream_out": up,
        "pos_B_out": r2.output.strip(), "pos_relay": pos,
        "neg_B_out": r3.output.strip(), "neg_relay": neg,
        "mid_B_out": r4.output.strip(), "mid_relay": mid,
        "detector_ok": bool(pos) and not neg and bool(mid),
    }


# ───────────────────────────────────────────────────────────────────────────
# A3 契约：真入口 + `_chat` 记录器
# ───────────────────────────────────────────────────────────────────────────
GOAL = """请实现一个函数，函数名必须是 `bump`，它把输入整数加一。
要求：只输出代码，不要解释。"""

UPSTREAM = """【上游产物】这是另一个 agent 交回来的东西：UPSTREAM_V195_MARKER
观察：目标函数适合用一行表达式实现。"""


def _load_code_agent():
    p = ROOT / "external/code_agent.py"
    spec = importlib.util.spec_from_file_location("code_agent_v195", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod, p


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, sys_p: str, user: str, **kw) -> str:
        self.calls.append({"sys": sys_p[:40], "user": user, "kw": dict(kw)})
        if int(kw.get("max_tokens") or 0) == 600:
            return json.dumps({"tests": ["assert bump(1) == 2"]})
        return "```python\ndef bump(x):\n    return x + 1\n```"


def a3_contract() -> dict:
    mod, path = _load_code_agent()
    rec = Recorder()
    mod._chat = rec                      # ⚠️ 被替换的是**模型调用**，不是被测边界

    payloads = {
        "V1_goal": GOAL,
        "V2_goal_then_upstream": GOAL + "\n\n" + UPSTREAM,
        "V3_upstream_then_goal": UPSTREAM + "\n\n" + GOAL,
        "V4_upstream_only": UPSTREAM,
        "V5_empty": "",
    }
    rows = []
    old_in, old_out = sys.stdin, sys.stdout
    try:
        for name, payload in payloads.items():
            rec.calls.clear()
            buf = io.StringIO()
            sys.stdin, sys.stdout = io.StringIO(payload), buf
            try:
                rc = mod.main()
            except BaseException as e:            # noqa: BLE001
                rc = f"EXC {type(e).__name__}: {e}"
            finally:
                sys.stdin, sys.stdout = old_in, old_out
            raw = buf.getvalue().strip()
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {}
            err = str(data.get("error") or "")
            if not raw:
                verdict = "NO_OUTPUT"
            elif err:
                verdict = "REFUSE" if ("没有『函数名必须是" in err or "stdin 为空" in err) else "ERROR"
            else:
                verdict = "PROCEED"
            prompts = [c["user"] for c in rec.calls]
            rows.append({
                "payload": name, "rc": rc, "verdict": verdict,
                "error": err[:120],
                "n_chat_calls": len(rec.calls),
                "prompt_has_upstream": any("UPSTREAM_V195_MARKER" in p for p in prompts),
                "prompt_chars": [len(p) for p in prompts],
            })
    finally:
        sys.stdin, sys.stdout = old_in, old_out

    sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return {"module_file": str(path), "module_sha16": sha,
            "loaded_file_matches": Path(mod.__file__).resolve() == path.resolve(),
            "rows": rows,
            "recorder_fired_on_V1": next(r["n_chat_calls"] for r in rows
                                          if r["payload"] == "V1_goal") > 0}


# ───────────────────────────────────────────────────────────────────────────
# A4 历史表达面
# ───────────────────────────────────────────────────────────────────────────
def a4_history() -> dict:
    p = ROOT / "bench_out/runs.jsonl"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    have = [r for r in rows if isinstance(r.get("kernel_external"), dict)]
    agg = {"calls": 0, "used_as_answer": 0, "err": 0, "ok": 0, "tasks": 0,
           "samples": 0, "fallback": 0, "skipped_no_budget": 0,
           "skipped_not_applicable": 0}
    per_run = []
    for r in have:
        e = r["kernel_external"]
        for k in agg:
            agg[k] += int(e.get(k) or 0)
        per_run.append({"run_id": r.get("run_id"), "calls": int(e.get("calls") or 0),
                        "used_as_answer": int(e.get("used_as_answer") or 0),
                        "err": int(e.get("err") or 0)})
    used_runs = [x for x in per_run if x["calls"] > 0]
    residual = agg["calls"] - agg["used_as_answer"] - agg["err"]
    return {
        "n_runs_total": len(rows), "n_runs_with_external_block": len(have),
        "n_runs_used_agent": len(used_runs),
        "sum": agg,
        "residual_calls_minus_answer_minus_err": residual,
        "max_calls_one_run": max([x["calls"] for x in per_run] or [0]),
        "top_runs": sorted(per_run, key=lambda x: -x["calls"])[:8],
    }


def main() -> int:
    READ["a1_static"] = a1_static()
    READ["a2_pipe"] = a2_pipe()
    READ["a3_contract"] = a3_contract()
    READ["a4_history"] = a4_history()
    (OUT / "readings_v195.json").write_text(
        json.dumps(READ, ensure_ascii=False, indent=1), encoding="utf-8")

    a1, a2, a3, a4 = (READ["a1_static"], READ["a2_pipe"],
                      READ["a3_contract"], READ["a4_history"])
    print("== A1 静态普查 ==")
    print(f"  task 字面量 值={a1['n_value']} schema={a1['n_schema']} 其他={a1['n_other']}")
    for s in a1["task_dict_sites"]:
        print(f"    {s['file']}:{s['line']} [{s['kind']}] {s['seg'][:60]}")
    print(f"  cand.args 赋值 {len(a1['arg_assigns'])} 处: "
          + ", ".join(f"{x['file']}:{x['line']}→{x['callee']}" for x in a1["arg_assigns"]))
    for g in a1["arg_assigns_guarded"]:
        print(f"    守卫 @ {g['line']}: if {g['guard']}")
    print(f"  answer_task() 调用 {len(a1['answer_task_calls'])} 处: "
          + "; ".join(f"{x['file']}:{x['line']} args={x['args']}"
                      for x in a1["answer_task_calls"]))
    print(f"  subprocess input= {len(a1['subprocess_input'])} 处: "
          + ", ".join(f"{x['file']}:{x['line']} {x['input']}" for x in a1["subprocess_input"]))
    print("== A2 物理管道 ==")
    print(f"  注册={a2['registered']} 上游输出={a2['upstream_out']!r}")
    print(f"  正例 relay={a2['pos_relay']} 负例 relay={a2['neg_relay']} "
          f"半保真 relay={a2['mid_relay']} detector_ok={a2['detector_ok']}")
    print("== A3 契约 ==")
    print(f"  模块={a3['module_file']} sha={a3['module_sha16']} "
          f"loaded_matches={a3['loaded_file_matches']} recorder_fired={a3['recorder_fired_on_V1']}")
    for r in a3["rows"]:
        print(f"    {r['payload']:24s} → {r['verdict']:9s} chat={r['n_chat_calls']} "
              f"prompt含上游={r['prompt_has_upstream']} err={r['error'][:40]}")
    print("== A4 历史 ==")
    print(f"  run 总数={a4['n_runs_total']} 带 kernel_external={a4['n_runs_with_external_block']} "
          f"用过 agent 的 run={a4['n_runs_used_agent']}")
    print(f"  汇总={a4['sum']}")
    print(f"  残差 calls-used_as_answer-err = {a4['residual_calls_minus_answer_minus_err']} "
          f"单 run 最大 calls={a4['max_calls_one_run']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

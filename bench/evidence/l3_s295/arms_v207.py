"""V207 装置：三臂蒸馏（单条 / 按错误类聚簇 / 按题聚簇）。

⛔ 不改 mw/：只 import 它的判定原语，不重写一套。
⛔ 读证据库只用 sqlite 只读连接（不 exec SCHEMA）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mw import abstractor as ab                     # noqa: E402
from mw.config import settings as _settings        # noqa: E402
from mw.llm import HTTPLLM, Message                # noqa: E402
from mw.worldmodel import _error_class             # noqa: E402

#: 题面里的函数名（用于判「是否点名某道题」）。从套件文件现读，不硬编码。
TASKS_DIR = Path(__file__).resolve().parents[2] / "tasks"


def task_func_names() -> set[str]:
    names: set[str] = set()
    for p in TASKS_DIR.glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            m = re.search(r"函数 ([A-Za-z_][A-Za-z0-9_]*)\(", str(t.get("prompt") or ""))
            if m:
                names.add(m.group(1))
    return names


def load_rewards(db: Path) -> list[dict]:
    """只读取出外部判分的 reward 条目（含失败的）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = []
    for _id, kind, content, meta in con.execute(
            "select id,kind,content,meta from episodes where kind='reward' order by id"):
        try:
            m = json.loads(meta or "{}")
        except Exception:                                  # noqa: BLE001
            m = {}
        out.append({"id": _id, "content": content or "", "meta": m,
                    "task_id": str(m.get("task_id") or ""),
                    "passed": bool(m.get("passed")),
                    "failure": str(m.get("failure") or ""),
                    "score": m.get("score")})
    con.close()
    return out


def judge(text: str, names: set[str]) -> str:
    """预注册 §4 的三分类。"""
    t = (text or "").strip()
    if not t or t.upper().startswith("NONE"):
        return "空话"
    if ab.looks_like_code(t):
        return "代码"
    has_name = any(n and re.search(rf"\b{re.escape(n)}\b", t) for n in names)
    # 具体输入字面量：括号包住的列表/元组字面量，或带引号的实参
    has_literal = bool(re.search(r"[\[\(]\s*['\"\d]", t)) or bool(
        re.search(r"==\s*[\[\(]", t))
    if has_name or has_literal:
        return "题级"
    return "跨题"


def run_arm(llm: HTTPLLM, units: list[tuple[str, str]], names: set[str],
            *, tag: str) -> dict:
    """units = [(单元名, 原料文本)]，每个单元一次 LLM 调用。"""
    outs = []
    for name, raw in units:
        try:
            r = llm.complete(
                [Message("system", ab._DISTILL_SYSTEM),
                 Message("user", f"执行记录（kind=reward，{name}）：\n{raw[:1200]}")],
                max_tokens=160, temperature=0.0)
            text = str(getattr(r, "content", r)).strip()
        except Exception as e:                              # noqa: BLE001
            outs.append({"unit": name, "error": f"{type(e).__name__}: {e}"})
            continue
        line = ab._to_one_line(text)
        gate = ("none" if not text or text.upper().startswith("NONE")
                else ("code" if ab.looks_like_code(text) else
                      ("ident" if ab._IDENTIFIER.search(text) else
                       ("short" if len(line) < ab.MIN_ABSTRACT_CHARS else "pass"))))
        outs.append({"unit": name, "raw": text[:400], "line": line,
                     "gate": gate, "cls": judge(text, names) if gate == "pass" else "-"})
    keep = [o for o in outs if o.get("gate") == "pass"]
    return {"tag": tag, "units": len(units), "calls": len(outs),
            "kept": len(keep),
            "cross": sum(1 for o in keep if o["cls"] == "跨题"),
            "task": sum(1 for o in keep if o["cls"] == "题级"),
            "filler": sum(1 for o in outs if o.get("gate") == "none"),
            "out": outs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, action="append",
                    help="证据库路径；可重复（多库合并）")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    s = _settings()
    if not s.llm.usable():
        print("❌ 需要真模型环境变量")
        return 2
    llm = HTTPLLM(s.llm)
    names = task_func_names()
    print(f"题面函数名 {len(names)} 个：{sorted(names)}")

    rw: list[dict] = []
    for d in a.db:
        part = load_rewards(Path(d))
        print(f"  库 {d}  reward={len(part)}")
        rw.extend(part)
    fails = [r for r in rw if not r["passed"] and r["failure"].strip()]
    print(f"reward 总={len(rw)}  其中失败且带原文={len(fails)}")
    if not fails:
        print("❌ 没有失败原料 ⇒ 阶段 2 无法进行")
        json.dump({"rewards": len(rw), "fails": 0}, open(a.out, "w"), ensure_ascii=False)
        return 3

    cls_cnt = Counter(_error_class(r["failure"]) for r in fails)
    print("错误类别分布：", dict(cls_cnt))

    # 臂 A：逐条
    units_a = [(f"id={r['id']}",
                f"题 {r['task_id']}：外部得分 {r['score']}，未通过，失败模式：{r['failure']}")
               for r in fails]
    # 臂 B：按错误类别
    by_cls: dict[str, list[dict]] = defaultdict(list)
    for r in fails:
        by_cls[_error_class(r["failure"])].append(r)
    units_b = [(f"簇={k}（{len(v)} 条，涉及 {len({x['task_id'] for x in v})} 道题）",
                "\n".join(f"- 题 {x['task_id']}：{x['failure'][:180]}" for x in v[:8]))
               for k, v in sorted(by_cls.items())]
    # 臂 C：按题
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in fails:
        by_task[r["task_id"]].append(r)
    units_c = [(f"题={k}（{len(v)} 条）",
                "\n".join(f"- {x['failure'][:180]}" for x in v[:8]))
               for k, v in sorted(by_task.items())]

    res = {"rewards": len(rw), "fails": len(fails),
           "error_classes": dict(cls_cnt),
           "task_spread": {k: len(v) for k, v in by_task.items()},
           "arms": {}}
    for tag, units in (("A_single", units_a), ("B_cluster_err", units_b),
                       ("C_cluster_task", units_c)):
        print(f"\n== 臂 {tag}  单元={len(units)}")
        arm = run_arm(llm, units, names, tag=tag)
        res["arms"][tag] = arm
        for o in arm["out"]:
            print(f"   [{o.get('gate','ERR')}/{o.get('cls','-')}] {o.get('line','')[:110]}")
    res["names"] = sorted(names)
    json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(f"\n落盘 {a.out}")
    for tag, arm in res["arms"].items():
        print(f"  {tag:16s} 单元={arm['units']:3d} kept={arm['kept']:3d} "
              f"跨题={arm['cross']} 题级={arm['task']} 空话={arm['filler']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

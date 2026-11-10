"""V79 前置判决：模型自写的期望值（自检断言）能不能咬住它自己的错答案。

⛔ 这不是跑批，是一个**决策探针**（同 V65 那个 10 秒单轮探针的用法）：
   在给 `derive()` 加第二个来源之前，先量「第二个来源造得出来吗、咬得住吗」。

装置：全部用现成件，不新建任何判据。
  · 错答案 = V78 跑批留在记忆库里的 `[finish]` 原文（`/tmp/v78/*/mem/memory.db`）
  · 正确答案 = 题集里现成的 `oracle`
  · 空实现 = `def <name>(*a, **k): return None`（用来抓「恒真断言」）
  · 跑断言 = `mw/verify.py: run_example()`（单一时真源，与内核同一把尺）

三个读数：
  bite_wrong   断言在**错答案**上失败 ⇒ 咬得住（越大越好）
  pass_oracle  断言在**正确答案**上通过 ⇒ 不误伤
  pass_stub    断言在**空实现**上通过 ⇒ 恒真（越大越糟）
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")
from mw.config import LLMConfig  # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402
from mw.verify import run_example  # noqa: E402

EV = Path("bench/evidence/v6_s168")
SYS = ("你是测试工程师。只输出 assert 语句，每行一条，不要解释，不要代码块标记。"
       "断言只依据目标里写明的行为。")
ASK = ("目标：{goal}\n\n"
       "请写出 2~3 条 assert 语句，用来检验一个实现是否正确。"
       "函数名与目标一致，只输出 assert 行。")

CODE_BLOCK = re.compile(r"```(?:python)?\s*(.*?)```", re.S)
FUNC = re.compile(r"def\s+([A-Za-z_]\w*)\s*\(")


def load_tasks(path: str) -> dict[str, dict]:
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            t = json.loads(line)
            out[t["id"]] = t
    return out


def answers_from_db(db: str) -> list[tuple[str, str]]:
    """按写入顺序把 (goal, code) 配对。

    ⚠️ 自踩（本脚本第一版）：`episodes.content` 会被截到 **1000 字符**
       （`k03` 正好 1000 ⇒ 半截代码 ⇒ 跑出 `SyntaxError`，被我读成"断言咬住了"）。
       完整原文在 `blobs`（`kind="observation"`，`chars` 是真实长度）。
       ⇒ 优先用 blob，只有 blob 缺这一条时才回退到 episode，并**标出来**。
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = list(con.execute(
        "select kind, content from episodes where kind in ('goal','finish') order by id"))
    blobs = [t for (t,) in con.execute(
        "select text from blobs where kind='observation' order by id")]
    con.close()
    by_fn: dict[str, str] = {}
    for text in blobs:
        m = CODE_BLOCK.search(text or "")
        name = FUNC.search(m.group(1)) if m else None
        if name:
            by_fn[name.group(1)] = m.group(1).strip()
    pairs: list[tuple[str, str, str]] = []
    goal = ""
    for kind, content in rows:
        if kind == "goal":
            goal = content
            continue
        body = content[len("done: "):] if content.startswith("done: ") else content
        m = CODE_BLOCK.search(body)
        code = (m.group(1) if m else body).strip()
        name = FUNC.search(code)
        src = "episode"
        if name and name.group(1) in by_fn:
            code, src = by_fn[name.group(1)], "blob"
        elif len(content) >= 1000:
            src = "episode(截断)"
        pairs.append((goal, code, src))
    return pairs


def asks(llm: HTTPLLM, goal: str) -> list[str]:
    txt = llm.complete([Message("system", SYS), Message("user", ASK.format(goal=goal))],
                       max_tokens=256) or ""
    m = CODE_BLOCK.search(txt)
    if m:
        txt = m.group(1)
    lines = [ln.strip() for ln in txt.splitlines()]
    return [ln for ln in lines if ln.startswith("assert ")]


def main() -> int:
    llm = HTTPLLM(LLMConfig())
    cases = [
        ("struct", "bench/heldout/struct_convert_heldout.jsonl", "/tmp/v78/struct/mem/memory.db"),
        ("g7j_n4", "bench/heldout/g7j_n4_heldout.jsonl", "/tmp/v78/g7j_n4/mem/memory.db"),
    ]
    rows = []
    for tag, suite_path, db in cases:
        tasks = load_tasks(suite_path)
        pairs = answers_from_db(db)
        for goal, code, src in pairs:
            name = FUNC.search(code)
            name = name.group(1) if name else ""
            task = next((t for t in tasks.values()
                         if name and FUNC.search(t.get("oracle") or "")
                         and FUNC.search(t["oracle"]).group(1) == name), None)
            if not task:
                print(f"⚠️ 配对失败：{tag} {name}")
                continue
            asserts = asks(llm, task["prompt"])
            stub = f"def {name}(*a, **k):\n    return None\n"
            r_wrong = run_example(code, "\n".join(asserts)) if asserts else "（没写出断言）"
            r_oracle = run_example(task["oracle"], "\n".join(asserts)) if asserts else "（没写出断言）"
            r_stub = run_example(stub, "\n".join(asserts)) if asserts else "（没写出断言）"
            rows.append({
                "suite": tag, "id": task["id"], "fn": name, "n_asserts": len(asserts),
                "src": src,
                "asserts": asserts,
                "bite_wrong": bool(asserts) and r_wrong != "",
                "pass_oracle": bool(asserts) and r_oracle == "",
                "pass_stub": bool(asserts) and r_stub == "",
                "why_wrong": r_wrong[:160], "why_oracle": r_oracle[:160],
                "why_stub": r_stub[:160],
            })
            r = rows[-1]
            print(f"{tag:8s} {task['id']:22s} n={r['n_asserts']} "
                  f"咬错={'✅' if r['bite_wrong'] else '❌'} "
                  f"过正确={'✅' if r['pass_oracle'] else '❌'} "
                  f"过空实现={'⚠️' if r['pass_stub'] else '✅'}")

    (EV / "probe_selfasserts.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    n = len(rows)
    bite = sum(1 for r in rows if r["bite_wrong"])
    ok = sum(1 for r in rows if r["pass_oracle"])
    stub = sum(1 for r in rows if r["pass_stub"])
    none = sum(1 for r in rows if r["n_asserts"] == 0)
    #: ⭐ 真正有判别力的只有这一条：**在正确答案上过、在错答案上不过**。
    #: 其余三种组合都不是"闸"：错-错 = 断言自己就是错的；过-过 = 恒真。
    disc = sum(1 for r in rows if r["bite_wrong"] and r["pass_oracle"])
    print(f"\n合计 {n} 题：写出断言 {n - none}、咬住错答案 {bite}、"
          f"在正确答案上通过 {ok}、在空实现上也通过 {stub}")
    print(f"⭐⭐ 有判别力（过正确 + 咬错） = {disc} / {n}")
    for tag in ("struct", "g7j_n4"):
        sub = [r for r in rows if r["suite"] == tag]
        print(f"  {tag:8s} n={len(sub)} 咬住={sum(1 for r in sub if r['bite_wrong'])} "
              f"过正确={sum(1 for r in sub if r['pass_oracle'])} "
              f"过空实现={sum(1 for r in sub if r['pass_stub'])} "
              f"有判别力={sum(1 for r in sub if r['bite_wrong'] and r['pass_oracle'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

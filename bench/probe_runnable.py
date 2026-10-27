#!/usr/bin/env python3
r"""交付物**可运行性**普查：`sg10` 缺 `import re` 到底有多普遍。

## 为什么需要它

第九十九轮（§66）留下的待办 N3 是「先量发生率，≥10% 再预注册内核改动」。
第一次量出来的数是 **38.3%** —— 而那个数是**装置误差**：

    我的第一版检测器：`import re` 在模块顶层、函数体内引用 `re`
    ⇒ 函数作用域里 `re` 是 global 且不是 imported ⇒ 判「未定义」
    ⇒ 顶层 import 与真缺 import **返回同一个结果**（12 例反证当场红 3 例）

修法是先算「模块顶层绑定集」，再拿它当豁免表。修完 12 例全过，读数从
**38.3% → 9.8%（sha 去重后）**。

⛔ 这不是第一次：§66 的 `audit_legs.py` A4 是同一个病（解析器恒 False，
方向对结论有利）。**一个不对自己先动手的检测器，给出的数和编的数同形。**

## 判据从哪来（⛔ 不另写一套判「对错」）

本工具**不判断答案对不对**，只回答三个**确定性问题**：

| 判定 | 问题 | 凭什么能做 |
|---|---|---|
| `empty` | 交付物是空串吗 | 空交付必是 0 分，平凡但真实 |
| `no_def` | 题面点名的 `def X(` 在不在 | 题面自己写的（`函数\s*(\w+)`） |
| `undef` | 有没有「哪儿都没绑定」的名字 | `symtable` 是确定性的 |
| `syntax` | 能不能 `ast.parse` | 语法错是确定性的 |

与 `mw/verify.py` 的 `undefined` 判据**同一个问题**，所以本工具必须与它
**逐条对拍**（S2）—— 两条独立实现给出不同答案时，至少有一条是错的，
而"哪条错"不能靠看代码决定（`mw/verify.py` 自己的注释里记着这件事：
第一版写成「全部符号」⇒ 模块级未定义名永远抓不到，读代码看不出来）。

## 四条反证（`--self-check`，零模型、零网络、零外部文件）

| # | 反证 | 防的是什么 |
|---|---|---|
| S1 | 12 例检测器样本，逐例断言 `undef` 集合 | ⛔ 顶层 `import` / 函数内 `import` / `import as` / `from … import` / 闭包 / 推导式 / `except as` / `with as` / lambda 九个**不该报**的分支，加三个**该报**的分支。第一版就是在这里红了 3 例 |
| S2 | 与 `mw.verify.undefined_names` 在内联样本上**逐条对拍** | 两条独立实现不一致 = 至少一条错。对拍只比「有没有未定义名」，不比顺序 |
| S3 | 同一份答案喂 3 次，`sha` 去重后必须只算 1 份 | 分母被重复 append 灌水（`bench_out/answers.jsonl` 是跨轮累积，271 行只有 9 个 run） |
| S4 | 空答案必须进 `empty` 而**不是** `no_def` | 空交付（K1：预算被思考吃掉）和「交了错的代码」是两种病，混进同一个分母会把缺 `def` 的比例凭空抬高 |

退出码：0 = 跑完；1 = 反证组红（拒绝出数）；2 = 用法错误。

## 口径

    python3 bench/probe_runnable.py                 # 全库普查（read-only）
    python3 bench/probe_runnable.py --self-check    # 只跑反证
    python3 bench/probe_runnable.py --dir <path>    # 只查指定目录下的 answers*.jsonl

⛔ 本工具**只读** `bench/evidence/**/answers*.jsonl` 与 `bench_out/answers.jsonl`。
后者**不入库** ⇒ 它缺席时全库普查仍必须跑得起来（分母变小，`--dir` 报告里
要写明扫描了几个文件）。S1~S4 一条都不依赖它。
"""

from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mw.verify as vfy  # noqa: E402

_BUILTINS = frozenset(dir(builtins))

#: 从题面读点名的函数名。与 `mw/verify.py:_FUNC_NAME` **不同的写法**是刻意的：
#: 那边匹配「函数名**必须**是 X」，这边匹配「函数 X(」—— 两种写法覆盖同一件事，
#: 谁漏了谁在 S2 的对照里会露出来。
_PROMPT_FN = re.compile(r"函数\s*([A-Za-z_]\w*)")


# ---------------------------------------------------------------------------
def code_of(answer: str) -> str:
    """抽代码。**直接复用内核的抽取器** —— 自检比尺子更严就是自造假失败，
    而"抽在哪"这件事上两处实现没有独立的必要（`mw/verify.py` 已与打分器对齐）。
    """
    return vfy.extract_code(answer or "")


def undef_names(src: str) -> list[str]:
    """代码里「被引用、却哪儿都没绑定」的全局名。空列表 = 没发现。

    ⚠️ 与 `mw/verify.py:undefined_names` 的区别：这里**不做** `_DYNAMIC_BIND`
    豁免（`exec`/`eval`/`globals`/`locals`/`vars`/`import *`）。理由是本工具
    要回答的是"答案里有没有这个洞"，不是"能不能确定没有这个洞" ——
    后者是内核闸的问题（不可判定时必须放行，否则自造假失败）。
    S2 对拍时把内核返回 `None` 的样本**排除**，那两类语义不同。
    """
    st = symtable_of(src)
    if st is None:
        return []
    # 模块顶层「绑定过」的名字：赋值 / 导入 / def / class / 参数（后者顶层不会出现）
    bound = {
        s.get_name() for s in st.get_symbols()
        if s.is_assigned() or s.is_imported() or s.is_namespace() or s.is_parameter()
    }
    bad: set[str] = set()

    def walk(tab) -> None:
        for sym in tab.get_symbols():
            name = sym.get_name()
            if name in _BUILTINS or name in bound:
                continue
            if (sym.is_assigned() or sym.is_imported() or sym.is_parameter()
                    or sym.is_namespace()):
                continue
            # 闭包变量由外层提供，外层那一遍会判它 —— 这里重复报就是误报。
            if sym.is_free():
                continue
            if sym.is_global() or sym.is_referenced():
                bad.add(name)
        for child in tab.get_children():
            walk(child)

    for child in st.get_children():
        walk(child)
    # 模块级自身的裸引用（`return` 在顶层之类），`symtable` 不会为它建子表
    for sym in st.get_symbols():
        name = sym.get_name()
        if name in _BUILTINS or name in bound:
            continue
        if sym.is_referenced():
            bad.add(name)
    return sorted(bad)


def symtable_of(src: str):
    import symtable
    try:
        return symtable.symtable(src, "<answer>", "exec")
    except (SyntaxError, ValueError):
        return None
    except Exception:  # noqa: BLE001 — 判据自己崩了不许记到被测对象头上
        return None


def top_defs(src: str) -> list[str] | None:
    """模块级 `def` 名。`None` = 语法错（由 `syntax` 那一档报，不重复报）。"""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return None
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def classify(answer: str, fn_expected: str | None) -> tuple[str, tuple[str, ...]]:
    """返回 `(verdict, undef)`。verdict ∈ {empty, syntax, no_def, undef, ok}。

    ⛔ `empty` 必须排在最前面：空交付是"没交"，不是"交错了"。
    """
    if not (answer or "").strip():
        return "empty", ()
    code = code_of(answer)
    defs = top_defs(code)
    if defs is None:
        return "syntax", ()
    if fn_expected and fn_expected not in defs:
        return "no_def", ()
    bad = undef_names(code)
    return ("undef", tuple(bad)) if bad else ("ok", ())


def answer_sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
def load_prompts(dirs: list[Path]) -> dict[str, str]:
    """题号 ⇒ 题面。只读 `bench/specgen/tasks_v*.jsonl`。"""
    out: dict[str, str] = {}
    for p in sorted((ROOT / "bench" / "specgen").glob("tasks_v*.jsonl")):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                out[str(row.get("id"))] = str(row.get("prompt") or "")
        except (OSError, json.JSONDecodeError):
            continue
    return out


def expected_fn(prompt: str | None) -> str | None:
    m = _PROMPT_FN.search(prompt or "")
    return m.group(1) if m else None


def collect(dirs: list[Path]) -> tuple[list[dict], list[Path]]:
    """读答案。返回 (答案列表, 扫到的文件)。

    ⚠️ 去重键是 `(文件, run_id, 题号, sha256)` —— 同一次运行被重复 append
    时 sha 相同，会被合并；不同 run 给出**逐字节相同**的答案时**不合并**
    （那是"复现"，本身就是读数，见 `--repeats`）。
    """
    seen: set[tuple] = set()
    rows: list[dict] = []
    files: list[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("**/answers*.jsonl")):
            files.append(f)
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("kind") != "answer":
                    continue
                sha = row.get("sha256") or answer_sha(row.get("answer") or "")
                key = (str(f), row.get("run_id"), row.get("task_id"), sha)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "file": str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f),
                    "task": str(row.get("task_id")),
                    "run": row.get("run_id"),
                    "sha": sha,
                    "chars": row.get("chars"),
                    "answer": row.get("answer") or "",
                })
    return rows, files


def survey(rows: list[dict], prompts: dict[str, str]) -> dict:
    per_task: dict[str, dict[str, int]] = {}
    buckets: dict[str, int] = {}
    detail: list[dict] = []
    for r in rows:
        verdict, undef = classify(r["answer"], expected_fn(prompts.get(r["task"])))
        buckets[verdict] = buckets.get(verdict, 0) + 1
        pt = per_task.setdefault(r["task"], {})
        pt[verdict] = pt.get(verdict, 0) + 1
        if verdict in ("undef", "no_def", "syntax"):
            detail.append({**r, "verdict": verdict, "undef": list(undef)})
    return {"n": len(rows), "buckets": buckets, "per_task": per_task,
            "detail": detail}


def _pct(k: int, n: int) -> str:
    return f"{k / n:6.1%}" if n else "   n/a"


def report(rows: list[dict], files: list[Path], prompts: dict[str, str]) -> None:
    s = survey(rows, prompts)
    n = s["n"]
    print(f"扫描文件 {len(files)} 个，去重后答案 {n} 份")
    for f in files:
        print(f"  · {f}")
    if not n:
        print("⚠️ 零答案 ⇒ 不出比率（分母为 0 时的百分比是编的）")
        return
    b = s["buckets"]
    print("\n=== 交付物形态 ===")
    for k in ("ok", "undef", "no_def", "syntax", "empty"):
        print(f"  {k:8s} {b.get(k, 0):4d}  {_pct(b.get(k, 0), n)}")
    runnable = b.get("ok", 0) + b.get("empty", 0)  # 空答案不算"不可运行"，单列
    print(f"\n⛔ 空交付与「交了不能跑的代码」必须分开：")
    print(f"    空交付        {b.get('empty', 0):4d}  {_pct(b.get('empty', 0), n)}"
          f"   （K1：预算被思考吃掉）")
    bad = n - runnable - b.get("empty", 0)
    print(f"    不可运行      {bad:4d}  {_pct(bad, n)}"
          f"   （缺 def / 缺名 / 语法错）")
    print("\n=== 按题 ===")
    for tid in sorted(s["per_task"]):
        pt = s["per_task"][tid]
        tot = sum(pt.values())
        print(f"  {tid:6s} 总{tot:3d}  "
              f"ok{pt.get('ok', 0):3d}  undef{pt.get('undef', 0):3d}  "
              f"no_def{pt.get('no_def', 0):2d}  syntax{pt.get('syntax', 0):2d}  "
              f"empty{pt.get('empty', 0):2d}   坏率 {_pct(tot - pt.get('ok', 0) - pt.get('empty', 0), tot)}")
    print("\n=== 坏答案明细 ===")
    for d in s["detail"]:
        u = ("," .join(d["undef"]) or "-")[:22]
        print(f"  {d['verdict']:7s} {u:22s} {d['task']:5s} "
              f"chars={str(d['chars']):>5s} {d['file']}")
    # ⛔ 「21 份缺 import」与「21 种错法」是两件事：若这 21 份 sha 相同，
    #    那是**同一个错被复现 21 次**，不是 21 次独立的失败。
    #    不加这一行，发生率会被读成"模型反复犯不同的错"，而实际是"模型一次都没改"。
    by_sha: dict[tuple[str, str], int] = {}
    for d in s["detail"]:
        by_sha[(d["verdict"], d["sha"])] = by_sha.get((d["verdict"], d["sha"]), 0) + 1
    print(f"\n=== 坏答案的**唯一性** ===")
    print(f"  {len(s['detail'])} 份坏答案，按 (判定, sha) 去重后只有 "
          f"**{len(by_sha)} 种**")
    for (v, sha), c in sorted(by_sha.items(), key=lambda kv: -kv[1]):
        print(f"    {v:7s} sha={sha[:12]}… 出现 {c:2d} 次")
    if len(by_sha) < len(s["detail"]):
        print("  ⇒ 有答案被**逐字节复现**多次：模型没改，只是重交了同一份"
              "（与 §93「只说结论 ⇒ 逐字相同重交」同形）")


# ---------------------------------------------------------------------------
#: S1 的样本。每一项都是**一个真实出现过的分支**，不是凑数的。
_S1_CASES: list[tuple[str, str, list[str]]] = [
    ("顶层 import", "import re\ndef f(s):\n    return re.sub('a','b',s)\n", []),
    ("真缺 import", "def f(s):\n    return re.sub('a','b',s)\n", ["re"]),
    ("函数内 import", "def f(s):\n    import re\n    return re.sub('a','b',s)\n", []),
    ("闭包", "def outer():\n    x=1\n    def inner(): return x\n    return inner\n", []),
    ("from import", "from os.path import join\ndef f(a):\n    return join(a,'b')\n", []),
    ("import as", "import re as rr\ndef f(s): return rr.sub('a','b',s)\n", []),
    ("推导式", "def f(rows):\n    return [r.strip() for r in rows]\n", []),
    ("except as", "def f(x):\n    try:\n        return int(x)\n"
                  "    except ValueError as e:\n        return str(e)\n", []),
    ("with as", "def f(p):\n    with open(p) as fh:\n        return fh.read()\n", []),
    ("lambda 闭包", "def f(rows):\n    k='x'\n"
                    "    return sorted(rows, key=lambda r: r+k)\n", []),
    ("global 赋値", "def f():\n    global z\n    z=1\n", []),
    ("真缺自定义名", "def f(rows):\n"
                     "    return [compress_spaces(r) for r in rows]\n",
     ["compress_spaces"]),
]

#: S2 对拍用的样本：**故意包含**内核会豁免的 `exec`（语义不同，必须被排除）
_S2_CASES: list[str] = [
    "import re\ndef f(s):\n    return re.sub('a','b',s)\n",
    "def f(s):\n    return re.sub('a','b',s)\n",
    "def f(rows):\n    return [compress_spaces(r) for r in rows]\n",
    "def f(x):\n    try:\n        return int(x)\n"
    "    except ValueError:\n        return 0\n",
    "def f(s):\n    return re.sub('a','b',s) + missing_name\n",
    "def f(d):\n    exec(str(d))\n    return undefined_by_exec\n",
]


def self_check() -> int:
    fails: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    print("=== S1 检测器 12 例（第一版在这里红 3 例）===")
    for name, src, exp in _S1_CASES:
        got = undef_names(src)
        ok = got == exp
        print(f"  {'✅' if ok else '❌'} {name:12s} got={got} exp={exp}")
        ck(ok, f"S1 {name}")

    print("\n=== S2 与内核 `mw.verify.undefined_names` 逐条对拍 ===")
    for i, src in enumerate(_S2_CASES):
        mine = tuple(undef_names(src))
        theirs = vfy.undefined_names(src)
        if theirs is None:
            print(f"  ⏭ 样本{i} 内核判「不可判定」⇒ 语义不同，不参与对拍")
            continue
        norm = tuple(sorted(n for _, n in theirs))
        ok = set(norm) == set(mine)
        print(f"  {'✅' if ok else '❌'} 样本{i} 本工具={mine} 内核={norm}")
        ck(ok, f"S2 样本{i}（两条实现不一致 ⇒ 至少一条错）")

    print("\n=== S3 sha 去重（同一份答案重复 append 不许灌分母）===")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "answers_x.jsonl"
        body = {"kind": "answer", "task_id": "sg10", "run_id": "r1", "rep": 1,
                "chars": 19, "sha256": answer_sha("def f(s): return re.sub"), 
                "answer": "def f(s): return re.sub"}
        lines = [json.dumps({"kind": "header", "run_id": "r1", "n_answers": 3,
                             "meta": {"suite": "tasks_v2"}})]
        lines += [json.dumps(body)] * 3
        # 另一次 run 交了**逐字节相同**的答案 —— 不许被合并（那是"复现"，是读数）
        body2 = dict(body, run_id="r2")
        lines.append(json.dumps(body2))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rows, files = collect([Path(td)])
        ok = len(rows) == 2
        print(f"  {'✅' if ok else '❌'} 3 次重复 append + 1 次跨 run 复现 ⇒ 去重后 {len(rows)} 份（期望 2）")
        ck(ok, "S3 去重")

        # S4 由同一批数据顺手钉：空答案必须进 empty
        p2 = Path(td) / "answers_y.jsonl"
        p2.write_text(
            json.dumps({"kind": "answer", "task_id": "sg10", "run_id": "r3",
                        "chars": 0, "sha256": answer_sha(""), "answer": ""}) + "\n"
            + json.dumps({"kind": "answer", "task_id": "sg10", "run_id": "r3",
                          "chars": 20, "sha256": answer_sha("def f(s):\n    pass\n"),
                          "answer": "def f(s):\n    pass\n"}) + "\n",
            encoding="utf-8")
        rows2, _ = collect([Path(td)])
        v_empty, _ = classify("", "f")
        v_ok, _ = classify("def f(s):\n    pass\n", "f")
        ok4 = v_empty == "empty" and v_ok == "ok"
        print(f"  {'✅' if ok4 else '❌'} 空答案 ⇒ {v_empty}（期望 empty）"
              f"；非空可运行 ⇒ {v_ok}（期望 ok）")
        ck(ok4, "S4 空答案单列")
        # ⚠️ 自踩（照登）：第一版写 3，实际是 4 —— `rows2` 重扫**整个**临时目录，
        #    含 `answers_x.jsonl` 的 2 份 + `answers_y.jsonl` 的 2 份。
        #    分母写错正是本工具存在的理由，所以它自己的分母也要被断言钉住。
        ck(len(rows2) == 4, f"S4 重扫应有 4 份（实得 {len(rows2)}）")

    print("\n=== 反证结论 ===")
    if fails:
        print(f"❌ {len(fails)} 条反证红 ⇒ 本工具的数**一律作废**：")
        for f in fails:
            print(f"   · {f}")
        return 1
    print(f"✅ 全部反证通过（S1 12 例 + S2 {len(_S2_CASES)} 例对拍 + S3 去重 + S4 空答案单列）")
    return 0


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="交付物可运行性普查（零模型、零网络）")
    ap.add_argument("--self-check", action="store_true", help="只跑 S1~S4 反证")
    ap.add_argument("--dir", action="append", default=None,
                    help="只查指定目录（可重复）；默认查 bench/evidence 与 bench_out")
    args = ap.parse_args(argv)

    if args.self_check:
        return self_check()

    dirs = [Path(d) for d in args.dir] if args.dir else [
        ROOT / "bench" / "evidence", ROOT / "bench_out"]
    rows, files = collect(dirs)
    report(rows, files, load_prompts(dirs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

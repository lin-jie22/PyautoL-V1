"""结构约束判据：**不需要知道答案**的检查，能盖住多少格？（§47，第八十四轮）

===========================================================================
预注册（写在任何模型调用之前，2026-10-25）
===========================================================================

§46 的结论是「要判对错，先要知道对」—— 224 条模型自写的例子型断言里 0 条真判别力。
§46.9 把它收成一条硬约束：**判据不许要求模型先知道答案**。本轮直接检验这条约束下
还剩什么，以及它值多少格。

## 为什么是「结构约束」这一族

「输出里每行不超过 8 个字符」「每行以 `> ` 开头」「整体按文本降序」「不含任何大写字母」
—— 这些检查**只看输出**，不需要参考答案。它们不是新发明：题面里**字面写着**这些要求
（题面由 `bench/taskspec.py` 从 spec 渲染，`render` 字符串里就含有数字与前缀）。

⇒ 所以本轮的问题不是「能不能想出判据」，而是**「能不能从题面文字里把它们读出来」**。
   这是 §46 的 43 倍教训（模型强在照抄步骤）第一次被用在**判据**上，而不是用在**答案**上。

## 三（四）个来源，只差「怎么拿到算子列表」

| 臂 | 算子从哪来 | 约束怎么来 |
|---|---|---|
| **G**（天花板） | `meta.spec`（内核看不见） | `derive()` |
| **R**（正则） | 题面文字 → 正则 | `derive()` |
| **M→d**（模型转写） | 题面文字 → 模型给 ops | `derive()` |
| **M**（模型直答） | — | 模型自己给约束 |

⛔ `derive()` 三条臂**共用同一份代码**，这是刻意的：本轮要隔离的是「**读到什么**」，
   不是「怎么推」。`derive()` 是本轮唯一一处**人写的推理**，它自己是否可靠由
   `--self-check` 的 (c) 项独立证明（在 10 题的 oracle 输出上必须一条都不响）。

## 五条预测（先写后量）

- **P1** 正则臂在 10/10 题上复原出与 spec **逐条相同**的算子序列（数字与前缀都是字面量）。
- **P2** 模型臂（`M→d`）算子序列 ≥ 8/10 完全正确 —— 比 §46 的 1.3% 高一个量级。
- **P3** 模型直答（`M`）的约束 **≤ 6/10** 与 `G` 相同。理由不是"模型笨"，是有一处
  **必须推理**的地方：`sort(key=len)` 之后再 `truncate`，长度序被截断破坏 ⇒ 有序性
  **不再是**不变量。抄写能让人写出 `ops`，但写不出"这条不变量活不下来"。
- **P4** 因此 `M` 会在 **PASS 对**上误响（`sorted` 类误响），而 `R`/`M→d` 不会。
- **P5** 覆盖面：`G` 盖住 **113/164 格**，`R`/`M→d` 与 `G` 逐对相同；
  `M` 因为误响被折价，**净覆盖反而更低**。

## 边界（先声明，别事后补）

1. ⛔ **题面是我渲染的，所以我能解析它** —— 这是「尺子被做成内核的镜子」的第二个位置
   （§45.10 是第一个）。正则臂的高分**只证明**「信息在题面里」，**不证明**「真实题面也能解析」。
   真实题面不会用「① ② ③」+ 固定措辞。这条必须和读数一起引用。
2. ⛔ 本轮的「覆盖」是**上界**：响了 ≠ 改得对（同 §46.6）。
3. ⛔ 只测这一个模型（本地 qwen2.5:7b，T=0）。10 次调用，一次一题。
4. 约束集是**保守**推出来的：推不出来就写 `None`（例如 `sort by len` 之后的有序性）。
   保守 ⇒ 覆盖率是**下界**、误响是**下界**，两个方向都不是乐观偏差。

用法：
    python3 bench/probe_structcheck.py --self-check      # 零模型调用
    python3 bench/probe_structcheck.py --solve           # 10 次模型调用
    python3 bench/probe_structcheck.py --report          # 零模型，从已有 raw 重出
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))       # 直接 `python3 bench/x.py` 时也能 import bench.*
SPECGEN = ROOT / "bench/specgen/tasks_v2.jsonl"
OUT_DEFAULT = "bench/evidence/struct_s84"

#: 「空白」的口径与 `bench/taskspec.py::_WS` **必须一致**。不一致的代价是：
#: 判据与 oracle 在同一个字符上各说一套，而两边都"有理由"。
WS = " \t\n\r\x0b\x0c"

#: 约束的键与类型。`sorted` 只允许 `text_asc`/`text_desc`/`None` ——
#: 按长度排的有序性**不是**不变量（被 `truncate`/`keep_tail` 破坏），见 `derive()`。
KEYS = ("max_len", "prefix", "lower", "max_ws_run", "sorted", "no_ellipsis")


# ===========================================================================
# 层 1：算子（两种前端：spec 字典 / 题面文字）
# ===========================================================================


def rules_from_spec(spec: dict) -> list[dict]:
    """spec 的规则列表**原样**返回（只保留 derive() 用得到的字段）。"""
    return [dict(r) for r in spec["rules"]]


#: 题面每一行 → 一个算子。顺序有意义：`dedupe_adj` 必须排在 `dedupe` 前面。
#: ⛔ 每条 pattern 都**只匹配该 op 的 render 原文**，不许写成宽松的"关键字命中" ——
#:    宽松匹配会在题面换个说法时静默匹配到错的 op，而"静默"正是本文件要防的。
_TEXT_PATTERNS: list[tuple[str, str, str | None]] = [
    # (op, 正则, 捕获组名)
    ("strip", r"^去掉每行首尾的空白字符", None),
    ("lower", r"^每行统一转成小写", None),
    ("collapse", r"^把每行内部连续的空白压成一个空格", None),
    ("dedupe_adj", r"^去掉\*\*紧挨着\*\*的重复行", None),
    ("dedupe", r"去重，保留(最后一次|第一次)出现", "dedupe"),
    ("squeeze", r"^每行里连续 (\d+) 个以上的空白字符都换成一个「-」", "min_run"),
    ("sort_len_desc", r"按字符数降序排列", None),
    ("sort_len_asc", r"按字符数升序排列", None),
    ("sort_text_desc", r"按文本字典序降序排列", None),
    ("sort_text_asc", r"按文本字典序升序排列", None),
    ("truncate", r"^每行只保留前 (\d+) 个字符", "n"),
    ("keep_tail", r"^每行只保留\*\*末尾\*\*的 (\d+) 个字符", "n"),
    ("prefix", r"^每行前面加上固定前缀「(.*?)」", "text"),
]


def op_from_text(line: str) -> dict:
    """题面的一行 → `{"op": ..., ...}`。⛔ 匹配不到就抛，不许静默跳过。"""
    hits = [p for p in _TEXT_PATTERNS if re.search(p[1], line)]
    if not hits:
        raise ValueError(f"这行没匹配到任何算子（静默跳过会被当成“题面里没有”）：{line!r}")
    # `sort_*` 四个是同一个算子的四种参数，按最长匹配挑一个
    sorts = [h for h in hits if h[0].startswith("sort_")]
    if sorts:
        op, _, _ = sorts[0]
        key, _, desc = op[len("sort_"):].rpartition("_")
        return {"op": "sort", "key": key, "desc": desc == "desc"}
    if len(hits) > 1:
        raise ValueError(f"这行匹配到多个算子，口径不明：{[h[0] for h in hits]} @ {line!r}")
    op, pat, grp = hits[0]
    if grp is None:
        return {"op": op}
    m = re.search(pat, line)
    val = m.group(1)
    if grp == "n":
        return {"op": op, "n": int(val)}
    if grp == "min_run":
        return {"op": op, "min_run": int(val)}
    if grp == "dedupe":
        return {"op": op, "keep": "first" if val == "第一次" else "last"}
    return {"op": op, "text": val}


def rules_from_text(prompt: str) -> list[dict]:
    """题面 → 算子序列。只吃 `①` 开头的步骤行（第一行与末行不是步骤）。"""
    out: list[dict] = []
    for line in prompt.splitlines():
        s = line.strip()
        if not s or s[0] not in "①②③④⑤⑥⑦⑧":
            continue
        out.append(op_from_text(s[1:].strip()))
    if not out:
        raise ValueError("题面里一行步骤都没找到")
    return out


# ===========================================================================
# 层 2：算子序列 → 约束（本轮唯一一处「人写的推理」，由 self-check (c) 独立证明）
# ===========================================================================


def derive(rules: list[dict]) -> dict:
    """从算子序列推**对任意输入都成立**的输出结构约束。推不出来 ⇒ `None`。

    三个必须小心的地方（每一个都有反例，且都在 self-check 里钉住）：

    1. **长度**：`truncate(n)`/`keep_tail(n)` 设上限，之后的 `prefix` 把上限**加**上
       前缀长度；再之后的 `truncate` 取 `min`。⛔ 不是简单地把所有数字取最小。
    2. **有序**：只有 `sort(key="text")` 之后 **仅剩** `prefix`/`truncate` 时才是不变量
       （两者都保序）。`sort(key="len")` 之后的 `truncate` 会把长度序压平 ⇒ 不是不变量。
    3. **空白游程**：`collapse` ⇒ 最长 1；`squeeze(n)` ⇒ 最长 `n-1`。取**最后**一个
       这样的算子。`prefix` 可能在边界造出一个更长的游程，所以 `check()` 先剥前缀再判。
    """
    c: dict[str, Any] = {"max_len": None, "prefix": None, "lower": False,
                         "max_ws_run": None, "sorted": None, "no_ellipsis": False}
    L: int | None = None
    for r in rules:
        if r["op"] in ("truncate", "keep_tail"):
            n = int(r["n"])
            L = n if L is None else min(L, n)
        elif r["op"] == "prefix" and L is not None:
            L += len(r["text"])
    c["max_len"] = L

    pidx = [i for i, r in enumerate(rules) if r["op"] == "prefix"]
    if pidx:
        i = pidx[-1]
        text = rules[i]["text"]
        ok = True
        for r in rules[i + 1:]:
            if r["op"] == "keep_tail":
                ok = False                       # 切前面 ⇒ 前缀没了
            if r["op"] == "truncate" and int(r["n"]) < len(text):
                ok = False                       # 截得比前缀还短 ⇒ 前缀不完整
        if ok:
            c["prefix"] = text

    lidx = [i for i, r in enumerate(rules) if r["op"] == "lower"]
    if lidx and all(not any(ch.isupper() for ch in r["text"])
                    for r in rules[lidx[0] + 1:] if r["op"] == "prefix"):
        c["lower"] = True

    for r in rules:
        if r["op"] == "collapse":
            c["max_ws_run"] = 1
        elif r["op"] == "squeeze":
            c["max_ws_run"] = int(r["min_run"]) - 1

    # 「不加省略号」只写在 `truncate` 的 render 里 ⇒ 只有在 truncate 出现过时才可推。
    # ⛔ 无条件写 True 就是把我自己的知识塞进判据（`keep_tail` 题面里没有这句话）。
    c["no_ellipsis"] = any(r["op"] == "truncate" for r in rules)

    sidx = [i for i, r in enumerate(rules) if r["op"] == "sort"]
    if sidx:
        i = sidx[-1]
        s = rules[i]
        if s.get("key") == "text" and all(r["op"] in ("prefix", "truncate")
                                          for r in rules[i + 1:]):
            c["sorted"] = "text_desc" if s.get("desc") else "text_asc"
    return c


# ===========================================================================
# 层 3：约束 → 违反清单（只读输出，不读期望值）
# ===========================================================================


def check(c: dict, out: Any) -> list[str]:
    """返回违反的约束名。⛔ 这里**没有**、也不许有「期望输出」这个入参。"""
    if not isinstance(out, list):
        return ["TYPE"]
    if not all(isinstance(x, str) for x in out):
        return ["TYPE"]
    bad: list[str] = []
    p = c.get("prefix") or ""
    if p and not all(x.startswith(p) for x in out):
        bad.append("prefix")
    if c.get("max_len") is not None and any(len(x) > c["max_len"] for x in out):
        bad.append("max_len")
    if c.get("lower") and any(x != x.lower() for x in out):
        bad.append("lower")
    if c.get("max_ws_run") is not None:
        lim = int(c["max_ws_run"])
        for x in out:
            run = 0
            for ch in (x[len(p):] if p and x.startswith(p) else x):
                run = run + 1 if ch in WS else 0
                if run > lim:
                    bad.append("ws_run")
                    break
            if "ws_run" in bad:
                break
    if c.get("no_ellipsis") and any(("…" in x) or ("..." in x) for x in out):
        bad.append("ellipsis")
    s = c.get("sorted")
    if s in ("text_asc", "text_desc"):
        pairs_ok = (all(out[i] <= out[i + 1] for i in range(len(out) - 1)) if s == "text_asc"
                    else all(out[i] >= out[i + 1] for i in range(len(out) - 1)))
        if not pairs_ok:
            bad.append("sorted")
    elif s is not None:
        raise ValueError(f"sorted 只能是 text_asc/text_desc/None：{s!r}")
    return bad


# ===========================================================================
# 层 4：题集、用例、真值
# ===========================================================================


def load_specs(path: Path = SPECGEN) -> dict[str, dict]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {str(r["id"]): r for r in rows}


def cases_of(row: dict) -> list[list[str]]:
    """从题集自带的断言里机械抽出输入（`assert fn(INPUT) == EXPECT`）。"""
    out: list[list[str]] = []
    for t in row["tests"]:
        node = ast.parse(t).body[0]
        call = node.test.left
        out.append(ast.literal_eval(call.args[0]))
    return out


def _run(src: str, fn: str, rows: list[str]) -> Any:
    from bench.taskspec import run_source                            # noqa: PLC0415
    return run_source(src, fn, rows)


def eval_source(c: dict, src: str, fn: str, cases: list[list[str]]) -> dict:
    """在一个候选程序上跑全部 14 个用例。

    ⛔ **必须分两个族报**，混在一起会把结论做假（第一版就踩了）：
    - `exec`：程序崩了/返回类型不对 ⇒ 「必须返回 list[str]」这条约束。
      它与**约束集无关**（约束集为空也照样响）⇒ 不能算进"结构约束的覆盖"。
    - `struct`：`check()` 报出的结构违反。
    """
    ex = False
    bad: set[str] = set()
    for cs in cases:
        try:
            out = _run(src, fn, cs)
        except Exception:                                            # noqa: BLE001
            ex = True
            continue
        v = check(c, out)
        if "TYPE" in v:
            ex = True
        bad |= {x for x in v if x != "TYPE"}
    return {"exec": ex, "struct": sorted(bad)}


# ===========================================================================
# 层 5：模型臂
# ===========================================================================

_SYSTEM = "你是严谨的程序员。严格按要求输出 JSON，不要解释，不要输出 JSON 以外的任何字符。"

_MODEL_TMPL = """{prompt}

现在请你只根据上面的题目，回答两件事。

（一）把题目的处理步骤逐条抄成 JSON 数组 `ops`。每个元素是一个对象，`op` 只能取：
strip / lower / collapse / squeeze / dedupe / dedupe_adj / sort / truncate / keep_tail / prefix
带参数的写成：`{{"op":"truncate","n":8}}`、`{{"op":"prefix","text":"-> "}}`、
`{{"op":"squeeze","min_run":2}}`、`{{"op":"sort","key":"text","desc":true}}`、
`{{"op":"dedupe","keep":"last"}}`。顺序必须与题目的 ①②③ 完全一致。

（二）写出**对这个函数返回的任何输出都成立**的结构约束 `constraints`：
- `max_len`：输出里最长的一行最多多少个字符（整数或 null）
- `prefix`：如果每行都保证以某个固定字符串开头，写它（字符串或 null）
- `lower`：输出是否保证不含大写字母（true/false）
- `max_ws_run`：输出里允许出现的最长连续空白字符个数（整数或 null）
- `sorted`：输出整体是否保证按文本有序（"text_asc"/"text_desc"/null）
- `no_ellipsis`：输出是否保证不含省略号（true/false）

⛔ 只写**对任意输入都成立**的。某条你推不出来、或只在某些输入下成立，就写 null。

只输出一个 JSON 对象：
{{"ops": [...], "constraints": {{"max_len": null, "prefix": null, "lower": false, "max_ws_run": null, "sorted": null, "no_ellipsis": true}}}}"""


def parse_model_json(text: str) -> dict | None:
    """从回答里取出**第一个开括号所对应的完整 `{...}`**。坏掉就返回 None。

    ⛔ 第一版会在最外层 JSON 坏掉时**退而取内层的第一个合法片段**（比如 `{"op":"strip"}`），
       于是 `ops` 变成空列表、`constraints` 变成默认值 —— 那是**静默降级**：
       它把"模型写了非法 JSON"读成"模型说没有约束"。实测 10 次里踩了 2 次
       （sg03 的 `"prefix">`、sg08 的括号错位）。现在是 PARSE_FAIL，不许猜。
    """
    s = text or ""
    i = s.find("{")
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[i:j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def norm_ops(ops: Any) -> list[dict]:
    """把模型给的 ops 收敛成与 `rules_from_spec` 可比的形式（缺省字段补齐）。"""
    if not isinstance(ops, list):
        return []
    out: list[dict] = []
    for o in ops:
        if not isinstance(o, dict) or "op" not in o:
            continue
        op = str(o["op"])
        d: dict[str, Any] = {"op": op}
        if op in ("truncate", "keep_tail"):
            d["n"] = int(o.get("n", -1))
        elif op == "prefix":
            d["text"] = str(o.get("text", ""))
        elif op == "squeeze":
            d["min_run"] = int(o.get("min_run", -1))
        elif op == "sort":
            d["key"] = str(o.get("key", ""))
            d["desc"] = bool(o.get("desc", False))
        elif op == "dedupe":
            d["keep"] = str(o.get("keep", ""))
        out.append(d)
    return out


def norm_constraints(c: Any) -> dict:
    if not isinstance(c, dict):
        return {k: None for k in KEYS}
    d: dict[str, Any] = {}
    d["max_len"] = int(c["max_len"]) if isinstance(c.get("max_len"), int) else None
    d["prefix"] = c["prefix"] if isinstance(c.get("prefix"), str) and c["prefix"] else None
    d["lower"] = bool(c.get("lower"))
    d["max_ws_run"] = int(c["max_ws_run"]) if isinstance(c.get("max_ws_run"), int) else None
    d["sorted"] = c["sorted"] if c.get("sorted") in ("text_asc", "text_desc") else None
    d["no_ellipsis"] = bool(c.get("no_ellipsis"))
    return d


# ===========================================================================
# 层 6：评估
# ===========================================================================


#: 只保留 `render` 能表达的字段。`dedupe.keep` / `dedupe.fold` 刻意**不参与比较**：
#: 题面里 dedupe 那一句永远写「转成小写后相同的键」（fold 恒定 True），
#: 所以 fold 从文字上**不可复原**，拿它比就是在测一件文本里没有的事。
_OPS_KEEP = ("op", "n", "text", "min_run", "key", "desc")


def _proj(rule: dict) -> dict:
    d = {k: rule[k] for k in _OPS_KEEP if k in rule}
    if d.get("op") == "sort":
        d["key"] = str(d.get("key", ""))
        d["desc"] = bool(d.get("desc", False))     # spec 里升序是"缺省不写"，语义相同
    return d


def _cmp_ops(a: list[dict], b: list[dict]) -> tuple[bool, str]:
    a = [_proj(r) for r in a]
    b = [_proj(r) for r in b]
    if len(a) != len(b):
        return False, f"长度 {len(a)} vs {len(b)}"
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return False, f"第 {i + 1} 条 {x} vs {y}"
    return True, ""


def _cmp_c(c: dict, g: dict) -> tuple[bool, str]:
    diff = [k for k in KEYS if c.get(k) != g.get(k)]
    return (not diff), ",".join(f"{k}:{c.get(k)!r}≠{g.get(k)!r}" for k in diff)


def evaluate(csets: dict[str, dict[str, dict]], pairs: list[dict],
             cases: dict[str, list[list[str]]]) -> dict:
    """覆盖 / 误响。`csets[name][task_id] = 约束`。三套读数分别报。

    `mode`：`struct` 只看结构约束｜`exec` 只看崩/类型｜`union` 两者。
    ⛔ `exec` 与 `name` 无关（同一批程序），所以它只该被读一次，不许当成
       "某个抽取器赢了"的证据。
    """
    out: dict[str, dict] = {}
    for name, by_task in csets.items():
        per_mode = {m: dict(fired_fail=0, fired_pass=0, cover=0) for m in
                    ("struct", "exec", "union")}
        why: dict[str, list[str]] = {}
        for p in pairs:
            c = by_task[p["task_id"]]
            v = eval_source(c, p["code"], p["fn"], cases[p["task_id"]])
            hit = {"struct": bool(v["struct"]),
                   "exec": bool(v["exec"]),
                   "union": bool(v["struct"] or v["exec"])}
            if v["struct"] or v["exec"]:
                tags = list(v["struct"]) + (["EXEC"] if v["exec"] else [])
                why[f"{p['run_id'][:5]}/{p['task_id']}"] = tags
            for m, on in hit.items():
                if not on:
                    continue
                if p["true_state"] == "FAIL":
                    per_mode[m]["fired_fail"] += 1
                    per_mode[m]["cover"] += p["lost"]
                else:
                    per_mode[m]["fired_pass"] += 1
        n_fail = sum(1 for p in pairs if p["true_state"] == "FAIL")
        out[name] = dict(n_fail=n_fail, n_pass=len(pairs) - n_fail,
                         total=sum(p["lost"] for p in pairs),
                         modes=per_mode, why=why)
    return out


def report(src: dict[str, dict[str, Any]], pairs: list[dict],
           specs: dict[str, dict], cases: dict[str, list[list[str]]]) -> int:
    """`src[name][task_id] = {"rules": [...], "c": {...}}`"""
    print("=" * 92)
    print("一、算子序列还原（与 spec 逐条相同）")
    print("=" * 92)
    print(f"  {'来源':10s} 完全正确   差异")
    for name in src:
        good, bad = 0, []
        for tid in specs:
            ok, why = _cmp_ops(src[name][tid]["rules"],
                               rules_from_spec(specs[tid]["meta"]["spec"]))
            if ok:
                good += 1
            else:
                bad.append(f"{tid}({why})")
        print(f"  {name:10s} {good}/{len(specs)}     {'; '.join(bad) or '—'}")

    print()
    print("=" * 92)
    print("二、约束集（对任意输入都成立的那些）与 G 的对撞")
    print("=" * 92)
    g = {tid: src["G"][tid]["c"] for tid in specs}
    for name in src:
        good, bad = 0, []
        for tid in specs:
            ok, why = _cmp_c(src[name][tid]["c"], g[tid])
            if ok:
                good += 1
            else:
                bad.append(f"{tid}({why})")
        print(f"  {name:10s} {good}/{len(specs)} 与 G 相同")
        for b in bad:
            print(f"      {b}")

    print()
    print("=" * 92)
    print("二·半、约束集在 **oracle 输出** 上的误响（140 个 (题,用例)）")
    print("=" * 92)
    print("  这一栏是**判据自己是否健全**的直接读数：正确答案违反它自己的约束 ⇒ 这条约束推错了。")
    print("  ⛔ 它与下面第三节无关：一个判据可以「抓得住错」，同时「冤枉对的」。")
    for name in src:
        nv = nt = 0
        detail: list[str] = []
        for tid in specs:
            c = src[name][tid]["c"]
            fn = str(specs[tid]["meta"]["spec"]["fn"])
            hit = 0
            for cs in cases[tid]:
                nt += 1
                v = check(c, _run(specs[tid]["oracle"], fn, cs))
                if v:
                    hit += 1
                    if len(detail) < 6:
                        detail.append(f"{tid}:{','.join(v)}")
            nv += hit
        mark = "✅" if nv == 0 else "❌"
        print(f"  {mark} {name:10s} 冤枉 {nv}/{nt} 个用例   {'; '.join(detail) or ''}")

    print()
    print("=" * 92)
    print("三、在 30 对真实答案上的 2×2（覆盖 = 响了的 FAIL 对里一共丢了多少格）")
    print("=" * 92)
    csets = {n: {tid: src[n][tid]["c"] for tid in specs} for n in src}
    res = evaluate(csets, pairs, cases)
    print(f"  {'来源':8s} {'族':7s} {'真错响':>7s}/{'总':<4s} {'真对误响':>7s}/{'总':<4s} "
          f"{'覆盖格':>7s}/{'丢格':<5s} {'覆盖率':>7s} {'精度':>7s}")
    for name, r in res.items():
        for m, d in r["modes"].items():
            hit, fp = d["fired_fail"], d["fired_pass"]
            prec = hit / (hit + fp) if (hit + fp) else float("nan")
            print(f"  {name:8s} {m:7s} {hit:7d}/{r['n_fail']:<4d} "
                  f"{fp:7d}/{r['n_pass']:<4d} "
                  f"{d['cover']:7d}/{r['total']:<5d} {d['cover'] / max(r['total'], 1):7.1%} "
                  f"{prec:7.1%}")
        print()
    for name, r in res.items():
        print(f"  {name} 的响点：")
        for k, v in sorted(r["why"].items()):
            print(f"      {k:12s} {','.join(v)}")

    print()
    print("=" * 92)
    print("四、按**失分类别**拆（类别来自 bench/probe_error_taxonomy.py 的机械四分类）")
    print("=" * 92)
    print(f"  {'来源/族':14s} " + " ".join(f"{c:>13s}" for c in ("UNDEFINED", "SYNTAX",
                                                                "CRASH", "SEMANTIC")))
    counts: dict[str, dict[str, int]] = {}
    for name, r in res.items():
        for m in ("struct", "exec", "union"):
            cells: dict[str, list[int]] = {}
            for p in pairs:
                cls = str(p.get("class") or "?")
                d = cells.setdefault(cls, [0, 0])
                d[0] += p["lost"]
                tags = r["why"].get(f"{p['run_id'][:5]}/{p['task_id']}")
                on = tags and ((m == "union") or (("EXEC" in tags) == (m == "exec")))
                if on:
                    d[1] += p["lost"]
            counts[f"{name}/{m}"] = {k: v[1] for k, v in cells.items()}
            tot = {k: v[0] for k, v in cells.items()}
            print(f"  {name + '/' + m:14s} " + " ".join(
                f"{counts[f'{name}/{m}'].get(c, 0):3d}/{tot.get(c, 0):<3d}     "
                for c in ("UNDEFINED", "SYNTAX", "CRASH", "SEMANTIC")))
    return 0


# ===========================================================================
# 反证：仪器自己要被样本打中
# ===========================================================================

_C_OK = {"max_len": 12, "prefix": "> ", "lower": True, "max_ws_run": 1,
         "sorted": "text_asc", "no_ellipsis": True}
_GOOD_OUT = ["> a b c", "> abc"]                       # 全 lowercase ✓ 有序 ✓
_SAMPLES: list[tuple[str, Any, str]] = [
    ("合规输出", _GOOD_OUT, ""),
    ("长度超限", ["> a b ccccccc"], "max_len"),
    ("前缀缺失", ["a b c"], "prefix"),
    ("含大写", ["> ABC"], "lower"),
    ("双空格", ["> a  b"], "ws_run"),
    ("省略号", ["> a…"], "ellipsis"),
    ("未排序", ["> z", "> a"], "sorted"),
    ("不是列表", "x", "TYPE"),
    ("元素不是字符串", [1, 2], "TYPE"),
]


def self_check() -> int:
    bad = 0
    print("反证 A：`check()` 七类违反 + 两类类型错，各一组样本")
    for name, out, want in _SAMPLES:
        got = check(_C_OK, out)
        ok = (want in got) if want else (not got)
        bad += int(not ok)
        print(f"  {'✅' if ok else '❌'} {name:14s} 期望 {want or '无':10s} 实得 {got}")

    print()
    print("反证 B：`derive()` 在 10 题的 **oracle 输出**上必须一条都不响（不变量的唯一证明）")
    specs = load_specs()
    n_case = n_viol = 0
    for tid, row in specs.items():
        c = derive(rules_from_spec(row["meta"]["spec"]))
        fn = row["meta"]["spec"]["fn"]
        for cs in cases_of(row):
            n_case += 1
            v = check(c, _run(row["oracle"], fn, cs))
            if v:
                n_viol += 1
                print(f"  ❌ {tid} 在 oracle 上响了：{v}")
    ok = n_viol == 0
    bad += int(not ok)
    print(f"  {'✅' if ok else '❌'} {n_case} 个 (题, 用例) 上违反数 {n_viol}")

    print()
    print("反证 C：题面解析器对**改动过的题面**必须跟着变（否则它是死代码）")
    row = specs["sg01"]
    p0 = row["prompt"]
    base = derive(rules_from_text(p0))
    muts = [
        ("sg01：前 8 个字符 → 前 3 个", p0.replace("前 8 个字符", "前 3 个字符"),
         ("max_len", 3)),
        # ⭐ 这条打的是 derive() 的一个真分支：sg03 是 `prefix '> '` 之后 `truncate 6`，
        #    截得比前缀长 ⇒ 前缀仍是不变量；把前缀换成长度 7 的串，6 < 7 ⇒ 前缀不完整 ⇒ None。
        ("sg03：前缀「> 」→「>>>>>> 」（7 字符）",
         specs["sg03"]["prompt"].replace("每行前面加上固定前缀「> 」", "每行前面加上固定前缀「>>>>>> 」"),
         ("prefix", None)),
        ("sg07：连续 2 个以上 → 连续 5 个以上",
         specs["sg07"]["prompt"].replace("连续 2 个以上", "连续 5 个以上"),
         ("max_ws_run", 4)),
    ]
    for name, prompt, (key, want) in muts:
        try:
            c = derive(rules_from_text(prompt))
        except ValueError as e:
            print(f"  ❌ {name}: 解析失败 {e}")
            bad += 1
            continue
        got = c.get(key)
        ok = got == want
        bad += int(not ok)
        print(f"  {'✅' if ok else '❌'} {name:34s} {key}: {got!r}（期望 {want!r}）")
    # ⛔ 反向：解析不了的行必须**抛**，不许静默跳过（静默跳过 = 判据静默变弱）
    try:
        rules_from_text(p0.replace("去掉每行首尾的空白字符", "随便干点什么"))
        ok = False
    except ValueError:
        ok = True
    bad += int(not ok)
    print(f"  {'✅' if ok else '❌'} 认不出的步骤行 ⇒ 抛错（不静默跳过）")
    print(f"  （基线 sg01 max_len={base['max_len']}；解析出的算子 {[r['op'] for r in rules_from_text(p0)]}）")

    print()
    print("反证 D：模型 JSON 抽取器")
    cases_ = [
        ("围栏包着", '```json\n{"ops": []}\n```', True),
        ("前面有废话", '好的，如下：{"ops": [{"op":"strip"}]} 完', True),
        ("坏 JSON", '{"ops": [,]}', None),
        ("没有 JSON", '我不知道', None),
        # ⭐ 实测踩到的形态：外层坏、内层有合法片段 ⇒ 必须判「没解析到」
        ("外层坏·内层合法", '{"ops":[{"op":"strip"}],"c":{"max_len">6}}', None),
    ]
    for name, text, want in cases_:
        got = parse_model_json(text)
        ok = (bool(got) == bool(want)) if want is not None else (got is None)
        bad += int(not ok)
        print(f"  {'✅' if ok else '❌'} {name:12s} → {'解析到' if got else '没解析到'}")

    print()
    print("✅ 四组反证全部命中" if not bad else f"❌ {bad} 个分支没被命中")
    return 0 if not bad else 1


# ===========================================================================
# 主流程
# ===========================================================================


def solve(outdir: Path, model: str) -> int:
    from mw.config import LLMConfig                                 # noqa: PLC0415
    from mw.llm import HTTPLLM, Message                             # noqa: PLC0415

    specs = load_specs()
    outdir.mkdir(parents=True, exist_ok=True)
    raw = (outdir / "raw_model.jsonl").open("w", encoding="utf-8")
    cfg = LLMConfig(
        base_url=os.environ.get("V1_LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
        api_key=os.environ.get("V1_LLM_API_KEY", "ollama"),
        model=os.environ.get("V1_LLM_MODEL", model),
        temperature=0.0,
    )
    llm = HTTPLLM(cfg)
    print(f"模型 {cfg.model}  T={cfg.temperature}  {len(specs)} 次调用")
    for k, (tid, row) in enumerate(specs.items(), 1):
        text = llm.complete(
            [Message("system", _SYSTEM),
             Message("user", _MODEL_TMPL.format(prompt=row["prompt"]))],
            max_tokens=1200, temperature=0.0, timeout_s=240.0)
        obj = parse_model_json(text)
        rec = {"task_id": tid, "text": text, "ok": obj is not None}
        if obj:
            rec["ops"] = norm_ops(obj.get("ops"))
            rec["constraints"] = norm_constraints(obj.get("constraints"))
        raw.write(json.dumps(rec, ensure_ascii=False) + "\n")
        raw.flush()
        print(f"  [{k}/{len(specs)}] {tid} → "
              f"{'解析成功' if obj else '❌ PARSE_FAIL'} "
              f"{rec.get('ops')}", flush=True)
    raw.close()
    return run_report(outdir, Path("bench_out/answers.jsonl"), Path("bench_out/runs.jsonl"))


def run_report(outdir: Path, answers: Path, runs: Path) -> int:
    from bench.probe_selftest import load_pairs                      # noqa: PLC0415

    specs = load_specs()
    cases = {tid: cases_of(row) for tid, row in specs.items()}
    pairs = load_pairs(answers, runs)
    path = outdir / "raw_model.jsonl"
    if not path.exists():
        raise SystemExit(f"❌ 没有 {path}，先跑 --solve")
    model = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            # ⚠️ 用**当前**口径重解析原文，而不是读落盘时算好的 ops ——
            #    否则收紧解析器之后，旧落盘会继续用旧口径（§46 的 `--recompute` 同理）。
            r["obj"] = parse_model_json(str(r.get("text") or ""))
            model[str(r["task_id"])] = r
    n_fail = sum(1 for r in model.values() if r["obj"] is None)
    print(f"模型臂：{len(model)} 个任务，其中 **{n_fail} 个没产出合法 JSON**"
          f"（记为 PARSE_FAIL ⇒ 约束集为空，不按「模型说没有约束」糊过去）")

    src: dict[str, dict[str, Any]] = {n: {} for n in ("G", "R", "M→d", "M")}
    for tid, row in specs.items():
        gt_rules = rules_from_spec(row["meta"]["spec"])
        src["G"][tid] = {"rules": gt_rules, "c": derive(gt_rules)}
        try:
            txt_rules = rules_from_text(row["prompt"])
        except ValueError as e:                                      # noqa: F841
            txt_rules = []
        src["R"][tid] = {"rules": txt_rules, "c": derive(txt_rules)}
        m = model.get(tid) or {}
        obj = m.get("obj") or {}
        mops = norm_ops(obj.get("ops")) if obj else []
        src["M→d"][tid] = {"rules": mops, "c": derive(mops) if mops else {}}
        src["M"][tid] = {"rules": mops,
                         "c": norm_constraints(obj.get("constraints")) if obj
                         else {k: None for k in KEYS}}
    rc = report(src, pairs, specs, cases)
    payload = {"src": src, "pairs": [{k: p[k] for k in
                                      ("run_id", "task_id", "true_state", "lost", "class")}
                                     for p in pairs]}
    (outdir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  落盘 {outdir}/summary.json")
    return rc


def pin() -> int:
    """题面文字 → 算子 ⇒ 约束，必须与 spec → 算子 ⇒ 约束**逐题相同**（10/10）。

    这是本轮唯一一条**正面**主张的守卫：`R == G`。它零模型、零网络、<1s。
    ⛔ 为什么不做成契约断言（同 §46.10 的理由）：`probe_structcheck` 是**测量装置**，
       把测量装置挂进契约层，就是把「被测」与「测量」混在一起。
       它挂在这里、由 `run_tests.sh` 步骤 7i 跑，效果一样，边界更干净。
    """
    specs = load_specs()
    bad = 0
    print("钉住：题面文字 → 约束 必须与 spec → 约束 逐题相同")
    for tid, row in specs.items():
        g = derive(rules_from_spec(row["meta"]["spec"]))
        try:
            r = derive(rules_from_text(row["prompt"]))
        except ValueError as e:
            print(f"  ❌ {tid} 题面解析失败：{e}")
            bad += 1
            continue
        ok, why = _cmp_c(r, g)
        if not ok:
            print(f"  ❌ {tid} {why}")
            bad += 1
    print("  ✅ 10/10 相同" if not bad else f"  ❌ {bad} 题不一致")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="结构约束判据（§47，内核外预演）")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--solve", action="store_true", help="10 次模型调用")
    ap.add_argument("--report", action="store_true", help="从已有 raw_model.jsonl 重出")
    ap.add_argument("--pin", action="store_true",
                    help="题面→约束 必须与 spec→约束 相同（零模型，供收口脚本用）")
    ap.add_argument("--answers", default="bench_out/answers.jsonl")
    ap.add_argument("--runs", default="bench_out/runs.jsonl")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--model", default="qwen2.5:7b")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.pin:
        return pin()
    if a.solve:
        return solve(ROOT / a.out, a.model)
    if a.report:
        return run_report(ROOT / a.out, Path(a.answers), Path(a.runs))
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

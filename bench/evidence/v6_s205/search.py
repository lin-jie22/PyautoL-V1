#!/usr/bin/env python3
"""V116：把「输入级」做成唯一变量 —— 同一搜索器、同一模板集、唯一差 = 候选词的原料里有没有 test。

零模型、零网络。判分 = 现有 bench/scorers/code_exec.py（不造新尺子）。

§0 两臂的候选词表 S 与差集
§1 爬山（两臂同一模板集 T1~T4，同一预算）
§2 判决 W1~W6
§3 0.8 档的行为盆普查（W6：同分不同盆）
"""
from __future__ import annotations

import ast
import collections
import hashlib
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
ROOT = EV.parent.parent
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
sys.path.insert(0, str(EV / "v6_s198"))
import idiom_transfer as I        # noqa: E402  harvest / task_of

_TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WRAPS = ["lower", "strip", "strip().lower"]
MAX_ROUNDS = 6


# ---------------------------------------------------------------- §0 词表
def tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOK.findall(text or "") if len(t) <= 6}


def str_literals(code: str) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(code)):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            v = n.value.lower()
            if len(v) <= 6:
                out.add(v)
    return out


def wordset(tid: str, code: str, see_tests: bool) -> tuple[set[str], dict]:
    t = I.task_of(tid)
    src_code = str_literals(code)
    src_prompt = tokens(t.get("prompt", ""))
    src_tests = tokens(" ".join(t["tests"])) if see_tests else set()
    S = src_code | src_prompt | src_tests
    return S, dict(code=sorted(src_code), prompt=sorted(src_prompt),
                   tests=sorted(src_tests), n=len(S))


# ---------------------------------------------------------------- 模板集
def sites(code: str) -> dict:
    tree = ast.parse(code)
    nodes = list(ast.walk(tree))
    ret_c, ret_any, in_cont, cmp_name = [], [], [], []
    for i, n in enumerate(nodes):
        if isinstance(n, ast.Return) and n.value is not None:
            ret_any.append(i)
            if isinstance(n.value, ast.Constant):
                ret_c.append(i)
        if isinstance(n, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops):
            for j, c in enumerate(n.comparators):
                if isinstance(c, (ast.List, ast.Tuple)):
                    in_cont.append((i, j))
            if isinstance(n.left, ast.Name):
                cmp_name.append(i)
    return dict(ret_c=ret_c, ret_any=ret_any, in_cont=in_cont, cmp_name=cmp_name)


def _unparse(tree: ast.AST) -> str:
    return ast.unparse(ast.fix_missing_locations(tree))


def candidates(code: str, S: set[str]) -> list[tuple[str, str]]:
    """⇒ [(label, new_code)]。位置由 AST 决定（与题无关）；S 是唯一变量。"""
    st = sites(code)
    out: list[tuple[str, str]] = []

    def rebuild(mut):
        tree = ast.parse(code)
        nodes = list(ast.walk(tree))
        mut(nodes)
        return _unparse(tree)

    # T1 常量替换：return <Constant> → S ∪ {None, True, False}
    vals = [None, True, False] + sorted(S)
    for i in st["ret_c"]:
        cur = list(ast.walk(ast.parse(code)))[i].value.value
        for v in vals:
            if type(v) is type(cur) and v == cur:
                continue

            def mut(nodes, i=i, v=v):
                nodes[i].value = ast.Constant(value=v)
            try:
                out.append((f"T1:ret{i}:={v!r}", rebuild(mut)))
            except Exception:                      # noqa: BLE001
                pass

    # T2 容器插入：in [...] / in (...) 追加一个词
    for i, j in st["in_cont"]:
        for w in sorted(S):

            def mut(nodes, i=i, j=j, w=w):
                nodes[i].comparators[j].elts.append(ast.Constant(value=w))
            try:
                out.append((f"T2:cont{i}.{j}:+{w!r}", rebuild(mut)))
            except Exception:                      # noqa: BLE001
                pass

    # T3 返回值包装：return EXPR → (EXPR).<wrap>
    for i in st["ret_any"]:
        for w in WRAPS:

            def mut(nodes, i=i, w=w):
                inner = nodes[i].value
                expr = inner
                for part in w.split("."):
                    expr = ast.Call(func=ast.Attribute(value=expr, attr=part, ctx=ast.Load()),
                                    args=[], keywords=[])
                nodes[i].value = expr
            try:
                out.append((f"T3:ret{i}:{w}", rebuild(mut)))
            except Exception:                      # noqa: BLE001
                pass

    # T4 比较主语包装：Compare.left → (left).<wrap>
    for i in st["cmp_name"]:
        for w in WRAPS:

            def mut(nodes, i=i, w=w):
                expr = nodes[i].left
                for part in w.split("."):
                    expr = ast.Call(func=ast.Attribute(value=expr, attr=part, ctx=ast.Load()),
                                    args=[], keywords=[])
                nodes[i].left = expr
            try:
                out.append((f"T4:cmp{i}:{w}", rebuild(mut)))
            except Exception:                      # noqa: BLE001
                pass

    # 去重（同一份源码只判一次）
    seen, uniq = set(), []
    for lab, src in out:
        if src in seen or src == code:
            continue
        seen.add(src)
        uniq.append((lab, src))
    return uniq


# ---------------------------------------------------------------- 判分
_CACHE: dict[tuple[str, int], float | None] = {}


def score_of(tid: str, code: str) -> float | None:
    key = (tid, hash(code))
    if key in _CACHE:
        return _CACHE[key]
    t = I.task_of(tid)
    pt = {"id": t["id"], "prompt": t.get("prompt", ""), "tests": t["tests"]}
    payload = json.dumps({"task": pt, "answer": code}, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                       capture_output=True, text=True, timeout=120)
    s = None
    if p.stdout.strip():
        s = json.loads(p.stdout.strip().splitlines()[-1]).get("score")
    _CACHE[key] = s
    return s


def bitmap(tid: str, code: str) -> list:
    ns: dict = {}
    try:
        exec(compile(code, "<c>", "exec"), ns)
    except Exception as e:                          # noqa: BLE001
        return [f"COMPILE:{type(e).__name__}"] * len(I.task_of(tid)["tests"])
    res = []
    for ts in I.task_of(tid)["tests"]:
        try:
            exec(compile(ts, "<t>", "exec"), ns)
            res.append(True)
        except AssertionError:
            res.append(False)
        except Exception as e:                      # noqa: BLE001
            res.append(f"EXC:{type(e).__name__}")
    return res


def climb(tid: str, code: str, S: set[str]) -> dict:
    s0 = score_of(tid, code)
    cur, cur_s = code, s0
    trail = []
    exhausted = False
    for _ in range(MAX_ROUNDS):
        cands = candidates(cur, S)
        best_lab, best_src, best_s = None, None, cur_s
        for lab, src in cands:
            s = score_of(tid, src)
            if s is not None and s > best_s:
                best_lab, best_src, best_s = lab, src, s
        if best_src is None:
            exhausted = True
            n_cand = len(cands)
            trail.append(dict(n_cand=n_cand, move=None, s=cur_s))
            break
        trail.append(dict(n_cand=len(cands), move=best_lab, s=best_s))
        cur, cur_s = best_src, best_s
        if cur_s >= 1.0:
            break
    return dict(s0=s0, s1=cur_s, code=cur, trail=trail, exhausted=exhausted,
                steps=len(trail), bitmap=bitmap(tid, cur))


def main() -> None:
    corpus = I.harvest()
    g7e1 = sorted(corpus["g7e1_parse_flag"])
    g7d2 = sorted(corpus["g7d2_dedupe_keep"])
    out = dict(sets={}, arms={}, verdicts={})

    print("## §0 两臂的候选词表 S")
    for tid, hs in [("g7e1_parse_flag", g7e1), ("g7d2_dedupe_keep", g7d2)]:
        code = corpus[tid][hs[0]]
        so, do = wordset(tid, code, False)
        sn, dn = wordset(tid, code, True)
        out["sets"][tid] = dict(off=sorted(so), on=sorted(sn), delta=sorted(sn - so),
                                info=dict(off=do, on=dn))
        print(f"\n  **{tid}**（以 {hs[0]} 为样例）")
        print(f"    S_off ({len(so)}): {sorted(so)}")
        print(f"    S_on  ({len(sn)}): {sorted(sn)}")
        print(f"    **差集 S_on \\ S_off ({len(sn - so)})**: {sorted(sn - so)}")
        print(f"    来源：code {len(do['code'])}｜prompt {len(do['prompt'])}｜tests {len(do['tests'])}")

    print("\n## §1 爬山（两臂同一模板集 T1~T4、同一预算、同一语料）")
    for tid, hs in [("g7e1_parse_flag", g7e1), ("g7d2_dedupe_keep", g7d2)]:
        for arm in ("OFF", "ON"):
            print(f"\n  **{tid} × {arm}**")
            rows = []
            for h in hs:
                code = corpus[tid][h]
                S, _ = wordset(tid, code, arm == "ON")
                r = climb(tid, code, S)
                r["h"] = h
                rows.append(r)
                moves = " → ".join(f"{t['move'] or 'EXHAUSTED'}({t['s']})" for t in r["trail"])
                print(f"    {h}  {r['s0']} → **{r['s1']}**  步{r['steps']}  "
                      f"{'EXHAUSTED' if r['exhausted'] else 'HIT-1.0'}  {moves}")
                print(f"        bitmap {r['bitmap']}")
            out["arms"][f"{tid}::{arm}"] = rows

    print("\n## §2 判决")
    A = out["arms"]
    e_off = A["g7e1_parse_flag::OFF"]
    e_on = A["g7e1_parse_flag::ON"]
    d_off = A["g7d2_dedupe_keep::OFF"]
    d_on = A["g7d2_dedupe_keep::ON"]

    w1 = all(r["s1"] is not None and r["s1"] < 1.0 for r in e_off) and \
        all(r["s1"] == 0.8 for r in e_off)
    print(f"\n  **W1 OFF 臂 g7e1 全部 <1.0 且最优 =0.8**")
    print(f"    分数 {[r['s1'] for r in e_off]} ⇒ {'✅ 押中' if w1 else '⛔ 押破'}")

    w2 = all(r["s1"] == 1.0 for r in e_on)
    print(f"\n  **W2 ON 臂 g7e1 全部到 1.0**")
    print(f"    分数 {[r['s1'] for r in e_on]} ⇒ {'✅ 押中' if w2 else '⛔ 押破'}")

    delta = out["sets"]["g7e1_parse_flag"]["delta"]
    w3 = "on" in delta and "true" not in delta
    print(f"\n  **W3 `'on'` ∈ S_on\\S_off，且它是必要新增词；`'true'` 题面已给（不在差集）**")
    print(f"    差集 {delta}　⇒ {'✅ 押中' if w3 else '⛔ 押破'}")

    same = ([r["s1"] for r in d_off] == [r["s1"] for r in d_on])
    same_bm = ([r["bitmap"] for r in d_off] == [r["bitmap"] for r in d_on])
    print(f"\n  **W4 g7d2 两臂逐候选逐分数完全一致**")
    print(f"    OFF {[r['s1'] for r in d_off]}　ON {[r['s1'] for r in d_on]}　"
          f"位图一致 {same_bm} ⇒ {'✅ 押中' if (same and same_bm) else '⛔ 押破'}")

    n_ex = sum(1 for r in e_off if r["exhausted"])
    w5_text = n_ex >= 1 and all(r["exhausted"] for r in e_off if r["s1"] < 1.0)
    w5_all = all(r["exhausted"] for r in e_off)
    print(f"\n  **W5 OFF 臂能报 EXHAUSTED，且报在到达最优点之后**")
    print(f"    报 EXHAUSTED 的 {n_ex}/{len(e_off)}；没报的 {[r['h'] for r in e_off if not r['exhausted']]}"
          f"（它们本来就到 1.0）")
    print(f"    ⚠️ 两种口径：")
    print(f"      预注册文字「**能**报，且报在最优之后」⇒ {'✅ 押中' if w5_text else '⛔ 押破'}")
    print(f"      脚本实现 `all(exhausted)`（比预注册严）⇒ {'✅ 押中' if w5_all else '⛔ 押破'}")
    print(f"    ⛔ 如实登记：脚本口径比预注册文字严，两者结论不同。")

    # W6：⛔ 本轮 §2 **不判** 它 —— 爬山终点每道题只有 1 个盆，看不见「同分多盆」。
    #     （第一版这里按题混在一个 Counter 里统计，是错的：把 g7e1 和 g7d2 的位图算成了「同档两盆」。）
    #     W6 的判决移到 `basins.py`（同一候选生成器、同一打分器，逐一普查全候选）。
    print(f"\n  **W6 0.8 档至少 2 个不同的行为盆**")
    print(f"    ⛔ 本节不判：爬山终点每题只剩 1 个盆，看不见「同分多盆」。")
    print(f"    ⇒ 判决见 `basins.py` 的 §3 普查（同候选空间、同打分器）。")
    print(f"    （第一版在这里按题混算，把 g7e1 与 g7d2 的位图并成「同档两盆」，已删。）")

    (HERE / "search.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str),
                                      encoding="utf-8")
    print("\n  写出 search.json")


if __name__ == "__main__":
    main()

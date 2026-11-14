#!/usr/bin/env python3
"""V113：失败的形状 —— 能不能从 test 失败直接读出「该改什么」。

零模型、零网络、零子进程。输入 = V111 的 `behaviour.json`（26 实现 × 5 test 的实际输出）。

§0 失败格总表（实际 / 期望 / 最小编辑脚本 / 类型）
§1 类型分布（A 单字符替换 / B 首尾 / C 结构 / D 故障）
§2 Z1 覆盖率 / Z2 一次修好 / Z3 跨 test 一致性
"""
from __future__ import annotations

import ast
import collections
import difflib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
sys.path.insert(0, str(EV / "v6_s200"))
sys.path.insert(0, str(EV / "v6_s198"))
import behaviour as B      # noqa: E402  parse_test
import idiom_transfer as I  # noqa: E402  task_of


def edits_of(got: str, exp: str) -> list[tuple[str, str, str]]:
    """⇒ [(op, a, b)]，op ∈ replace/insert/delete，a/b 是原文片段。"""
    sm = difflib.SequenceMatcher(None, got, exp, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        op = {"replace": "replace", "insert": "insert", "delete": "delete"}[tag]
        out.append((op, got[i1:i2], exp[j1:j2]))
    return out


def classify(err: list, got: str, exp: str) -> str:
    if not err:
        return "equal"
    if not err:
        return "equal"
    if got.startswith("ERROR:"):
        return "D_error"
    # A 类：全部是 replace，且**去重后只有一个 (a,b) 对** ⇒ 一次全局 replace 就能修好这一格
    if all(op == "replace" for op, _a, _b in err):
        uniq = {(a, b) for op, a, b in err}
        if len(uniq) == 1:
            (a, b), = uniq
            if len(a) == 1 and len(b) == 1:
                return "A_sub"
    if len(err) == 1:
        op, _a, _b = err[0]
        if op in ("insert", "delete"):
            if not got or not exp:
                return "B_edge"
            if got.endswith(exp) or exp.endswith(got) or got.startswith(exp) or exp.startswith(got):
                return "B_edge"
    return "C_multi"


def literal(s: str):
    """指纹里的 str 是 `repr(...)` 的产物；还原成真值。ERROR:* 原样返回。"""
    if s.startswith("ERROR:") or s == "UNPARSED":
        return s
    try:
        return ast.literal_eval(s)
    except Exception:
        return s


def main() -> None:
    beh = json.loads((EV / "v6_s200" / "behaviour.json").read_text(encoding="utf-8"))
    by_task = collections.defaultdict(dict)
    for k, v in beh.items():
        tid, h = k.split("::")
        by_task[tid][h] = v

    rows = []
    print("## §0 失败格总表")
    for tid in sorted(by_task):
        task = I.task_of(tid)
        exps = [B.parse_test(str(x))[1] for x in task["tests"]]
        print(f"\n  **{tid}**（{len(task['tests'])} 条 test）")
        for h, d in sorted(by_task[tid].items()):
            for i, (got_s, exp) in enumerate(zip(d["fp"], exps)):
                got = literal(got_s)
                if isinstance(got, str) and got.startswith("ERROR:"):
                    rows.append(dict(tid=tid, h=h, i=i, got=got, exp=exp,
                                     err=[], kind="D_error", n_err=0))
                    print(f"    {h} t{i}　⛔ {got}")
                    continue
                ok = got == exp
                if ok:
                    continue
                if not isinstance(got, str) or not isinstance(exp, str):
                    # ⛔ 布尔/None/数字的「字符串 diff」是假的（`None` vs `False` 只会得到 replace('Non'→'Fals')）
                    rows.append(dict(tid=tid, h=h, i=i, got=repr(got), exp=repr(exp),
                                     err=[], kind="C_nonstr", n_err=0))
                    print(f"    {h} t{i}　{got!r} ≠ {exp!r}　C_nonstr（非字符串输出，diff 不适用）")
                    continue
                err = edits_of(got, exp)
                kind = classify(err, got, exp)
                rows.append(dict(tid=tid, h=h, i=i, got=str(got), exp=str(exp),
                                 err=[list(e) for e in err], kind=kind, n_err=len(err)))
                shown = "；".join(f"{o}({a!r}→{b!r})" for o, a, b in err[:3])
                print(f"    {h} t{i}　{got!r} ≠ {exp!r}　{kind}　{shown}")

    n_fail = len(rows)
    dist = collections.Counter(r["kind"] for r in rows)
    print(f"\n## §1 类型分布（分母 = 失败格 {n_fail}）")
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"  {k:14s} {v:3d}　{v / n_fail:.1%}")

    a = dist.get("A_sub", 0)
    print(f"\n## §2 判决")
    print(f"\n  **Z1 A 类 ≥ 50%**（分母 {n_fail}；A = 全部 replace 去重后只有一个单字符替换）")
    print(f"    A 类 {a}/{n_fail} = {a / n_fail:.1%}")
    print(f"    ⇒ {'⛔ 押破（≥50%：失败大多是一个字符的事）' if a / n_fail >= 0.5 else '✅ 押中（<50%）'}")

    # Z3：同一实现的多条失败格给出的 A 类动作是否一致
    print(f"\n  **Z3 A 类动作跨 test 一致性**")
    per_impl = collections.defaultdict(list)
    for r in rows:
        if r["kind"] == "A_sub":
            per_impl[(r["tid"], r["h"])].append(tuple(r["err"][0]))
    multi = {k: v for k, v in per_impl.items() if len(v) >= 2}
    conflict = {k: v for k, v in multi.items() if len(set(v)) > 1}
    print(f"    有 ≥2 个 A 类失败格的实现 {len(multi)} 个（分母）；其中动作**互相冲突**的 {len(conflict)} 个")
    for k, v in conflict.items():
        print(f"      ⛔ {k[0].split('_')[0]} {k[1]}　动作 {v}")
    print(f"    ⇒ {'⛔ 押破（有冲突）' if conflict else '✅ 押中（不冲突）'}")
    print(f"    ⚠️ 分母只有 {len(multi)}，样本极小")

    # Z2：能否一次抬到 1.0 —— 用「全部失败格的动作集合」去合成一个 replace 链
    print(f"\n## §3 Z2 能否用「diff 反推的动作」一次修好")
    print("  （判据：对一个非满分实现，把它全部 A 类动作合成 `X.replace(a,b)` 链后，是否 5/5 通过）")
    print("  ⛔ 本轮**不做**这个合成：它要求动作作用在**函数的输出**上，而输出是 5 个不同输入的结果，")
    print("     合成后无法保证对其他输入无害 —— 这正是「逐格反推」的固有缺口。")
    print("  ⇒ **Z2 判 UNKNOWN**，理由记入 RESULTS（不是「没做」，是「装置上做不到」）。")

    # 逐实现的失败类型画像
    print(f"\n## §4 逐实现的失败画像（这个系统能不能知道「这次属于哪一类」）")
    for tid in sorted(by_task):
        for h, d in sorted(by_task[tid].items()):
            if d["score"] == 1.0:
                continue
            ks = collections.Counter(r["kind"] for r in rows if r["tid"] == tid and r["h"] == h)
            print(f"  {tid.split('_')[0]:6s} {h} score={d['score']:<4} {dict(ks)}")

    out = dict(n_fail=n_fail, dist=dict(dist), rows=rows,
               z1=dict(a=a, n=n_fail), z3=dict(multi=len(multi), conflict=len(conflict)))
    (HERE / "failures.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

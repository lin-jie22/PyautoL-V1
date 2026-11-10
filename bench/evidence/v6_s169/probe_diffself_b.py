"""V80b：把输入电池换成**判分用例之外**的输入，重算分歧。

为什么必须重算：V80 的电池就是题面 `tests` 里的调用表达式，于是

    PP（两份都过测） ⇒ 在两个判分输入上逐点相等 ⇒ 分歧 ≡ 0   （恒等式）
    MIX（一过一不过） ⇒ 至少在某个判分输入上不等 ⇒ 分歧 ≡ 1   （恒等式）

⇒ V80 的 `R_PP = 0.0000` 不是读数，是**构造**。`Δ = R_FF − R_PP` 因此测不到东西。
（PREREG §4 我只把 MIX 认成恒等式，漏了 PP —— 同一个坑的对称面。）

本脚本不改数据、不调 LLM，只做两件事：
  ① 用 AST 对每条判分输入的**实参**做通用变异（加键 / 数值±1 / 空容器 / 串加长），
     得到一批判分用例**没有覆盖**的输入；
  ② 拿 V80 已存盘的 44 份样例代码在这批新输入上重跑，按 PP / FF 重新分组。

两族变异分开报，避免把「越界输入」与「同域扰动」混成一个数：
  extend  加键 / 数值±1 / 串加长   —— 大体仍在契约内
  degen   空 dict / 空 list / 空串  —— 容易越界（读数要单独看）
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "bench/evidence/v6_s169")
from probe_diffself import EV, fn_of, load_tasks, CASES, run_sample  # noqa: E402


def mutations(node: ast.AST) -> list[tuple[str, str, ast.AST]]:
    """返回 [(族, 标签, 新节点)]。"""
    out: list[tuple[str, str, ast.AST]] = []
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool):
            return out
        if isinstance(v, int):
            out.append(("extend", f"int{v}->{v + 1}", ast.Constant(v + 1)))
            out.append(("extend", f"int{v}->{max(0, v - 1)}", ast.Constant(max(0, v - 1))))
        elif isinstance(v, float):
            out.append(("extend", "f+0.5", ast.Constant(v + 0.5)))
        elif isinstance(v, str):
            out.append(("extend", "str+x", ast.Constant(v + "x")))
            out.append(("degen", "str_empty", ast.Constant("")))
    elif isinstance(node, ast.Dict):
        out.append(("extend", "dict+key", ast.Dict(
            keys=list(node.keys) + [ast.Constant("__v1_x__")],
            values=list(node.values) + [ast.Constant(1)])))
        out.append(("degen", "dict_empty", ast.Dict(keys=[], values=[])))
    elif isinstance(node, ast.List):
        out.append(("extend", "list*2", ast.List(elts=list(node.elts) * 2, ctx=ast.Load())))
        out.append(("degen", "list_empty", ast.List(elts=[], ctx=ast.Load())))
    return out


def battery(task: dict, fn: str) -> list[tuple[str, str]]:
    """[(输入源码, 族)]，去重。"""
    seen: dict[str, str] = {}
    for line in task["tests"]:
        body = line.strip()
        if body.startswith("assert "):
            body = body[len("assert "):].strip()
        try:
            tree = ast.parse(body, mode="eval")
        except SyntaxError:
            continue
        for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == fn]:
            for i, arg in enumerate(call.args):
                for fam, tag, newarg in mutations(arg):
                    args = list(call.args)
                    args[i] = newarg
                    try:
                        src = ast.unparse(ast.Call(func=call.func, args=args, keywords=[]))
                    except Exception:
                        continue
                    seen.setdefault(src, f"{fam}:{tag}")
            break                     # 每题只取第一处同名调用，与 V80 电池同源
    return list(seen.items())


def main() -> int:
    src = EV / (sys.argv[1] if len(sys.argv) > 1 else "probe_diffself.json")
    d = json.loads(src.read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in d["tasks"]}
    battery_all = {}
    for _tag, path in CASES:
        for t in load_tasks(path):
            fn = fn_of(t["oracle"])
            battery_all[t["id"]] = (fn, battery(t, fn))

    rows = []
    for tid, rec in tasks.items():
        fn, muts = battery_all[tid]
        calls = [c for c, _ in muts]
        fam = [f.split(":")[0] for _, f in muts]
        outs = [run_sample(c, fn, calls) if c else "EMPTY" for c in rec["codes"]]
        pairs = []
        for i in range(len(outs)):
            for j in range(i + 1, len(outs)):
                if rec["labels"][i] == "invalid" or rec["labels"][j] == "invalid":
                    continue
                if isinstance(outs[i], str) or isinstance(outs[j], str):
                    continue
                g = ("PP" if rec["labels"][i] == rec["labels"][j] == "pass"
                     else "FF" if rec["labels"][i] == rec["labels"][j] == "fail" else "MIX")
                bad = [k for k in range(len(calls)) if outs[i][k] != outs[j][k]]
                pairs.append({"i": i, "j": j, "group": g, "diverge": bool(bad),
                              "bad": bad,
                              "bad_fam": sorted({fam[k] for k in bad})})
        rows.append({"id": tid, "suite": rec["suite"], "labels": rec["labels"],
                     "n_inputs": len(calls), "battery": [{"call": c, "fam": f}
                                                         for c, f in muts],
                     "pairs": pairs,
                     "faults": [o if isinstance(o, str) else "" for o in outs]})

    out = {"meta": {"from": src.name, "task_n": len(rows)}, "tasks": rows}
    (EV / "probe_diffself_b.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    for r in rows:
        g = {}
        for p in r["pairs"]:
            g.setdefault(p["group"], []).append(p)
        s = " ".join(f"{k}={sum(1 for p in v if p['diverge'])}/{len(v)}"
                     for k, v in sorted(g.items()))
        print(f"{r['suite']:8s} {r['id']:22s} 输入点={r['n_inputs']:2d} "
              f"{''.join(x[0] for x in r['labels'])} {s}")

    print("\n── 非判分输入上的分组汇总")
    tot, fam_bad, npts = {}, {}, {}
    for r in rows:
        for p in r["pairs"]:
            k = p["group"]
            tot.setdefault(k, [0, 0])
            tot[k][0] += 1
            tot[k][1] += int(p["diverge"])
            npts.setdefault(k, [0, 0])
            npts[k][0] += r["n_inputs"]
            npts[k][1] += len(p["bad"])
            for f in p["bad_fam"]:
                fam_bad.setdefault((k, f), 0)
                fam_bad[(k, f)] += 1
    for k in ("PP", "FF", "MIX"):
        n, dd = tot.get(k, [0, 0])
        pn, pd = npts.get(k, [0, 0])
        print(f"  {k:3s} 对={n:3d} 分歧={dd}/{n} = {(dd / n if n else float('nan')):.4f}"
              f"   按输入点={pd}/{pn} = {(pd / pn if pn else float('nan')):.4f}")
    print("\n  分歧点落在哪一族变异上（按对计，可多族）：")
    for (k, f), c in sorted(fam_bad.items()):
        print(f"    {k:3s} {f:24s} {c}")

    pp, ff = tot.get("PP", [0, 0]), tot.get("FF", [0, 0])
    r_pp = pp[1] / pp[0] if pp[0] else None
    r_ff = ff[1] / ff[0] if ff[0] else None
    print("\n── 判决")
    if r_pp is not None and r_ff is not None:
        print(f"  R_PP={r_pp:.4f}（假警报）  R_FF={r_ff:.4f}  Δ={r_ff - r_pp:+.4f}")
        print(f"  {'✅ 有可用信号' if r_ff - r_pp >= 0.30 and r_pp <= 0.20 else '❌ 不成立'}")
    else:
        print(f"  R_PP={r_pp} R_FF={r_ff} ⇒ UNKNOWN（某组无有效对）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

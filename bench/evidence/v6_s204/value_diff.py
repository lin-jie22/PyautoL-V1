#!/usr/bin/env python3
"""V115：值级 diff —— 输出级读得到什么，读不到什么。

零模型、零网络。判分 19 次（基线 7 + A 5 + B 2 + C 5），约 15 秒。

§0 全 55 失败格的值级 diff 分类（T_type / L_len / L_elem / S_* / E_err）
§1 C_nonstr 20 格 → 三层动作（L1 字面量 / L2 关系 / L3 数据）+ 压缩比
§2 三臂判分
§3 判决 W1~W6
"""
from __future__ import annotations

import ast
import collections
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
sys.path.insert(0, str(EV / "v6_s200"))
import idiom_transfer as I        # noqa: E402  harvest / task_of
import behaviour as B             # noqa: E402  parse_test

_ARG = re.compile(r"\w+\(\s*(['\"])(.*?)\1")
_RET_NONE = re.compile(r"^(\s*)return None\s*$", re.M)
_IN_CONT = re.compile(r"(\bin\s+)([\(\[][^\)\]]*[\)\]])")
_NORM = re.compile(r"^(\s*)(\w+) = x\.lower\(\)", re.M)   # ⛔ 不带 `\s*$`：行尾可能有注释


def score_of(tid: str, code: str) -> float | None:
    t = I.task_of(tid)
    pt = {"id": t["id"], "prompt": t.get("prompt", ""), "tests": t["tests"]}
    payload = json.dumps({"task": pt, "answer": code}, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                       capture_output=True, text=True, timeout=120)
    if not p.stdout.strip():
        return None
    return json.loads(p.stdout.strip().splitlines()[-1]).get("score")


def literal(s: str):
    if s.startswith("ERROR:") or s == "UNPARSED":
        return s
    try:
        return ast.literal_eval(s)
    except Exception:
        return s


# ---------- §0 值级 diff 分类 ----------
def vkind(got, exp) -> str:
    if isinstance(got, str) and got.startswith("ERROR:"):
        return "E_err"
    if type(got) is not type(exp):
        return "T_type"
    if isinstance(got, list):
        return "L_len" if len(got) != len(exp) else "L_elem"
    if isinstance(got, str):
        return "S_diff"
    if isinstance(got, (int, float)) and not isinstance(got, bool):
        return "N_num"
    return "OTHER"


# ---------- §1 关系形状：got 是否 exp 的超集、多出的元素是不是变体 ----------
def norm_candidates(s: str) -> dict[str, str]:
    return {"lower": s.lower(), "strip": s.strip(), "strip+lower": s.strip().lower()}


def relation_of(got: list, exp: list) -> tuple[str, set[str]]:
    """⇒ (shape, 需要补的归一化算子集合)"""
    g, e = list(got), list(exp)
    extra = [x for x in g if x not in e]
    if not extra:
        return "not_superset", set()
    need: set[str] = set()
    for x in extra:
        hit = [k for k, f in norm_candidates(x).items()
               if any(f == norm_candidates(y)[k] for y in e if isinstance(y, str)) and isinstance(x, str)]
        if not hit:
            return "superset_unknown", set()
        # 只保留最弱的那个（lower ⊂ strip+lower）
        need.add(sorted(hit, key=lambda k: ["lower", "strip", "strip+lower"].index(k))[0])
    # 若某个更弱的算子单独就够，就取最弱
    if need == {"lower"}:
        return "variant_lower", {"lower"}
    return "variant_full", {"strip+lower"}


# ---------- 三臂的动作算子（机械施加，无人工逐题改写） ----------
def arm_a(code: str) -> tuple[str, int]:
    """L1：`return None`（else 分支）→ `return False`"""
    new, n = _RET_NONE.subn(r"\1return False", code)
    return new, n


def arm_b(code: str) -> tuple[str, int]:
    """L2：去重 key → strip().lower()，原文仍入结果"""
    m = _NORM.search(code)
    if m:
        return _NORM.sub(r"\1\2 = x.strip().lower()", code), 1
    m = re.search(r"^(\s*)for x in xs:\s*$", code, re.M)
    if not m:
        return code, 0
    ind = re.search(r"^(\s*)", code[m.end():], re.M).group(1) or "    "
    ind = "    " * (len(m.group(1)) // 4 + 1)
    code = code[:m.end()] + "\n" + ind + "normalized_x = x.strip().lower()" + code[m.end():]
    code = code.replace("if x not in seen:", "if normalized_x not in seen:")
    code = code.replace("seen.add(x)", "seen.add(normalized_x)")
    return code, 2


def arm_c(code: str, words: list[str]) -> tuple[str, list[str]]:
    """L3：把失败 test 的实参词插进「离 `return False` 最近的容器字面量」"""
    conts = list(_IN_CONT.finditer(code))
    if not conts:
        return code, []
    best, best_d = None, 10 ** 9
    for m in conts:
        pos = code.find("return False", m.end())
        if pos < 0:
            continue
        d = pos - m.end()
        if d < best_d:
            best, best_d = m, d
    if best is None:
        return code, []
    cont = best.group(2)
    existing = cont
    added = []
    for w in dict.fromkeys(words):
        if f"'{w}'" in existing or f'"{w}"' in existing:
            continue
        added.append(w)
    if not added:
        return code, []
    ins = "".join(f", {w!r}" for w in added)
    new_cont = cont[:-1] + ins + cont[-1]
    return code[:best.start(2)] + new_cont + code[best.end(2):], added


def main() -> None:
    fails = json.loads((EV / "v6_s202" / "failures.json").read_text(encoding="utf-8"))["rows"]
    beh = json.loads((EV / "v6_s200" / "behaviour.json").read_text(encoding="utf-8"))
    corpus = I.harvest()

    # ---------- §0 ----------
    # ⛔ 必须从 behaviour.json 的**原始指纹**（`repr(EXPR)`）算。
    #    不能拿 failures.json 再 literal_eval：那里 A_sub/C_multi 存的是 `str(got)`，
    #    `literal_eval("1,234.50")` 会得到 tuple `(1, 234.5)` —— 纯属造出来的假象。
    print("## §0 全失败格的值级 diff 分类（源 = behaviour.json 原始指纹）")
    dist0 = collections.Counter()
    cells0 = []
    for key, v in beh.items():
        tid, h = key.split("::")
        exps = [B.parse_test(str(x))[1] for x in I.task_of(tid)["tests"]]
        for i, (got_s, exp) in enumerate(zip(v["fp"], exps)):
            got = literal(got_s)
            if isinstance(got, str) and got.startswith("ERROR:"):
                k = "E_err"
            elif got == exp:
                k = "equal"
            else:
                k = vkind(got, exp)
            dist0[k] += 1
            cells0.append(dict(tid=tid, h=h, i=i, got=repr(got), exp=repr(exp), vkind=k))
    n_all = sum(dist0.values())
    for k, v in sorted(dist0.items(), key=lambda kv: -kv[1]):
        print(f"  {k:12s} {v:3d}　{v / n_all:6.1%}")
    print(f"  分母 n_cell_total（26 实现 × 5 test）= {n_all}")
    print(f"  分母 n_cell_fail（失败格）= {n_all - dist0['equal']}")

    # 失败格与 V113 的五类交叉（确认两张表说的是同一批格）
    x = collections.Counter()
    key5 = {(r["tid"], r["h"], r["i"]): r["kind"] for r in fails}
    for c in cells0:
        if c["vkind"] == "equal":
            continue
        x[(c["vkind"], key5.get((c["tid"], c["h"], c["i"]), "?"))] += 1
    print("\n  **值级 5 类 × V113 5 类 交叉表**")
    for k, v in sorted(x.items(), key=lambda kv: -kv[1]):
        print(f"    {k[0]:10s} × {k[1]:10s} {v:3d}")

    # ---------- §1 ----------
    print("\n## §1 C_nonstr 20 格 → 三层动作")
    cn = [r for r in fails if r["kind"] == "C_nonstr"]
    vmap = {(c["tid"], c["h"], c["i"]): (literal(c["got"]), literal(c["exp"])) for c in cells0}
    for r in cn:
        r["_got_v"], r["_exp_v"] = vmap[(r["tid"], r["h"], r["i"])]
    print(f"  n_cell_cnonstr = {len(cn)}")
    by_task = collections.defaultdict(list)
    for r in cn:
        by_task[r["tid"]].append(r)

    acts = {}          # (tid,h) -> [动作描述]
    for tid in sorted(by_task):
        print(f"\n  **{tid}**（{len(by_task[tid])} 格）")
        for r in by_task[tid]:
            got, exp = r["_got_v"], r["_exp_v"]
            if isinstance(got, list) and isinstance(exp, list):
                shape, need = relation_of(got, exp)
                act = f"L2:key<={sorted(need)}  ({shape})" if need else f"L2:?({shape})"
            elif got is None and exp is False:
                act = "L1:None->False"
            else:
                act = f"L?:{type(got).__name__}->{type(exp).__name__}"
            r["_act"] = act
            acts.setdefault((tid, r["h"]), []).append(act)
            print(f"    {r['h']} t{r['i']}　{got!r} → {exp!r}　{act}")

    print("\n  **动作去重表**")
    tbl = collections.Counter(a for r in cn for a in [r["_act"]])
    for a, c in sorted(tbl.items(), key=lambda kv: -kv[1]):
        print(f"    {a:34s} 覆盖 {c:2d} 格")

    # 逐实现的「真正需要的动作条数」（L3 按 (实现, 词) 计，不按词计 —— 见 PREREG §6.3）
    print("\n  **压缩比（格数 / 动作条数）**")
    ziped = {}
    for tid in sorted(by_task):
        cells = len(by_task[tid])
        if tid.startswith("g7d2"):
            n_act = 1                      # 一个 L2 模板
            unit = "1 条 L2 模板"
        else:
            n_act = 0
            for (t, h), _ in [(k, v) for k, v in acts.items() if k[0] == tid]:
                ws = set()
                for r in by_task[tid]:
                    if r["h"] != h:
                        continue
                    tsrc = [x for x in I.task_of(tid)["tests"]][r["i"]]
                    m = _ARG.search(tsrc)
                    if m:
                        ws.add(m.group(2).strip().lower())
                code = corpus[tid][h]
                conts = list(_IN_CONT.finditer(code))
                if conts:
                    best, bd = None, 10 ** 9
                    for mm in conts:
                        pos = code.find("return False", mm.end())
                        if pos < 0:
                            continue
                        if pos - mm.end() < bd:
                            best, bd = mm, pos - mm.end()
                    if best:
                        have = {w for w in ws if f"'{w}'" in best.group(2) or f'"{w}"' in best.group(2)}
                        ws -= have
                n_act += len(ws)
            unit = f"{n_act} 条 L3 数据项（按 实现×词 计）"
        ziped[tid] = dict(cells=cells, n_act=n_act, ratio=round(cells / n_act, 2) if n_act else None)
        print(f"    {tid.split('_')[0]:6s} 格 {cells:2d} / {unit:34s} ⇒ 压缩比 {ziped[tid]['ratio']}")

    # ---------- §2 三臂 ----------
    print("\n## §2 三臂判分")
    g7e1 = [h for h in sorted(corpus["g7e1_parse_flag"])]
    g7e1 = [h for h in g7e1 if any(r["h"] == h for r in by_task["g7e1_parse_flag"])]
    g7d2 = [h for h in sorted(corpus["g7d2_dedupe_keep"]) if any(r["h"] == h for r in by_task["g7d2_dedupe_keep"])]

    out = dict(cells=cn, ziped=ziped, arms={})

    print("\n  **基线**（重算，不引用旧文件）")
    base = {}
    for tid, hs in [("g7e1_parse_flag", g7e1), ("g7d2_dedupe_keep", g7d2)]:
        for h in hs:
            base[(tid, h)] = score_of(tid, corpus[tid][h])
            print(f"    {tid.split('_')[0]:6s} {h}  {base[(tid, h)]}")

    print("\n  **A 臂（L1：`return None` → `return False`）**")
    ra = []
    for h in g7e1:
        code, n = arm_a(corpus["g7e1_parse_flag"][h])
        s = score_of("g7e1_parse_flag", code)
        ra.append(dict(h=h, s0=base[("g7e1_parse_flag", h)], n_edit=n, s1=s))
        print(f"    {h}  改动 {n} 处　{base[('g7e1_parse_flag', h)]} → {s}")
    out["arms"]["A"] = ra

    print("\n  **B 臂（L2：key → `x.strip().lower()`）**")
    rb = []
    for h in g7d2:
        code, n = arm_b(corpus["g7d2_dedupe_keep"][h])
        s = score_of("g7d2_dedupe_keep", code)
        rb.append(dict(h=h, s0=base[("g7d2_dedupe_keep", h)], n_edit=n, s1=s))
        print(f"    {h}  改动 {n} 处　{base[('g7d2_dedupe_keep', h)]} → {s}")
    out["arms"]["B"] = rb

    print("\n  **C 臂（L3：把失败实参词插进最近的假值容器）**")
    rc = []
    for h in g7e1:
        words = []
        for r in by_task["g7e1_parse_flag"]:
            if r["h"] != h:
                continue
            m = _ARG.search(I.task_of("g7e1_parse_flag")["tests"][r["i"]])
            if m:
                words.append(m.group(2).strip().lower())
        code, added = arm_c(corpus["g7e1_parse_flag"][h], words)
        s = score_of("g7e1_parse_flag", code)
        rc.append(dict(h=h, s0=base[("g7e1_parse_flag", h)], words=words, added=added, s1=s))
        print(f"    {h}  需词 {words}　新插 {added}　{base[('g7e1_parse_flag', h)]} → {s}")
    out["arms"]["C"] = rc

    # ---------- §2b 逐格位图（PREREG §3 W2 押了「掉的那一格恒为 t4」，必须真测） ----------
    print("\n  **§2b A 臂逐格位图**（判分器只给总分，这里直接执行 5 条 test）")

    def bitmap(tid: str, code: str) -> list:
        ns: dict = {}
        try:
            exec(compile(code, "<c>", "exec"), ns)
        except Exception as e:                      # noqa: BLE001
            return [f"COMPILE:{type(e).__name__}"] * len(I.task_of(tid)["tests"])
        res = []
        for ts in I.task_of(tid)["tests"]:
            try:
                exec(compile(ts, "<t>", "exec"), ns)
                res.append(True)
            except AssertionError:
                res.append(False)
            except Exception as e:                  # noqa: BLE001
                res.append(f"EXC:{type(e).__name__}")
        return res

    for x in ra:
        code, _ = arm_a(corpus["g7e1_parse_flag"][x["h"]])
        x["cells"] = bitmap("g7e1_parse_flag", code)
        fail = [i for i, v in enumerate(x["cells"]) if v is not True]
        print(f"    {x['h']}　{x['cells']}　掉格 = {fail}")

    # ---------- §2c 事后扩展（PREREG 未押，仅登记）----------
    # 480ada8e 的 t0/t3 逐格读出的动作是 `lower`、t1/t2 是 `strip+lower`
    # ⇒ 若按「最弱动作」外推，会得到「只加 lower」的实现。语料里 74aa48d8 恰好就是这个样子（0.6）。
    print("\n  **§2c 事后扩展：按「最弱动作」外推**（PREREG 未押，只登记）")
    code = corpus["g7d2_dedupe_keep"]["480ada8e"]
    weak = code.replace("if x not in seen:", "if x.lower() not in seen:").replace(
        "seen.add(x)", "seen.add(x.lower())")
    s_weak = score_of("g7d2_dedupe_keep", weak)
    print(f"    480ada8e + 只 lower　0.2 → {s_weak}　（语料里 74aa48d8 的起点 = "
          f"{base[('g7d2_dedupe_keep', '74aa48d8')]}）")
    out["arm_weak"] = dict(s0=0.2, s1=s_weak, known_impl_score=base[("g7d2_dedupe_keep", "74aa48d8")])

    # ---------- §1b 逐格动作在实现内部是否一致（V113 Z3 的复现）----------
    print("\n## §2d 逐格读出动作的实现内一致性（V113 Z3 复现；跑后补的观察项）")
    for tid in sorted(by_task):
        per = collections.defaultdict(set)
        for r in by_task[tid]:
            per[r["h"]].add(r["_act"])
        for h, s in sorted(per.items()):
            flag = "⛔ 冲突" if len(s) > 1 else "一致"
            print(f"    {tid.split('_')[0]:6s} {h}　{flag}　{sorted(s)}")

    # ---------- §3 判决 ----------
    print("\n## §3 判决")
    r1, r2 = ziped["g7d2_dedupe_keep"]["ratio"], ziped["g7e1_parse_flag"]["ratio"]
    print(f"\n  **W1 同一失败类内部压缩比差 ≥3 倍**　g7d2 {r1} : g7e1 {r2} = {r1 / r2:.2f}×")
    print(f"    ⇒ {'✅ 押中' if r1 / r2 >= 3 else '⛔ 押破'}")

    all_le1 = all(x["s1"] is not None and x["s1"] >= x["s0"] for x in ra)
    all_lt1 = all(x["s1"] is not None and x["s1"] < 1.0 for x in ra)
    drop_t4 = all([i for i, v in enumerate(x["cells"]) if v is not True] == [4] for x in ra)
    print(f"\n  **W2 A 臂（L1）全部 ≥ 起点、全部 < 1.0、掉格恒为 t4**")
    print(f"    全部 ≥ 起点：{all_le1}　全部 <1.0：{all_lt1}　掉格恒为 t4：{drop_t4}")
    print(f"    ⇒ {'✅ 押中' if (all_le1 and all_lt1 and drop_t4) else '⛔ 押破'}")

    ok_b = sum(1 for x in rb if x["s1"] == 1.0)
    print(f"\n  **W3 B 臂（L2）2/2 到 1.0**　实际 {ok_b}/2")
    print(f"    ⇒ {'✅ 押中' if ok_b == len(rb) == 2 else '⛔ 押破'}")

    ok_c = sum(1 for x in rc if x["s1"] == 1.0)
    print(f"\n  **W4 C 臂（L3）5/5 到 1.0**　实际 {ok_c}/{len(rc)}")
    print(f"    ⇒ {'✅ 押中' if ok_c == len(rc) == 5 else '⛔ 押破'}")

    print(f"\n  **W5「值级 diff 能给出可执行动作」在 C_nonstr 上是假的**")
    print(f"    L1 封顶（<1.0）= {all_lt1}　且 L1 有改善 = {all_le1} ⇒ {'✅ 押中' if all_lt1 else '⛔ 押破'}")

    print(f"\n  **W6 同类别内部「每格动作条数」差 ≥3 倍**")
    print(f"    g7d2 {ziped['g7d2_dedupe_keep']['n_act']} 条/{ziped['g7d2_dedupe_keep']['cells']} 格 vs "
          f"g7e1 {ziped['g7e1_parse_flag']['n_act']} 条/{ziped['g7e1_parse_flag']['cells']} 格")
    print(f"    ⇒ {'✅ 押中' if r1 / r2 >= 3 else '⛔ 押破'}（同 W1 口径）")

    print(f"\n  **分母口径**　n_cell_cnonstr={len(cn)}　n_impl_cnonstr={len(by_task['g7e1_parse_flag']) and len(set([r['h'] for r in cn]))}"
          f"　n_impl_nonfull=16　n_impl_all=26　n_cell_total={n_all}　n_cell_fail={n_all - dist0['equal']}")

    (HERE / "value_diff.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("\n  写出 value_diff.json")


if __name__ == "__main__":
    main()

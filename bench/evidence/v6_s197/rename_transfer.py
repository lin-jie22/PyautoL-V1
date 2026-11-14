#!/usr/bin/env python3
"""V108：结构能否迁移 —— 把私有名字（alpha-renaming）去掉再搬。

零模型、零网络。判据 = `bench/scorers/code_exec.py`。

§0 物料 + 改名（ast 取形参/局部名，tokenize 只替换 NAME token）
§1 R2 编译通过率（vs V107 的 24.9%）
§2 R1 跨题命中
§3 R3 命中分类：常量巧合 / 结构迁移
§4 R4 全分布
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import pathlib
import re
import subprocess
import sys
import tokenize
from concurrent.futures import ThreadPoolExecutor

EV = pathlib.Path(__file__).resolve().parent.parent
ROOT = EV.parent.parent
TASK = "g7d3_fmt_amount"
SUITE = ROOT / "bench" / "heldout" / "g7j_n4_heldout.jsonl"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
WORKERS = 3
V107_RATE = 56 / 225

_FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.S)


def code_of(answer: str) -> str:
    m = _FENCE.search(answer or "")
    return (m.group(1) if m else (answer or "")).strip("\n")


def load_task() -> dict:
    for ln in SUITE.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r["id"] == TASK:
            return r
    raise SystemExit("找不到题目")


def harvest() -> dict[str, str]:
    out: dict[str, str] = {}
    for rd in sorted(EV.iterdir()):
        for ans in sorted(rd.glob("answers/**/answers.jsonl")):
            for ln in ans.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                if r.get("kind") != "answer":
                    continue
                tid, a = r.get("task_id"), r.get("answer") or ""
                if not tid or not a:
                    continue
                key = f"{tid}::{hashlib.sha256(a.encode()).hexdigest()[:8]}"
                out.setdefault(key, code_of(a))
    return out


def name_map(src: str) -> dict[str, str] | None:
    """捐赠实现的**自洽**改名：形参→x，唯一局部名→fmt_str。返回 None = 无法唯一映射（跳过）。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)), None)
    if fn is None:
        return None
    params = [a.arg for a in fn.args.args]
    if len(params) != 1:
        return None
    stores: list[str] = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            if n.id not in stores:
                stores.append(n.id)
    if len(stores) != 1:
        return None
    return {params[0]: "x", stores[0]: "fmt_str"}


def rename(src: str, mapping: dict[str, str]) -> str:
    """只替换 NAME token（不碰字符串字面量）。"""
    out = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:
        return src
    for t in toks:
        if t.type == tokenize.NAME and t.string in mapping:
            out.append((t.type, mapping[t.string], t.start, t.end, t.line))
        else:
            out.append((t.type, t.string, t.start, t.end, t.line))
    try:
        return tokenize.untokenize(out)
    except Exception:  # noqa: BLE001
        return src


def main() -> None:
    task = load_task()
    pt = {"id": task["id"], "prompt": task.get("prompt", ""), "tests": task["tests"]}

    def score(code: str) -> float | None:
        payload = json.dumps({"task": pt, "answer": code}, ensure_ascii=False)
        try:
            p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                               capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return None
        if not p.stdout.strip():
            return None
        try:
            return json.loads(p.stdout.strip().splitlines()[-1]).get("score")
        except Exception:  # noqa: BLE001
            return None

    def ok(code: str) -> bool:
        try:
            compile(code, "<m>", "exec")
            return True
        except SyntaxError:
            return False

    pool = harvest()
    same = {k: v for k, v in pool.items() if k.startswith(TASK + "::")}
    other = {k: v for k, v in pool.items() if not k.startswith(TASK + "::")}
    target = next(v for k, v in same.items() if k.split("::")[1] == "075b4099")
    tlines = target.splitlines(keepends=True)

    print("## §0 物料与改名")
    print(f"  别的题实现 {len(other)} 个；目标 5 行（起点 {score(target)}）")
    donors: dict[str, list] = {}
    skipped = []
    for k, v in other.items():
        mp = name_map(v)
        if mp is None:
            skipped.append(k)
            continue
        rv = rename(v, mp)
        tid = k.split("::")[0]
        for i, l in enumerate(rv.splitlines()):
            donors.setdefault(l, []).append(f"{tid}:L{i}({mp})")
    print(f"  改名成功 {len(other)-len(skipped)} 个，跳过 {len(skipped)} 个（形参≠1 或局部名≠1）：")
    for s in skipped:
        print(f"    跳过 {s}")
    print(f"  改名后互异捐赠行 = {len(donors)} 条")
    for l in list(donors)[:8]:
        print(f"    {l!r}")
    if len(donors) > 8:
        print(f"    …… 共 {len(donors)} 条")

    # 满分实现的 L4（用于 R3 分类）
    perfect_l4 = set()
    for k, v in same.items():
        if score(v) == 1.0:
            perfect_l4.add(v.splitlines()[-1].strip())
    print(f"  满分实现 L4（去缩进后互异）= {perfect_l4}")

    # ---------- 组合 ----------
    combos = []
    for j, tl in enumerate(tlines):
        for dl, srcs in donors.items():
            if dl + "\n" == tl:
                continue
            combos.append((f"L{j}<-{srcs[0]}", dl, "".join(tlines[:j] + [dl + "\n"] + tlines[j + 1:])))
    kept = [(lab, dl, c) for lab, dl, c in combos if ok(c)]

    print("\n## §1 R2 编译通过率")
    rate = len(kept) / len(combos) if combos else 0
    print(f"  组合 {len(combos)} 个，compile 通过 {len(kept)} 个 = {rate:.1%}"
          f"　V107 是 {V107_RATE:.1%}")
    print("  ⇒ " + ("**上升**（押中）" if rate > V107_RATE else "⛔ **没上升** ⇒ 「名字」不是主要失败源，"
                                                              "V107 §175.4 机制段要重写"))

    with ThreadPoolExecutor(WORKERS) as ex:
        sc = list(ex.map(score, [c for _l, _d, c in kept]))
    pairs = [(lab, dl, s) for (lab, dl, _c), s in zip(kept, sc) if s is not None]

    print("\n## §2/§4 R1+R4 全分布（不许只报命中）")
    dist: dict[float, int] = {}
    for _l, _d, s in pairs:
        dist[s] = dist.get(s, 0) + 1
    base = score(target)
    print(f"  {len(pairs)} 个候选：{dict(sorted(dist.items(), reverse=True))}")
    print(f"  起点 {base}：低于 {sum(1 for _l,_d,s in pairs if s<base)}"
          f"　等于 {sum(1 for _l,_d,s in pairs if s==base)}"
          f"　高于 {sum(1 for _l,_d,s in pairs if s>base)}")
    best = max((s for _l, _d, s in pairs), default=None)
    print(f"  最高 = {best}")

    print("\n## §3 R3 命中分类（常量巧合 vs 结构迁移）")
    wins = [(lab, dl, s) for lab, dl, s in pairs if s == 1.0]
    if not wins:
        print("  ⛔ 命中 = **0**")
        print("  ⇒ **R1 破：改名也没救活跨题搬运 ⇒ 结构也不可迁移。**")
        print("  ⇒ L4 的「搬运」路线到此为止，只剩「重新发明」。")
    else:
        for lab, dl, s in wins:
            norm = dl.strip()
            kind = "常量巧合（改名后与满分 L4 逐字相同）" if norm in perfect_l4 else "**结构迁移**"
            print(f"    {lab} ⇒ {s}")
            print(f"      捐赠行（改名后）: {dl!r}")
            print(f"      分类: {kind}")

    out = {"rate": rate, "n_combo": len(combos), "n_kept": len(kept),
           "dist": {str(k): v for k, v in dist.items()}, "best": best,
           "wins": len(wins), "skipped": len(skipped),
           "win_list": [lab for lab, _d, _s in wins]}
    (EV / "v6_s197" / "rename.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

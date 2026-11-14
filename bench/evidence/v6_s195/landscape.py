#!/usr/bin/env python3
"""V106：程序空间的适应度地形 —— 梯度在字符层还是语句块层？

零模型、零网络。判据 = `bench/scorers/code_exec.py`（每候选一次子进程，不复刻判据）。

§0 起点与物料（来自 V105 的分桶表）
§1 过滤合法性抽查（compile 丢掉的那些，code_exec 确实给 0.0）
§2 P1 字符级 1-邻域（0.4 起点）：最高分
§3 P2 满分脆弱性（1.0 起点）
§4 P3 块级盲交叉（整行替换）：能不能 1 步到 1.0
§5 P4 合并判决
"""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

EV = pathlib.Path(__file__).resolve().parent.parent
ROOT = EV.parent.parent
TASK = "g7d3_fmt_amount"
SUITE = ROOT / "bench" / "heldout" / "g7j_n4_heldout.jsonl"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
RUNS = ["v6_s189", "v6_s190", "v6_s191", "v6_s192"]

ALPHA = [",", ".", "'", '"', "(", ")", " ", "_", "-"]

#: 与 `bench/scorers/code_exec.py` **同一口径**的围栏抽取（答案里常带 ```python 围栏）
_FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.S)


def code_of(answer: str) -> str:
    m = _FENCE.search(answer or "")
    return (m.group(1) if m else (answer or "")).strip("\n")

#: ⚠️ 事后缩减（见 PREREG.md §7）：6 并发被 SIGKILL，降到 3 并发；
#: 字符级邻域只跑 1 个 0.4 与 1 个 1.0（各约 596 个合法邻居），块级交叉仍跑全部 3 个目标。
TARGETS = ["075b4099", "419ec792", "5755e033"]   # 0.4 / 0.4 / 0.6（块级交叉用全部）
CHAR_TARGETS = ["075b4099"]                      # 字符级邻域只测这一个
PERFECT = ["8089eee6"]                           # 1.0
WORKERS = 3


def load_task() -> dict:
    for ln in SUITE.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r["id"] == TASK:
            return r
    raise SystemExit("找不到题目")


def harvest() -> dict[str, str]:
    impls: dict[str, str] = {}
    for run in RUNS:
        d = EV / run
        if not d.exists():
            continue
        for ans in sorted(d.glob("answers/**/answers.jsonl")):
            for ln in ans.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                if r.get("kind") == "answer" and r.get("task_id") == TASK:
                    a = r.get("answer") or ""
                    impls.setdefault(hashlib.sha256(a.encode()).hexdigest()[:8], a)
    return impls


def make_scorer(task: dict):
    payload_task = {"id": task["id"], "prompt": task.get("prompt", ""), "tests": task["tests"]}

    def score(code: str) -> float | None:
        payload = json.dumps({"task": payload_task, "answer": code}, ensure_ascii=False)
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

    return score


def compiles(code: str) -> bool:
    try:
        compile(code, "<m>", "exec")
        return True
    except SyntaxError:
        return False


def char_mutants(code: str):
    """盲的 1 字符编辑：替换 / 删除 / 插入。产出 (标签, 变体)，**按变体字节去重**。"""
    n = len(code)
    seen = {code}
    for i in range(n):
        if code[i] == "\n":
            continue
        for c in ALPHA:
            if c == code[i]:
                continue
            m = code[:i] + c + code[i + 1:]
            if m not in seen:
                seen.add(m)
                yield f"sub@{i}:{code[i]!r}->{c!r}", m
        m = code[:i] + code[i + 1:]
        if m not in seen:
            seen.add(m)
            yield f"del@{i}:{code[i]!r}", m
    for i in range(n + 1):
        for c in ALPHA:
            m = code[:i] + c + code[i:]
            if m not in seen:
                seen.add(m)
                yield f"ins@{i}:{c!r}", m


def main() -> None:
    task = load_task()
    score = make_scorer(task)
    raw = harvest()
    print("## §0 起点与物料")
    print(f"  仓库档案里共有 {len(raw)} 个 g7d3 实现")
    impls = {h: code_of(v) for h, v in raw.items()}
    print("\n## §0b 校准：裸代码 与 原答案（带 Markdown 围栏）必须同分")
    mism = []
    for h in sorted(raw):
        a, b = score(raw[h]), score(impls[h])
        if a != b:
            mism.append((h, a, b))
    print(f"  {len(raw)} 个实现逐一比对　不一致 {len(mism)} 个 {mism}")
    print("  ⇒ " + ("✅ 裸代码等价（后续在裸代码上做变异，不与判据口径脱节）"
                    if not mism else "⛔ 不等价，必须带着围栏变异"))
    base = {h: score(impls[h]) for h in sorted(impls)}
    for h in sorted(base):
        tag = " ← 目标" if h in TARGETS else (" ← 满分" if h in PERFECT else "")
        print(f"    {h}  {base[h]}{tag}")

    # ---------- §1 过滤抽查 ----------
    print("\n## §1 过滤合法性抽查（compile 不过的直接丢掉，抽 20 个验证 code_exec 也给 0.0）")
    src = impls[TARGETS[0]]
    dropped = [m for lab, m in char_mutants(src) if not compiles(m)][:20]
    if dropped:
        with ThreadPoolExecutor(WORKERS) as ex:
            sc = list(ex.map(score, dropped))
        zeros = sum(1 for s in sc if s == 0.0)
        print(f"  抽 {len(dropped)} 个语法不合法的变体 ⇒ code_exec 给 0.0 的 {zeros}/{len(dropped)}"
              f"　分数集合 {sorted(set(sc), key=str)}")
        print("  ⇒ " + ("✅ 过滤与判据同口径（丢掉它们不改变任何结论）"
                        if zeros == len(dropped) else "⛔ 不同口径！过滤会漏掉真实分数，必须全跑"))
    else:
        print("  （没有语法不合法的变体，过滤一步空转）")

    # ---------- §2 P1 字符级邻域 ----------
    print("\n## §2 P1 字符级 1-邻域：0.4 起点上还有没有更高分")
    p1 = {}
    for h in CHAR_TARGETS:
        code = impls[h]
        cands = [(lab, m) for lab, m in char_mutants(code) if compiles(m)]
        with ThreadPoolExecutor(WORKERS) as ex:
            sc = list(ex.map(lambda p: score(p[1]), cands))
        pairs = [(lab, s) for (lab, _m), s in zip(cands, sc) if s is not None]
        dist: dict[float, int] = {}
        for _l, s in pairs:
            dist[s] = dist.get(s, 0) + 1
        best = max((s for _l, s in pairs), default=None)
        start = base[h]
        print(f"  {h}（起点 {start}）：合法邻居 {len(pairs)} 个　分布 "
              + " ".join(f"{k:g}×{v}" for k, v in sorted(dist.items(), reverse=True)))
        if best is None:
            print("    ⇒ ⛔ 邻域没有可用读数（全部超时/无输出），P1 **不可判**")
        else:
            print(f"    ⇒ 邻域最高分 = {best}　" + ("**没有更高分 ⇒ 字符级无梯度**"
                                                   if start is not None and best <= start
                                                   else "⚠️ 有更高分"))
        ups = [(l, s) for l, s in pairs if start is not None and s > start]
        print(f"    比起点高的邻居：{len(ups)} 个 {ups[:6]}")
        p1[h] = {"start": start, "best": best, "n": len(pairs),
                 "ups": len(ups), "dist": {str(k): v for k, v in dist.items()}}

    # ---------- §3 P2 满分脆弱性 ----------
    print("\n## §3 P2 满分脆弱性：1.0 起点的 1-字符-邻域")
    p2 = {}
    for h in PERFECT:
        code = impls[h]
        cands = [(lab, m) for lab, m in char_mutants(code) if compiles(m)]
        with ThreadPoolExecutor(WORKERS) as ex:
            sc = list(ex.map(lambda p: score(p[1]), cands))
        svals = [s for s in sc if s is not None]
        dist: dict[float, int] = {}
        for s in svals:
            dist[s] = dist.get(s, 0) + 1
        n = len(svals)
        if n == 0:
            print(f"  {h}：⛔ 没有可用读数，P2 **不可判**")
            p2[h] = {"n": 0, "keep": 0, "low": 0, "zero": 0, "diff_at_1": 0}
            continue
        keep = dist.get(1.0, 0)
        low = sum(v for k, v in dist.items() if k <= 0.6)
        zero = dist.get(0.0, 0)
        # 「仍是 1.0 但代码不同」= 同分不同实现
        diff_at_1 = sum(1 for (lab, m), s in zip(cands, sc) if s == 1.0 and m != code)
        print(f"  {h}（起点 1.0）：合法邻居 {n} 个　分布 "
              + " ".join(f"{k:g}×{v}" for k, v in sorted(dist.items(), reverse=True)))
        print(f"    仍 1.0：{keep}（{keep/n:.1%}，其中**代码不同**的 {diff_at_1} 个）"
              f"　掉到 ≤0.6：{low}（{low/n:.1%}）　掉到 0.0：{zero}（{zero/n:.1%}）")
        p2[h] = {"n": n, "keep": keep, "low": low, "zero": zero, "diff_at_1": diff_at_1}

    print("  （§2/§3 完成）", flush=True)

    # ---------- §4 P3 块级盲交叉 ----------
    print("\n## §4 P3 块级盲交叉：把目标的一整行换成捐赠实现的一整行（盲、穷举）")
    donors = {h: impls[h] for h in impls if h not in TARGETS}
    p3 = {}
    for h in TARGETS:
        tlines = impls[h].splitlines(keepends=True)
        combos = []
        for j, tl in enumerate(tlines):
            for dh, dc in donors.items():
                for k, dl in enumerate(dc.splitlines(keepends=True)):
                    if dl == tl:
                        continue
                    mut = "".join(tlines[:j] + [dl] + tlines[j + 1:])
                    combos.append((f"{h}:L{j}<-{dh}:L{k}", mut))
        with ThreadPoolExecutor(WORKERS) as ex:
            sc = list(ex.map(lambda p: score(p[1]), combos))
        pairs = [(lab, s) for (lab, _m), s in zip(combos, sc) if s is not None]
        best = max((s for _l, s in pairs), default=None)
        wins = [(l, s) for l, s in pairs if s == 1.0]
        ups = [(l, s) for l, s in pairs if base[h] is not None and s > base[h]]
        print(f"  {h}（起点 {base[h]}）：组合 {len(pairs)} 个　最高分 = {best}"
              f"　比起点高的 {len(ups)} 个　到 1.0 的 {len(wins)} 个")
        for l, s in wins[:12]:
            print(f"    ✅ {l} ⇒ {s}")
        if len(wins) > 12:
            print(f"    …… 另有 {len(wins)-12} 个")
        p3[h] = {"n": len(pairs), "best": best, "ups": len(ups), "wins": len(wins),
                 "win_list": [l for l, _s in wins]}

    # ---------- §5 判决 ----------
    print("\n## §5 P4 合并判决")
    char_grad = all(p1[h]["ups"] == 0 for h in CHAR_TARGETS)
    block_grad = any(p3[h]["wins"] > 0 for h in TARGETS)
    print(f"  字符级：{len(CHAR_TARGETS)} 个目标里邻域高于起点的总数 = {sum(p1[h]['ups'] for h in CHAR_TARGETS)}"
          f" ⇒ " + ("**无梯度**" if char_grad else "有梯度"))
    print(f"  块级：能一步到 1.0 的目标 = "
          f"{[h for h in TARGETS if p3[h]['wins'] > 0]}"
          f" ⇒ " + ("**有梯度**" if block_grad else "也无梯度"))
    if char_grad and block_grad:
        print("  ⭐ 结论：**梯度只存在于语句块粒度，不存在于字符粒度。**")
    elif char_grad and not block_grad:
        print("  ⛔ 结论：**两层都没有梯度** ⇒ 自改需要结构重写，不是搜索。")
    elif not char_grad:
        print("  ⭐ 结论：字符级就有梯度 ⇒ 1 编辑爬山可行。")
    fr = [p2[h]["low"] / p2[h]["n"] for h in PERFECT if p2[h]["n"]]
    print(f"  满分脆弱性：掉到 ≤0.6 的比例 = "
          + "、".join(f"{h} {p2[h]['low']/p2[h]['n']:.1%}" for h in PERFECT if p2[h]["n"])
          + "　⇒ " + ("**脆**（>50%）" if fr and min(fr) > 0.5 else "不脆（≤50%）"))

    out = {"base": base, "P1": p1, "P2": p2, "P3": p3}
    (EV / "v6_s195" / "landscape.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

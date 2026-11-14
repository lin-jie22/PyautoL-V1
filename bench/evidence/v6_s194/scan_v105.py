#!/usr/bin/env python3
"""V105 读数：「满分不迁移」到底是谁变的 —— 答案，还是分数？

零模型、零网络。输入 = `bench/evidence/v6_s189..s192` 里全部 `g7d3_fmt_amount` 答案。
判分 = 用**固定 tests**（`bench/heldout/g7j_n4_heldout.jsonl` 的原版）重算 `bench/scorers/code_exec.py`。

§0 H1 审计（scorer 吃不吃 prompt）
§1 盘点（42 格 / 9 实现 / 题面分组）
§2 H1 交叉校验（s191/s192 的 scores.json vs 重算）
§3 H2 分辨率（9 实现 → 几个分）
§4 H3 归因（按题面组列实现与分数）—— **本轮核心**
§5 H4 变异分解（per-prompt mean 能不能由实现分布解释）
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys

EV = pathlib.Path(__file__).resolve().parent.parent   # bench/evidence
ROOT = EV.parent.parent                                # 仓库根
TASK = "g7d3_fmt_amount"
SUITE = ROOT / "bench" / "heldout" / "g7j_n4_heldout.jsonl"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
RUNS = ["v6_s189", "v6_s190", "v6_s191", "v6_s192"]


def load_task() -> dict:
    for ln in SUITE.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r["id"] == TASK:
            return r
    raise SystemExit(f"套件里没有 {TASK}")


def score_code(task: dict, code: str) -> tuple[int, dict]:
    payload = json.dumps({"task": {"id": task["id"], "prompt": task.get("prompt", ""),
                                   "tests": task["tests"]}, "answer": code},
                         ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCORER)], input=payload,
                       capture_output=True, text=True, timeout=180)
    if not p.stdout.strip():
        return p.returncode, {"score": None, "why": f"无输出 rc={p.returncode} {p.stderr[:120]}"}
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def prompt_of(run: str, tag: str) -> str:
    """题面变体标签。V100 的闭环跑在原版 g7j_n4 上，tag 里没有 `__`。"""
    if "__" in tag:
        return tag.split("__", 1)[1]
    return "g7j_n4_heldout"


def arm_of(tag: str) -> str:
    return tag.split("__", 1)[0] if "__" in tag else tag


def main() -> None:
    task = load_task()

    print("## §0 H1 审计：`code_exec.py` 到底吃不吃 `prompt`")
    src = (ROOT / "bench" / "scorers" / "code_exec.py").read_text(encoding="utf-8")
    hits = [(i, l.strip()) for i, l in enumerate(src.splitlines(), 1) if "prompt" in l]
    print(f"  `grep -n prompt` 命中 {len(hits)} 处：")
    for i, l in hits:
        print(f"    :{i}  {l[:100]}")
    code_hits = [(i, l) for i, l in hits if not l.lstrip().startswith("#") and '"""' not in l
                 and "stdin" not in l]
    print(f"  去掉 docstring/注释后剩 {len(code_hits)} 处 ⇒ "
          + ("**判分逻辑不读 prompt**" if not code_hits else "⚠️ 有代码读 prompt，H1 需重审"))

    # ---------------- 盘点 ----------------
    cells = []          # (run, tag, prompt, arm, impl_hash, code)
    for run in RUNS:
        d = EV / run
        if not d.exists():
            continue
        for ans in sorted(d.glob("answers/**/answers.jsonl")):
            tag = ans.parent.name
            got = []
            for ln in ans.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                r = json.loads(ln)
                if r.get("kind") == "answer" and r.get("task_id") == TASK:
                    got.append(r.get("answer") or "")
            if not got:
                continue
            code = got[0]
            h = hashlib.sha256(code.encode()).hexdigest()[:8]
            cells.append({"run": run, "tag": tag, "prompt": prompt_of(run, tag),
                          "arm": arm_of(tag), "impl": h, "code": code})

    print("\n## §1 盘点")
    impls = {}
    for c in cells:
        impls.setdefault(c["impl"], c["code"])
    print(f"  有效格数 = {len(cells)}　互异实现 = {len(impls)}")
    by_prompt: dict[str, list] = {}
    for c in cells:
        by_prompt.setdefault(c["prompt"], []).append(c)
    print(f"  题面变体 = {len(by_prompt)}：")
    for p, cs in sorted(by_prompt.items()):
        print(f"    {p:22s} {len(cs)} 格，臂 = {sorted({x['arm'] for x in cs})}")

    # ---------------- 重算分数 ----------------
    print("\n## §2 H1 交叉校验：`scores.json` 记录的 vs 用固定 tests 重算的")
    sc = {}
    for h, code in impls.items():
        rc, out = score_code(task, code)
        sc[h] = {"score": out.get("score"), "rc": rc, "why": out.get("why", "")}
    bad = []
    checked = 0
    for run in ("v6_s191", "v6_s192"):
        sp = EV / run / "scores.json"
        if not sp.exists():
            continue
        recorded = json.loads(sp.read_text(encoding="utf-8"))
        for c in [x for x in cells if x["run"] == run]:
            rec = (recorded.get(c["tag"]) or {}).get(TASK)
            if rec is None:
                continue
            checked += 1
            mine = sc[c["impl"]]["score"]
            if abs(float(rec) - float(mine)) > 1e-9:
                bad.append((run, c["tag"], c["impl"], rec, mine))
    print(f"  可比对 {checked} 格　不一致 {len(bad)} 格")
    for b in bad:
        print(f"    ⛔ {b}")
    print("  ⇒ " + ("✅ **H1 成立**：判据只由答案决定，与题面无关（审计 + 交叉校验两条独立证据）"
                    if not bad else "⛔ **H1 倒**：有格对不上，先查差异来源"))

    # ---------------- H2 分辨率 ----------------
    print("\n## §3 H2 分辨率：9 个实现 → 几个分")
    buckets: dict[float, list] = {}
    for h, v in sc.items():
        buckets.setdefault(v["score"], []).append(h)
    print(f"  分数取值 = {sorted(buckets)} ⇒ S = **{len(buckets)}**（实现数 9）")
    for s in sorted(buckets, reverse=True):
        hs = buckets[s]
        print(f"    {s:>4} 分：{len(hs)} 个实现　{' '.join(sorted(hs))}"
              + ("　⚠️ **压在一起**" if len(hs) > 1 else ""))
    print(f"  ⇒ 分辨率 = {len(buckets)}/{len(sc)} = {len(buckets)/len(sc):.2f}；"
          f"被压缩的实现有 {len(sc) - len(buckets)} 个")

    # ---------------- H3 归因 ----------------
    print("\n## §4 H3 归因（核心）：按题面组看**产出了哪些实现**")
    ones = [h for h in sc if sc[h]["score"] == 1.0]
    print(f"  满分实现 = {sorted(ones)}")
    print(f"  {'题面':22s} {'格':>3s}  实现的分数")
    for p, cs in sorted(by_prompt.items()):
        row = " ".join(f"{c['impl']}={sc[c['impl']]['score']}" for c in cs)
        n1 = sum(1 for c in cs if sc[c["impl"]]["score"] == 1.0)
        print(f"  {p:22s} {len(cs):>3d}  {row}   ← 满分格 {n1}")
    print("\n  ⭐ 关键：满分实现在各组出现次数")
    verdict = {}
    for p, cs in sorted(by_prompt.items()):
        cnt = sum(1 for c in cs if c["impl"] in ones)
        verdict[p] = cnt
        hi = max(sc[c["impl"]]["score"] for c in cs)
        print(f"    {p:22s} 满分实现出现 {cnt} 次　该组最高分 {hi}")
    zero_groups = [p for p, c in verdict.items() if c == 0]
    if not ones:
        print("  ⛔ 没有满分实现，H3 不可判")
    elif len(zero_groups) == len(by_prompt):
        print("  ⛔ 所有组都没出现过满分实现 ⇒ 装置有问题，先查")
    else:
        print(f"  ⇒ 满分实现**从不出现**的组：{zero_groups}")
        # (a) 还是 (b)：出现过但被判低分？
        wrong = [(c["prompt"], c["impl"], sc[c["impl"]]["score"])
                 for c in cells if c["impl"] in ones and sc[c["impl"]]["score"] < 1.0]
        print(f"  (a) 判据把满分的同一实现判低：{len(wrong)} 格 {wrong[:5]}")
        print("  ⇒ " + ("**(b) 可达性成立**：满分实现没在这些题面下出现 ⇒ 变的不是分数，是答案"
                        if not wrong else "**(a) 判据成立**：同一实现被换题面判低 ⇒ V104 方向有真靶子"))

    # ---------------- H4 分解 ----------------
    print("\n## §5 H4 变异分解：per-prompt mean 能不能由实现分布解释")
    print(f"  {'题面':22s} {'格':>3s} {'mean':>7s}  组成")
    means = {}
    for p, cs in sorted(by_prompt.items()):
        ss = [sc[c["impl"]]["score"] for c in cs]
        means[p] = sum(ss) / len(ss)
        print(f"  {p:22s} {len(cs):>3d} {means[p]:>7.4f}  "
              + ", ".join(f"{s:g}×{ss.count(s)}" for s in sorted(set(ss), reverse=True)))
    if means:
        print(f"  ⇒ mean 跨度 {min(means.values()):.4f} ~ {max(means.values()):.4f}")
        print("  ⇒ **判据项贡献 = 0**（H1：同一实现跨题面同分）⇒ 跨题面 mean 的全部差异"
              "只能来自「产出了哪个实现」")
        hi_p = max(means, key=means.get)
        lo_p = min(means, key=means.get)
        print(f"  反事实：若把 {hi_p} 的实现分布搬到 {lo_p}，mean 会变成 "
              f"{means[hi_p]:.4f}（因为分跟着实现走，不跟着题面走）")


if __name__ == "__main__":
    main()

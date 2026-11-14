#!/usr/bin/env python3
"""V107：跨任务搬运 —— 把「迁移」与「抄」分开。

零模型、零网络。判据 = `bench/scorers/code_exec.py`（每候选一次子进程）。

§0 物料：别的题的互异实现行（**显式排除** g7d3 自己的实现）
§1 M2 校准：同题供体必须复现 V106 的 2 个命中
§2 M1 跨题穷举整行替换
§3 M3 破坏性分布
§4 M4 命中时逐字比对（vs 满分实现的 L4）
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

EV = pathlib.Path(__file__).resolve().parent.parent
ROOT = EV.parent.parent
TASK = "g7d3_fmt_amount"
SUITE = ROOT / "bench" / "heldout" / "g7j_n4_heldout.jsonl"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
WORKERS = 3

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
    """(task_id, impl_hash) -> 裸代码，扫全部档案。"""
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


def compiles(code: str) -> bool:
    try:
        compile(code, "<m>", "exec")
        return True
    except SyntaxError:
        return False


def main() -> None:
    task = load_task()
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

    pool = harvest()
    same = {k: v for k, v in pool.items() if k.startswith(TASK + "::")}
    other = {k: v for k, v in pool.items() if not k.startswith(TASK + "::")}
    target_h = "075b4099"
    target = same[f"{TASK}::{target_h}"] if f"{TASK}::{target_h}" in same else None
    if target is None:
        for k, v in same.items():
            if hashlib.sha256(v.encode()).hexdigest()[:8] == target_h:
                target = v
                break
    assert target is not None

    print("## §0 物料")
    print(f"  档案里 (题,实现) 对 = {len(pool)}　其中本题 {len(same)} 个、**别的题 {len(other)} 个**")
    print(f"  目标 = `{target_h}`（{score(target)} 分），{len(target.splitlines())} 行：")
    for i, l in enumerate(target.splitlines()):
        print(f"    L{i}: {l}")
    perfect_lines = set()
    for k, v in same.items():
        h = k.split("::")[1]
        if score(v) == 1.0:
            for l in v.splitlines():
                perfect_lines.add(l)
    print(f"  满分实现的**互异行** {len(perfect_lines)} 条（M4 逐字比对用）")

    # ---------- §1 M2 校准 ----------
    print("\n## §1 M2 校准：同题供体必须复现 V106 的 2 个命中")
    tlines = target.splitlines(keepends=True)
    same_lines = {}
    for k, v in same.items():
        h = k.split("::")[1]
        if h == target_h:
            continue
        for i, l in enumerate(v.splitlines()):
            same_lines.setdefault(l, []).append(f"{h}:L{i}")
    # ⚠️ 两种计数必须分开：**组合数**（含两个供体给同一行文本）vs **互异候选数**
    combo = []
    for j, tl in enumerate(tlines):
        for dl, srcs in same_lines.items():
            if dl == tl.rstrip("\n") or dl + "\n" == tl:
                continue
            for s in srcs:
                combo.append((f"L{j}<-{s}", "".join(tlines[:j] + [dl + "\n"] + tlines[j + 1:])))
    combo = [(lab, c) for lab, c in combo if compiles(c)]
    uniq = {}
    for lab, c in combo:
        uniq.setdefault(c, []).append(lab)
    with ThreadPoolExecutor(WORKERS) as ex:
        sc = list(ex.map(score, [c for _l, c in combo]))
    hits = [(lab, s) for (lab, _c), s in zip(combo, sc) if s == 1.0]
    hit_text = {(lab, s) for (lab, c), s in zip(combo, sc) if s == 1.0}
    win_lines = []
    for c, labs in uniq.items():
        if any((l, 1.0) in hit_text for l in labs):
            win_lines.append((labs, c.splitlines()[-1]))
    print(f"  同题**组合** {len(combo)} 个（含两个供体给同一行）"
          f"　**互异候选** {len(uniq)} 个　命中组合 {len(hits)} 个、命中互异候选 {len(win_lines)} 个")
    for labs, line in win_lines:
        print(f"    命中行 {line!r} ← {labs}")
    print(f"    {hits}")
    print("  ⇒ " + ("✅ 复现 V106 的 2 个**组合**（但只有 "
                    f"{len(win_lines)} 个**互异候选**，因为两个供体的 L4 逐字相同）"
                    if len(hits) >= 2 else f"⛔ 组合数 {len(hits)} < 2，未复现，需查口径"))

    # ---------- §2 M1 跨题 ----------
    print("\n## §2 M1 跨题穷举（捐赠者**只允许来自别的题**）")
    donor_lines: dict[str, list] = {}
    for k, v in other.items():
        tid = k.split("::")[0]
        for i, l in enumerate(v.splitlines()):
            donor_lines.setdefault(l, []).append(f"{tid}:L{i}")
    print(f"  别的题互异行 = {len(donor_lines)} 条（来自 {len(other)} 个实现）")
    combos = []
    for j, tl in enumerate(tlines):
        for dl, srcs in donor_lines.items():
            if dl + "\n" == tl:
                continue
            combos.append((f"L{j}<-{srcs[0]}", dl, "".join(tlines[:j] + [dl + "\n"] + tlines[j + 1:])))
    kept = [(lab, dl, c) for lab, dl, c in combos if compiles(c)]
    print(f"  组合 {len(combos)} 个，compile 通过 {len(kept)} 个")
    with ThreadPoolExecutor(WORKERS) as ex:
        sc = list(ex.map(score, [c for _l, _d, c in kept]))
    pairs = [(lab, dl, s) for (lab, dl, _c), s in zip(kept, sc) if s is not None]
    dist: dict[float, int] = {}
    for _l, _d, s in pairs:
        dist[s] = dist.get(s, 0) + 1
    best = max((s for _l, _d, s in pairs), default=None)
    print(f"  分数分布 {dict(sorted(dist.items(), reverse=True))}　最高 = {best}")

    # ---------- §3 M3 破坏性 ----------
    print("\n## §3 M3 破坏性")
    base = score(target)
    lo = sum(1 for _l, _d, s in pairs if s < base)
    eq = sum(1 for _l, _d, s in pairs if s == base)
    hi = sum(1 for _l, _d, s in pairs if s > base)
    print(f"  起点 {base}：低于 {lo}（{lo/len(pairs):.1%}）　等于 {eq}　高于 {hi}")

    # ---------- §4 M4 ----------
    print("\n## §4 M4 命中逐字比对")
    wins = [(lab, dl, s) for lab, dl, s in pairs if s == 1.0]
    if not wins:
        print("  ⛔ 跨题命中 = **0** ⇒ 无逐字可比对")
        print("  ⇒ **M1 成立：跨题搬运到不了 1.0。**")
        print("  ⇒ §174.4 第 2 条降级：「块级有梯度」只在**同题供体**上成立 ⇒ 本质是「抄同一题的更好实现」。")
    else:
        print(f"  命中 {len(wins)} 条：")
        for lab, dl, s in wins:
            same_as_perfect = dl in perfect_lines
            print(f"    {lab} ⇒ {s}")
            print(f"      捐赠行原文: {dl!r}")
            print(f"      与满分实现的某行逐字相同? {same_as_perfect} ⇒ "
                  + ("**抄**" if same_as_perfect else "**迁移**"))

    out = {"target": target_h, "base": base, "same_hits": len(hits),
           "cross_n": len(pairs), "cross_dist": {str(k): v for k, v in dist.items()},
           "cross_best": best, "cross_wins": len(wins),
           "lo": lo, "eq": eq, "hi": hi}
    (EV / "v6_s196" / "cross.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

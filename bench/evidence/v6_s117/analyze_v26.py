#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V26 判决：V25 那 +16 的通路到底是什么？

只读落盘证据，不起模型、不调内核（除第 ⑥ 节的离线 guidance 复算）。
判据写死在 PREREG（`bench/evidence/v6_s117/PREREG.md`），本脚本**只执行不解释**。

用法：analyze_v26.py [工作目录，默认 /tmp/v26]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent.parent.parent
BLOCKS = ("[目标]", "[工作记忆]", "[语义记忆]", "[外部评审]", "[轨迹]", "[第")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def load_states(w: Path, tag: str) -> dict[tuple[str, int], str]:
    p = w / f"states_{tag}.jsonl"
    out: dict[tuple[str, int], str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        out[(str(d["task"]), int(d["it"]))] = str(d["state"])
    return out


def load_scores(w: Path, tag: str) -> dict[str, float]:
    p = w / f"bench_{tag}" / "runs.jsonl"
    if not p.exists():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        return {}
    per = rows[-1]["report"]["per_task"]
    return {str(t["id"]): float(t.get("score") or 0) * 16 for t in per}


def block_of(lines: list[str], i: int) -> str:
    """第 i 行属于哪个块：从 i 往前找最近的行首标记。"""
    for j in range(i, -1, -1):
        if lines[j].startswith(BLOCKS):
            return lines[j].split(" ")[0].split("：")[0]
    return "(无块)"


def first_diff(a: str, b: str) -> tuple[int, str, str, str]:
    """返回 (行号, 块名, a 该行, b 该行)。行数不同时用 "(无此行)" 占位。"""
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "(无此行)"
        y = lb[i] if i < len(lb) else "(无此行)"
        if x != y:
            blk = block_of(la, i) if i < len(la) else block_of(lb, i)
            return i, blk, x, y
    return -1, "", "", ""


def main(argv: list[str]) -> int:
    w = Path(argv[1] if len(argv) > 1 else "/tmp/v26")
    print(f"===== V26 通路取证  {w} =====")

    tags = ["d2_r1", "d2_r2", "f2_r1", "f2_r2"]
    S = {t: load_states(w, t) for t in tags}
    SC = {t: load_scores(w, t) for t in tags}

    print("\n--- ① 读数总览（得分 = 逐题 ×16）---")
    tasks = []
    for t in tags:
        for (tid, _it) in S[t]:
            if tid not in tasks:
                tasks.append(tid)
    print(f"{'task':6s}" + "".join(f"{t:>9s}" for t in tags))
    for tid in tasks:
        row = f"{tid:6s}"
        for t in tags:
            v = SC[t].get(tid)
            row += f"{v:9.0f}" if v is not None else f"{'-':>9s}"
        print(row)
    print(f"{'Σ':6s}" + "".join(f"{sum(SC[t].values()):9.0f}" for t in tags))

    print("\n--- ② 每轮 state 覆盖（步数 = 每题 _state() 次数）---")
    for t in tags:
        n_task = len({tid for (tid, _i) in S[t]})
        n_step = len(S[t])
        print(f"  {t:7s} 题 {n_task}  步 {n_step}  文件 {'有' if S[t] else '**缺**'}")

    # ---------------- 判决 1：f2 正控 ----------------
    print("\n--- ③ 正控 f2（每轮重置 ⇒ 同起点，state 必须逐字节相同）---")
    keys = sorted(set(S["f2_r1"]) | set(S["f2_r2"]))
    diff_keys = [k for k in keys if S["f2_r1"].get(k) != S["f2_r2"].get(k)]
    print(f"  共同键 {len([k for k in keys if k in S['f2_r1'] and k in S['f2_r2']])} 个；"
          f"逐字节不同 {len(diff_keys)} 个")
    for k in diff_keys[:6]:
        i, blk, x, y = first_diff(S["f2_r1"].get(k, ""), S["f2_r2"].get(k, ""))
        print(f"    ✗ {k[0]} 步{k[1]} 首差异行 {i} 块 {blk}")
        print(f"        r1: {x[:160]}")
        print(f"        r2: {y[:160]}")
    J0 = bool(diff_keys)

    # ---------------- 判决 2/3/4：d2 ----------------
    print("\n--- ④ 主臂 d2（累积，echo=0 ⇒ reward 不进 prompt）---")
    keys = sorted(set(S["d2_r1"]) | set(S["d2_r2"]))
    d2_diff = [k for k in keys if S["d2_r1"].get(k) != S["d2_r2"].get(k)]
    score_changed = [tid for tid in tasks if SC["d2_r1"].get(tid) != SC["d2_r2"].get(tid)]
    print(f"  分数变化的题：{score_changed if score_changed else '无'}")
    for tid in score_changed:
        print(f"    · {tid}: {SC['d2_r1'].get(tid, 0):.0f} → {SC['d2_r2'].get(tid, 0):.0f}")
    print(f"  state 不同的 (task, it) 共 {len(d2_diff)} 个")
    for k in d2_diff[:12]:
        a, b = S["d2_r1"].get(k, ""), S["d2_r2"].get(k, "")
        i, blk, x, y = first_diff(a, b)
        print(f"    ✗ {k[0]} 步{k[1]}  len {len(a)}→{len(b)}  首差异行 {i} 块 {blk}")
        print(f"        r1: {x[:200]}")
        print(f"        r2: {y[:200]}")

    # ---------------- 块清单 ----------------
    print("\n--- ⑤ 块清单（每题最后一步）---")
    for tid in tasks:
        for t in ("d2_r1", "d2_r2"):
            its = [i for (x, i) in S[t] if x == tid]
            if not its:
                print(f"  {tid:6s} {t:7s} （无）")
                continue
            st = S[t][(tid, max(its))]
            bl = [ln.split(" ")[0].split("：")[0] for ln in st.splitlines()
                  if ln.startswith(BLOCKS)]
            print(f"  {tid:6s} {t:7s} 步{len(its):2d} {len(st):5d}字 {sha(st)} 块 {bl}")

    # ---------------- ⑥ 离线复算 guidance（echo=0 必须为空） ----------------
    print("\n--- ⑥ 离线复算 guidance（d2 用 echo=0 ⇒ 必须恒为空串）---")
    try:
        sys.path.insert(0, str(V1))
        from mw.features import feature_set            # noqa: E402
        from mw.memory import MemoryStore              # noqa: E402
        from mw.worldmodel import WorldModel           # noqa: E402
        suite = [json.loads(x) for x in
                 (V1 / "bench/specgen/tasks_v8.jsonl").read_text(encoding="utf-8").splitlines()
                 if x.strip()]
        db = w / "mem_d2" / "memory.db"
        if db.exists():
            st = MemoryStore(str(db))
            for echo in (0, 1):
                wm = WorldModel(st, llm=None, reward_echo=echo)
                lens = []
                for t in suite:
                    g = wm.guidance(action=t["prompt"], features=feature_set(t),
                                    task_id=t["id"])
                    lens.append(f"{t['id']}={len(g)}")
                print(f"  echo={echo}: " + " ".join(lens))
            st.close()
        else:
            print("  ⚠️ mem_d2/memory.db 不存在，跳过")
    except Exception as e:                                       # noqa: BLE001
        print(f"  ⚠️ 复算失败 {type(e).__name__}: {e}")

    # ---------------- 判决 ----------------
    print("\n" + "=" * 62)
    print("判据（PREREG §2，按顺序取第一条命中）")
    if J0:
        print("  ⇒ J0 装置不可重复：f2 两轮同起点，state 却逐字节不同")
        print("     ⛔ 本轮不出归因；V25 的 noise_floor=0 一并作废。")
    elif not score_changed:
        print("  ⇒ J3 未复现：d2 两轮没有任何题分数变化（本时段不走那两个吸引子）")
        print("     ⛔ 归因问题顺延；不许拿 V25 旧读数硬套。")
    elif d2_diff:
        blocks = {}
        for k in d2_diff:
            _i, blk, _x, _y = first_diff(S["d2_r1"].get(k, ""), S["d2_r2"].get(k, ""))
            blocks[blk] = blocks.get(blk, 0) + 1
        print(f"  ⇒ J1 定位成功：{len(d2_diff)} 个 (task,it) 的 state 不同，首差异块分布 {blocks}")
        print("     ⛔ 只报事实，不许写「所以内核学会了 X」。")
    else:
        print("  ⇒ J2 反证：分数变了，但六题所有步 state 逐字节全同")
        print("     ⇒ 同 prompt 同 T=0 却不同答案 ⇒ 采样不是确定性的，V25 的确定性结论要重写。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

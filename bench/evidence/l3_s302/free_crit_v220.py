#!/usr/bin/env python3
"""V220 免费复算：真实账本上的「跨轮自省」块，逐字节长什么样。

⛔ 不改判据（见 PREREG_v220.md §8 的跑前修订）。
⭐ 关键：**不重写逻辑**。直接调 `run._critique_snapshot`（第②遍用的就是它）
   与 `CritiqueLedger.admissible`，再照 `core.py:2319` 的 pick 规则取块。
   抄一遍就是「证明抄写正确」，不是「证明实现正确」（V31 自踩⑩）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from mw.critique import CritiqueLedger            # noqa: E402
from run import _critique_snapshot                # noqa: E402

D = Path("bench/evidence/l3_s302")
CRIT = D / "crit_v220.jsonl"

SUITE = [json.loads(l) for l in (D / "suite_sub2.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
IDS = [t["id"] for t in SUITE]


def block_for(snap: CritiqueLedger, tid: str, scorer_sha: str, suite_sha: str) -> list:
    """复刻 `core.py:2315-2319`：self_c 一条 + 别的题尾 2 条（上限 3）。"""
    crit = snap.admissible(scorer_sha=scorer_sha, suite_sha=suite_sha,
                           exclude_task_id=tid)
    self_c = [c for c in crit if c.task_id == tid]
    others = [c for c in crit if c.task_id != tid]
    # ⚠️ `allow_self=False` 时 self_c 恒空 ⇒ pick 就是 crit[-3:]（旧口径）
    pick = ([self_c[-1]] + others[-(3 - 1):]) if self_c else crit[-3:]
    return pick


def main() -> None:
    print("=" * 72)
    print("V220 免费复算：第①遍真实写出的账本 ⇒ 第②遍会拿到的块")
    print("=" * 72)

    if not CRIT.exists():
        print(f"⛔ 账本不存在：{CRIT}")
        raise SystemExit(1)
    rows = [json.loads(l) for l in CRIT.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"\n账本 {len(rows)} 行")
    for r in rows:
        print(f"  {r['task_id']:24s} score={r['score']:.4f}  why[:70]={r['why'][:70]!r}")

    if not rows:
        print("⛔ 空账本 ⇒ 本轮无法回答 P6（判 UNKNOWN）")
        raise SystemExit(2)

    ss, su = rows[0]["scorer_sha"], rows[0]["suite_sha"]
    print(f"\n口径  scorer_sha={ss}  suite_sha={su}")
    # 口径一致性：不同行必须同 sha，否则 admissible 会分叉
    bad = [(r["task_id"], r["scorer_sha"] == ss, r["suite_sha"] == su) for r in rows]
    print("  每行同口径：" + ("✅" if all(a and b for _, a, b in bad) else f"⛔ {bad}"))

    crit = CritiqueLedger(CRIT)
    # ⛔ 必须显式 load：`__init__` 只存 path（V220 首版漏了这一句 ⇒ 快照 0 条 ⇒ P6 假负）
    n_loaded = crit.load()
    print(f"\n账本 load() 回读 {n_loaded} 条（by_key={len(crit.by_key)}）")
    snap = _critique_snapshot(crit, "prev-self", None, "shape")
    print(f"\n快照 allow_self={snap.allow_self}  why_mode={snap.why_mode}  条数={len(snap.by_key)}")

    p6_ok = True
    for tid in IDS:
        pick = block_for(snap, tid, ss, su)
        txt = "[外部评审] " + " ｜ ".join(c.as_line()[:140] for c in pick) if pick else ""
        own = [c for c in pick if c.task_id == tid]
        print(f"\n  --- {tid} ---  {len(pick)} 条  {len(txt)} 字")
        if txt:
            print(f"    {txt}")
        print(f"    含本题那条：{len(own)}  首条=本题：{bool(pick) and pick[0].task_id == tid}")
        print(f"    含 assert：{'assert' in txt}   why 被 shape 改写：{snap.why_rewritten} / 清空 {snap.why_emptied}")

        if tid.startswith("t01"):
            cond = {
                "(a) 非空": bool(txt),
                "(b) 以 [外部评审] 开头": txt.startswith("[外部评审]"),
                "(c) 含 通过 与 /6": ("通过" in txt and "/6" in txt),
                "(d) 首条是本题": bool(pick) and pick[0].task_id == tid,
                "(e) 首条通过数 < 6": False,
                "(f) 不含 assert": "assert" not in txt,
            }
            if pick:
                import re
                m = re.search(r"通过\s*(\d+)\s*/\s*(\d+)", pick[0].as_line())
                if m:
                    cond["(e) 首条通过数 < 6"] = int(m.group(1)) < int(m.group(2))
            p6_ok = all(cond.values())
            print("    P6 分解：")
            for k, v in cond.items():
                print(f"      {k}: {'✅' if v else '⛔'}")

    print("\n" + "=" * 72)
    print(f"P6（免费复算）= {'PASS' if p6_ok else 'FAIL'}")
    print("  说明：这证明「链路能通」，不证明「模型会用它」（后者引 V219c/d）。")
    print("=" * 72)


if __name__ == "__main__":
    main()

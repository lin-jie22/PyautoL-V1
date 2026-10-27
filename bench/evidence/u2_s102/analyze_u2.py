#!/usr/bin/env python3
"""§72 U2 的读数器：`verify_static` 1（报名字）vs 2（盲报），`tasks_v4`。

预注册见同目录 `PREREG.md`（写于跑批之前）。本脚本**只做读数与判负检查**，
不做裁决之外的解释；每一条结论都必须能指回下面某个数。

⭐ 自检在 `S0`：三个检测器各带一条**反证**（负例来自 §71 T1b 的真实读数），
   自检不过 ⇒ 直接退出，不打印任何 Δ 或结论（§70.4 ③ 的教训）。

用法：
  python3 bench/evidence/u2_s102/analyze_u2.py \
      --a /tmp/v1_s102_bench_a1_v4 --b /tmp/v1_s102_bench_a2_v4 \
      --a-log /tmp/v1_s102_run_a1_v4.log --b-log /tmp/v1_s102_run_a2_v4.log \
      --sub-a /tmp/v1_s102_bench_a1_at04 --sub-b /tmp/v1_s102_bench_a2_at04
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# ── 检测器 ──────────────────────────────────────────────────────────────────
#: 闸的两种载荷：`=1` 会点名，`=2` 不会。判「模型有没有被告知名子」只看这一处。
_REVEAL = re.compile(r"未定义的名字\s*'([A-Za-z_]\w*)'")
_BLIND = "不提供细节"

#: 缺 `import os` 的判定：出现 `os.` 却没有把 `os` 绑进命名空间。
_BINDS_OS = re.compile(r"^\s*(?:import\s+os\b|from\s+os\s+import\b|import\s+os\s*,\s*)",
                       re.M)
_USES_OS = re.compile(r"\bos\.")


def gate_payload(msg: str) -> str:
    """返回 'revealed:<name>' / 'blind' / 'other'。"""
    m = _REVEAL.search(msg)
    if m:
        return f"revealed:{m.group(1)}"
    if _BLIND in msg:
        return "blind"
    return "other"


def os_bound(src: str) -> bool:
    return bool(_BINDS_OS.search(src))


def os_used(src: str) -> bool:
    return bool(_USES_OS.search(src))


# ── S0 自检（每条带反证；负例取自 §71 T1b 的真实读数）──────────────────────
SELFTEST = [
    ("闸载荷 =1 点名", "1/5 项未过：未定义的名字 'os'（在 f 内被引用，模块里既没有 import 也没有赋值）",
     "revealed:os"),
    ("闸载荷 =2 盲报", "1/5 项未过：自检未通过（不提供细节）", "blind"),
    ("闸载荷 其他", "1/5 项未过：缺少 def f(...)", "other"),
    # ⛔ 反证：这份 383 字答案**真的**缺 import os（§71 T1b 三条 rep 逐字节相同）
    ("真实缺 import 的答案", "T1B_AT04", "no_bind"),
    ("补上一行的答案", "```python\nimport os\n\ndef f(p):\n    os.makedirs(p)\n```", "bind"),
    ("from os import 也算绑定", "from os import makedirs\ndef f(p):\n    makedirs(p)\n", "bind"),
    ("只写注释不算绑定", "# import os\ndef f(p):\n    os.makedirs(p)\n", "no_bind"),
]


def selftest(t1b_at04: str) -> bool:
    ok = True
    print("=== S0 检测器自检（7 例，含 1 条真实负例）===")
    for name, src, want in SELFTEST:
        if src == "T1B_AT04":
            got = "bind" if os_bound(t1b_at04) else "no_bind"
        elif want in ("revealed:os", "blind", "other"):
            got = gate_payload(src)
        else:
            got = "bind" if os_bound(src) else "no_bind"
        good = got == want
        ok &= good
        print(f"  {'PASS' if good else '**FAIL**':8s} {name:22s} got={got:12s} want={want}")
    print("自检全通过" if ok else "⛔ 自检失败 ⇒ 不打印结论")
    return ok


# ── 台账读取 ────────────────────────────────────────────────────────────────
def load(bdir: Path) -> dict:
    runs = [json.loads(l) for l in open(bdir / "runs.jsonl", encoding="utf-8")]
    run = runs[-1]
    per = {(t["id"], t["rep"]): t for t in run["report"]["per_task"]}
    ans = [json.loads(l) for l in open(bdir / "answers.jsonl", encoding="utf-8")
           if json.loads(l).get("kind") == "answer"]
    return {"run": run, "per": per, "ans": ans}


def log_stats(path: Path) -> dict:
    """从 run.log 里数闸的读数，**按尝试块**聚合。

    ⚠️ 机会数按**尝试**算（每次尝试至多一次机会 = 该次尝试的第一次静态失败），
    这是 `PREREG.md` 第三节写定的口径（「3 次尝试 / 9 次闸跑」）。
    ⛔ 下游闸跑数（打回 + 收尾未过闸）**不是**机会数：治疗生效时它自己会变小，
       拿它当分母等于让分母随结果变（§66 的自踩就是这个形态）。
    """
    txt = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict] = []
    cur: dict | None = None
    for line in txt.splitlines():
        if "▶ 任务开始" in line:
            m = re.search(r"`([A-Za-z_]\w*)`", line)
            cur = {"task": m.group(1) if m else "?", "fails": [],
                   "retries": 0, "closeout_fail": 0, "closeout_pass": 0}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        if "自检未过（第" in line:
            cur["retries"] += 1
            cur["fails"].append(gate_payload(line))
        elif "收尾交付**未过闸**" in line:
            cur["closeout_fail"] += 1
            cur["fails"].append(gate_payload(line))
        elif "收尾交付过闸" in line:
            cur["closeout_pass"] += 1
    payloads: dict[str, int] = {}
    for b in blocks:
        for p in b["fails"]:
            payloads[p] = payloads.get(p, 0) + 1
    return {
        "attempts": len(blocks),
        "blocks": blocks,
        #: 机会数 = 至少失败过一次闸的**尝试**数（PREREG §三）
        "opportunities": sum(1 for b in blocks if b["fails"]),
        #: 咬合数 = **机会里**「首次失败之后重写就过闸」的次数（该尝试再无第二次失败）
        #: ⚠️ 不用「收尾过闸」当判据：交付可以走主循环（不打收尾），那样就没有收尾行。
        "engaged": sum(1 for b in blocks if len(b["fails"]) == 1),
        "retries": sum(b["retries"] for b in blocks),
        "closeout_fail": sum(b["closeout_fail"] for b in blocks),
        "closeout_pass": sum(b["closeout_pass"] for b in blocks),
        "payloads": payloads,
    }


def task_scores(d: dict, reps: int) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for (tid, r), t in sorted(d["per"].items()):
        if r <= reps:
            out.setdefault(tid, []).append(t["score"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="/tmp/v1_s102_bench_a1_v4")
    ap.add_argument("--b", default="/tmp/v1_s102_bench_a2_v4")
    ap.add_argument("--a-log", default="/tmp/v1_s102_run_a1_v4.log")
    ap.add_argument("--b-log", default="/tmp/v1_s102_run_a2_v4.log")
    ap.add_argument("--sub-a", default="/tmp/v1_s102_bench_a1_at04")
    ap.add_argument("--sub-b", default="/tmp/v1_s102_bench_a2_at04")
    ap.add_argument("--t1b", default=str(ROOT / "bench/evidence/artifact_run_s101/t1b_fixed_ruler"))
    args = ap.parse_args()

    t1b = load(Path(args.t1b))
    t1b_at04 = next(a["answer"] for a in t1b["ans"] if a["task_id"] == "at04")
    if not selftest(t1b_at04):
        return 2

    A, B = load(Path(args.a)), load(Path(args.b))
    la, lb = log_stats(Path(args.a_log)), log_stats(Path(args.b_log))

    print("\n=== S1 接线层（预注册 N1）===")
    for nm, d, l in (("A =1", A, la), ("B =2", B, lb)):
        r = d["run"]["report"]
        print(f"  {nm}: attempted={r['attempted']} recorded={r['recorded']} "
              f"skipped_no_answer={r['skipped_no_answer']} failed_scorer={r['failed_scorer']} "
              f"| 日志尝试块={l['attempts']}")

    print("\n=== S2 口径（唯一自变量 = verify_static）===")
    for nm, d in (("A", A), ("B", B)):
        c = d["run"]["caliber"]
        lim = dict(c.get("limits", {}))
        print(f"  {nm}: sig={d['run']['caliber_sig']} suite={c['suite_sha'][:12]} "
              f"scorer={c['scorer_sha'][:12]} limits.verify_static={lim.get('verify_static')}")
    same_sig_t1b = B["run"]["caliber_sig"] == t1b["run"]["caliber_sig"]
    print(f"  B 与 §71 T1b 同 sig？{same_sig_t1b}（⇒ 可作跨 run 噪声复现，不是新口径）")
    da = {k: v for k, v in A["run"]["caliber"]["limits"].items()}
    db = {k: v for k, v in B["run"]["caliber"]["limits"].items()}
    diff = {k: (da[k], db[k]) for k in da if da[k] != db[k]}
    print(f"  两臂 limits 差异项 = {diff}")

    print("\n=== S3 分数 ===")
    ma, mb = A["run"]["measurement"], B["run"]["measurement"]
    print(f"  A（=1 报名字） mean={ma['mean']:.4f} std={ma['std']:.4f} "
          f"lo={ma['lo']:.4f} hi={ma['hi']:.4f}")
    print(f"  B（=2 盲报）   mean={mb['mean']:.4f} std={mb['std']:.4f} "
          f"lo={mb['lo']:.4f} hi={mb['hi']:.4f}")
    delta = ma["mean"] - mb["mean"]
    range_a = ma["hi"] - ma["lo"]
    range_b = mb["hi"] - mb["lo"]
    print(f"  Δ(A−B) = {delta:+.4f}  本 run 极差：A={range_a:.4f} B={range_b:.4f} "
          f"⇒ 判定线取 max={max(range_a, range_b):.4f}")
    print(f"  ≡ {delta * 128:+.1f} 格 / 128")

    print("\n=== S4 逐题 × 逐 rep ===")
    ka, kb = task_scores(A, 3), task_scores(B, 3)
    print("  题     A(r1,r2,r3)            B(r1,r2,r3)            Δ")
    for tid in ka:
        sa, sb = ka[tid], kb.get(tid, [])
        d = (sum(sa) / len(sa)) - (sum(sb) / len(sb))
        print(f"  {tid:5s} {' '.join(f'{x:.4f}' for x in sa):22s} "
              f"{' '.join(f'{x:.4f}' for x in sb):22s} {d:+.4f}")

    print("\n=== S5 机制读数（机会 = 触发静态判据的尝试；咬合 = 首次失败后重写即过闸）===")
    for nm, l in (("A =1", la), ("B =2", lb)):
        print(f"  {nm}: 机会={l['opportunities']} 咬合={l['engaged']} "
              f"| 下游闸跑：打回={l['retries']} 收尾未过闸={l['closeout_fail']} "
              f"收尾过闸={l['closeout_pass']} | 载荷={l['payloads']}")
        for i, b in enumerate(l["blocks"]):
            if b["fails"]:
                print(f"      块{i + 1:02d} {b['task']:14s} 失败{len(b['fails'])}次 "
                      f"{b['fails']} 打回={b['retries']} 收尾未过={b['closeout_fail']} "
                      f"⇒ {'重写后过闸' if len(b['fails']) == 1 else '仍失败'}")

    print("\n=== S6 交付物证据（at04：用了 os.* 却没有绑定 os）===")
    for nm, d in (("A =1", A), ("B =2", B)):
        rows = [a for a in d["ans"] if a["task_id"] == "at04"]
        uniq = {}
        for a in rows:
            uniq.setdefault(a["sha256"], a)
        bad = [s for s, a in uniq.items() if os_used(a["answer"]) and not os_bound(a["answer"])]
        good = [s for s, a in uniq.items() if os_bound(a["answer"])]
        print(f"  {nm}: 交付 {len(rows)} 条 / 唯一 {len(uniq)} 条 | 缺绑定 {len(bad)} 条 "
              f"| 已绑定 {len(good)} 条 | sha={[s[:8] for s in uniq]}")

    print("\n=== S7 增补块（只含 at04，repeat=6；⛔ 分数禁跨 sha 比）===")
    for nm, p in (("A-子集 =1", args.sub_a), ("B-子集 =2", args.sub_b)):
        if not Path(p).exists():
            print(f"  {nm}: 未生成")
            continue
        d = load(Path(p))
        m = d["run"]["measurement"]
        scores = [t["score"] for t in sorted(d["run"]["report"]["per_task"], key=lambda x: x["rep"])]
        rows = [a for a in d["ans"] if a["task_id"] == "at04"]
        uniq = {}
        for a in rows:
            uniq.setdefault(a["sha256"], a)
        good = sum(1 for a in uniq.values() if os_bound(a["answer"]))
        print(f"  {nm}: mean={m['mean']:.4f} 逐 rep={[f'{s:.4f}' for s in scores]} "
              f"唯一答案={len(uniq)} 已绑定 os={good}")

    print("\n=== S8 判负检查（预注册第四节）===")
    ra, rb = A["run"]["report"], B["run"]["report"]
    #: N2 读跑批时落盘的 oracle 日志（`run_u2.sh` 第一步），不在这里重跑
    oracle_ok = False
    for p in ("/tmp/v1_s102_oracle_v4.log", "/tmp/v1_s102_oracle_sub.log"):
        try:
            t = Path(p).read_text(encoding="utf-8", errors="replace")
            m = re.search(r"上界参照）@1x n=\d+ mean=([\d.]+)", t)
            oracle_ok = oracle_ok or (m is not None and float(m.group(1)) == 1.0)
        except OSError:
            pass
    checks = [
        ("N1 接线", ra["attempted"] == ra["recorded"] and ra["failed_scorer"] == 0
         and rb["attempted"] == rb["recorded"] and rb["failed_scorer"] == 0),
        ("N2 oracle=1.0（日志）", oracle_ok),
        ("N3 两臂都有机会", la["opportunities"] > 0 and lb["opportunities"] > 0),
        ("N4 机会数相等", la["opportunities"] == lb["opportunities"]),
        ("N5 |Δ| > 本 run 极差", abs(delta) > max(range_a, range_b)),
        ("N6 |Δ| > 3 格（0.0234）", abs(delta) > 3 / 128),
    ]
    for nm, ok in checks:
        print(f"  {'✅' if ok else '⛔'} {nm}")
    if all(ok for _, ok in checks) and delta > 0:
        print("  ⇒ 判据全部满足且方向为 A 优：H1 未被否证，H0 被否证")
    elif all(ok for _, ok in checks) and delta < 0:
        print("  ⇒ 判据全部满足且方向为 B 优：H0 未被否证，H1 被否证（保留默认 2）")
    else:
        print("  ⇒ ⛔ 有判负项不满足 ⇒ UNKNOWN；不许写成「略好」或「无效」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

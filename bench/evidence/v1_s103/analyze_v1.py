#!/usr/bin/env python3
"""§73 V1 的读数器：`verify_static` **0（关自检）** vs **2（盲报，默认）**，`tasks_v4`。

预注册见同目录 `PREREG.md`（写于跑批之前）。本脚本**只做读数与判负检查**；
每条结论都必须能指回下面某个数。

⭐ 本轮装置的特殊之处：`=0` 臂**结构上**不会触发闸（判据根本没派生），所以
   「机会数」不能只从日志数（会恒 0）。本脚本用**离线复算**补出第二个分母：

     · 真机会   = `=2` 臂日志里闸真的触发的**尝试**数
     · 潜在机会 = 交付物离线跑 `mw.verify.undefined_names` **会被拦**的份数

   ⛔ 潜在机会 ≠ 被救回的机会：它只说「如果关开着，这份交付物会被拦」，
      说不出「打回之后会不会变好」——后者只能由 `=2` 臂的分数承担。

⭐ 自检在 `S0`（7 例反证）；不过 ⇒ 直接退出，不打印任何 Δ 或结论（§70.4 ③）。

用法：
  python3 bench/evidence/v1_s103/analyze_v1.py
  python3 bench/evidence/v1_s103/analyze_v1.py --a /tmp/... --b /tmp/... --a-log ... --b-log ...
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import mw.verify as V  # noqa: E402  （只读；不改内核）

#: 闸的两种载荷：`=2` 盲报（本轮 B 臂）。
_BLIND = "不提供细节"
_REVEAL = re.compile(r"未定义的名字\s*'([A-Za-z_]\w*)'")


def gate_payload(msg: str) -> str:
    m = _REVEAL.search(msg)
    if m:
        return f"revealed:{m.group(1)}"
    if _BLIND in msg:
        return "blind"
    return "other"


def would_block(answer: str) -> tuple[str, ...] | None:
    """这份答案**如果**被 `undefined` 判据检查，会被哪些名字拦住。

    返回 `()` = 放行；非空 = 会被拦（元组是 `(作用域, 名字)`）；
    `None` = 不可判定（语法错等）—— 与内核同一条出口（不拦）。
    """
    code = V.extract_code(answer)
    return V.undefined_names(code)


# ── S0 自检：每一列都带一条反证 ────────────────────────────────────────────
_GOOD_WITH_IMPORT = "```python\nimport os\n\ndef f(p):\n    os.makedirs(p)\n```"
_GOOD_FROM_IMPORT = "```python\nfrom os import makedirs\ndef f(p):\n    makedirs(p)\n```"
_BAD_MISSING = "```python\ndef f(p):\n    os.makedirs(p)\n    return 1\n```"
_BAD_COMMENT_ONLY = "```python\n# import os\ndef f(p):\n    os.makedirs(p)\n```"
_BAD_SYNTAX = "```python\ndef f(:\n    os.makedirs(p)\n```"


def selftest(t1b_at04: str) -> bool:
    rows = [
        # (名字, 输入, 期望是否会被拦, 期望拦到的名字)
        ("补了 import os", _GOOD_WITH_IMPORT, False, ""),
        ("from os import", _GOOD_FROM_IMPORT, False, ""),
        ("真缺 import os", _BAD_MISSING, True, "os"),
        ("只写注释不算绑定", _BAD_COMMENT_ONLY, True, "os"),
        ("语法错 ⇒ 不可判定 ⇒ 不拦", _BAD_SYNTAX, False, ""),
        # ⭐ 真实负例：§71 T1b 三条 rep 逐字节相同的那份 at04 答案
        ("§71 T1b 真实 at04 答案", t1b_at04, True, "os"),
    ]
    ok = True
    print("=== S0-A 检测器自检（6 例，含 1 条真实负例）===")
    for name, src, want_block, want_name in rows:
        bad = would_block(src)
        got_block = bool(bad)
        got_name = bad[0][1] if bad else ""
        good = got_block == want_block and got_name == want_name
        ok &= good
        print(f"  {'PASS' if good else '**FAIL**':8s} {name:26s} "
              f"拦={got_block!s:5s} 名字={got_name or '-':6s} 期望={want_block}/{want_name or '-'}")

    print("=== S0-B 判据派生自检（0 档必须**没有** undefined 判据）===")
    goal = "写一个函数 f，只输出代码，函数名必须是 f"
    for st, want in ((0, False), (1, True), (2, True)):
        kinds = [c.kind for c in V.derive(goal, st)]
        has = "undefined" in kinds
        good = has == want
        ok &= good
        print(f"  {'PASS' if good else '**FAIL**':8s} static={st} kinds={kinds} 期望含 undefined={want}")
    # 反证：`=0` 真的**放行**那份缺 import 的答案，而 `=1/=2` 拦
    for st, want_ok in ((0, True), (1, False), (2, False)):
        v = V.run(_BAD_MISSING, V.derive(goal, st))
        good = v.ok == want_ok
        ok &= good
        print(f"  {'PASS' if good else '**FAIL**':8s} static={st} 放行={v.ok} 期望放行={want_ok} "
              f"载荷={gate_payload(v.report) if not v.ok else '-'}")

    print("自检全通过" if ok else "⛔ 自检失败 ⇒ 不打印结论")
    return ok


# ── 台账读取 ───────────────────────────────────────────────────────────────
def load(bdir: Path) -> dict:
    runs = [json.loads(l) for l in open(bdir / "runs.jsonl", encoding="utf-8")]
    run = runs[-1]
    per = {(t["id"], t["rep"]): t for t in run["report"]["per_task"]}
    ans = [json.loads(l) for l in open(bdir / "answers.jsonl", encoding="utf-8")
           if json.loads(l).get("kind") == "answer"]
    return {"run": run, "per": per, "ans": ans}


def log_stats(path: Path) -> dict:
    """按 `▶ 任务开始` 切块数闸读数（机会数按**尝试**算，PREREG §三）。"""
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
        "opportunities": sum(1 for b in blocks if b["fails"]),
        "engaged": sum(1 for b in blocks if len(b["fails"]) == 1),
        "retries": sum(b["retries"] for b in blocks),
        "closeout_fail": sum(b["closeout_fail"] for b in blocks),
        "closeout_pass": sum(b["closeout_pass"] for b in blocks),
        "payloads": payloads,
        #: N4 反证：关掉的臂里不该出现任何闸行
        "gate_lines": sum(1 for l in txt.splitlines()
                          if "自检未过（第" in l or "收尾交付**未过闸**" in l),
    }


def task_scores(d: dict, reps: int) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for (tid, r), t in sorted(d["per"].items()):
        if r <= reps:
            out.setdefault(tid, []).append(t["score"])
    return out


def fn_to_task(suite: Path) -> dict[str, str]:
    """日志的 `▶ 任务开始` 行只印**函数名**，不印题号 ⇒ 这里建映射。

    ⚠️ 这是 S11 第一版留空的直接原因：我拿日志里的名字去和题号比，永远匹配不上，
       于是「闸碰过的题」成了空集，而空集与「没碰过」输出同形（§70.4 ③ 的老病）。
    """
    out: dict[str, str] = {}
    for line in open(suite, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        m = re.search(r"def\s+(\w+)\s*\(", r.get("prompt", ""))
        if m:
            out[m.group(1)] = r["id"]
    return out


def latent(d: dict) -> dict:
    """交付物离线复算：有多少份「如果关开着会被拦」，缺的是什么名字。"""
    rows = d["ans"]
    uniq: dict[str, dict] = {}
    for a in rows:
        uniq.setdefault(a["sha256"], a)
    blocked: dict[str, list] = {}
    names: dict[str, int] = {}
    for a in uniq.values():
        bad = would_block(a["answer"])
        if bad:
            blocked.setdefault(a["task_id"], []).append(a["sha256"][:8])
            for _fn, nm in bad:
                names[nm] = names.get(nm, 0) + 1
    return {"n_rows": len(rows), "n_uniq": len(uniq), "blocked": blocked,
            "n_blocked": sum(len(v) for v in blocked.values()), "names": names}


def proxy_calls(path: Path) -> dict:
    """代理台账：请求数（≈ 生成次数）与总 token。"""
    n = 0
    tok = 0
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            for k in ("total_tokens", "tokens", "completion_tokens"):
                v = r.get(k)
                if isinstance(v, int):
                    tok += v
                    break
            else:
                u = r.get("usage") or {}
                if isinstance(u.get("total_tokens"), int):
                    tok += u["total_tokens"]
    except OSError:
        return {"n": 0, "tok": 0}
    return {"n": n, "tok": tok}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="/tmp/v1_s103_bench_a0_v4")
    ap.add_argument("--b", default="/tmp/v1_s103_bench_a2_v4")
    ap.add_argument("--a-log", default="/tmp/v1_s103_run_a0_v4.log")
    ap.add_argument("--b-log", default="/tmp/v1_s103_run_a2_v4.log")
    ap.add_argument("--a-proxy", default="/tmp/v1_s103_proxy_a0_v4.jsonl")
    ap.add_argument("--b-proxy", default="/tmp/v1_s103_proxy_a2_v4.jsonl")
    ap.add_argument("--a-time", default="/tmp/v1_s103_time_a0_v4.txt")
    ap.add_argument("--b-time", default="/tmp/v1_s103_time_a2_v4.txt")
    ap.add_argument("--oracle-log", default="/tmp/v1_s103_oracle_v4.log")
    ap.add_argument("--t1b", default=str(ROOT / "bench/evidence/artifact_run_s101/t1b_fixed_ruler"))
    ap.add_argument("--suite", default=str(ROOT / "bench/specgen/tasks_v4.jsonl"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    F2T = fn_to_task(Path(args.suite))

    t1b = load(Path(args.t1b))
    t1b_at04 = next(a["answer"] for a in t1b["ans"] if a["task_id"] == "at04")
    if not selftest(t1b_at04):
        return 2

    A, B = load(Path(args.a)), load(Path(args.b))
    la, lb = log_stats(Path(args.a_log)), log_stats(Path(args.b_log))

    print("\n=== S1 接线层（N1）===")
    for nm, d, l in (("A =0", A, la), ("B =2", B, lb)):
        r = d["run"]["report"]
        print(f"  {nm}: attempted={r['attempted']} recorded={r['recorded']} "
              f"skipped_no_answer={r['skipped_no_answer']} failed_scorer={r['failed_scorer']} "
              f"| 日志尝试块={l['attempts']}")

    print("\n=== S2 口径（唯一自变量 = verify_static；端口必须相同）===")
    for nm, d in (("A", A), ("B", B)):
        c = d["run"]["caliber"]
        print(f"  {nm}: sig={d['run']['caliber_sig']} suite={c['suite_sha'][:12]} "
              f"scorer={c['scorer_sha'][:12]} endpoint={c['llm_cond'].get('endpoint')} "
              f"verify_static={c['limits'].get('verify_static')}")

    def flat(d, pre=""):
        out = {}
        for k, v in d.items():
            if isinstance(v, dict):
                out.update(flat(v, pre + k + "."))
            else:
                out[pre + k] = v
        return out

    fa, fb = flat(A["run"]["caliber"]), flat(B["run"]["caliber"])
    diff = {k: (fa.get(k), fb.get(k)) for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)}
    print(f"  两臂口径差异项（逐字段）= {diff}")
    only_vst = set(diff) == {"limits.verify_static"}

    print("\n=== S3 分数（Δ = A(=0) − B(=2)）===")
    ma, mb = A["run"]["measurement"], B["run"]["measurement"]
    print(f"  A（=0 关自检） mean={ma['mean']:.4f} std={ma['std']:.4f} "
          f"lo={ma['lo']:.4f} hi={ma['hi']:.4f} 极差={ma['hi'] - ma['lo']:.4f}")
    print(f"  B（=2 盲报）   mean={mb['mean']:.4f} std={mb['std']:.4f} "
          f"lo={mb['lo']:.4f} hi={mb['hi']:.4f} 极差={mb['hi'] - mb['lo']:.4f}")
    delta = ma["mean"] - mb["mean"]
    rng = max(ma["hi"] - ma["lo"], mb["hi"] - mb["lo"])
    print(f"  Δ(A−B) = {delta:+.4f}  ≡ {delta * 128:+.1f} 格 / 128   判定线 = max 极差 {rng:.4f}")

    print("\n=== S4 逐题 × 逐 rep ===")
    ka, kb = task_scores(A, 3), task_scores(B, 3)
    print("  题     A=0 (r1,r2,r3)        B=2 (r1,r2,r3)        Δ")
    for tid in ka:
        sa, sb = ka[tid], kb.get(tid, [])
        d = (sum(sa) / len(sa)) - (sum(sb) / len(sb))
        mark = "  ←" if abs(d) > 1e-9 else ""
        print(f"  {tid:5s} {' '.join(f'{x:.4f}' for x in sa):22s} "
              f"{' '.join(f'{x:.4f}' for x in sb):22s} {d:+.4f}{mark}")

    print("\n=== S5 真机会（日志；机会 = 触发静态判据的尝试）===")
    for nm, l in (("A =0", la), ("B =2", lb)):
        print(f"  {nm}: 机会={l['opportunities']} 咬合={l['engaged']} "
              f"| 打回={l['retries']} 收尾未过闸={l['closeout_fail']} "
              f"收尾过闸={l['closeout_pass']} | 载荷={l['payloads']}")
        for i, b in enumerate(l["blocks"]):
            if b["fails"]:
                print(f"      块{i + 1:02d} {F2T.get(b['task'], b['task']):6s}({b['task']}) "
                      f"失败{len(b['fails'])}次 "
                      f"{b['fails']} 打回={b['retries']} 收尾未过={b['closeout_fail']} "
                      f"⇒ {'重写后过闸' if len(b['fails']) == 1 else '仍失败'}")

    print("\n=== S6 潜在机会（交付物离线复算：如果闸开着会被拦的份数）===")
    lat_a, lat_b = latent(A), latent(B)
    for nm, lat in (("A =0", lat_a), ("B =2", lat_b)):
        print(f"  {nm}: 交付 {lat['n_rows']} 条 / 唯一 {lat['n_uniq']} 条 "
              f"⇒ 会被拦 {lat['n_blocked']} 条  按题={ {k: len(v) for k, v in lat['blocked'].items()} } "
              f"缺名={lat['names']}")

    print("\n=== S7 N8 反证：复算器 vs 内核判据是否同源 ===")
    print(f"  B 臂日志「收尾未过闸」= {lb['closeout_fail']} 次；"
          f"B 臂交付物离线复算「会被拦」= {lat_b['n_blocked']} 条")
    print("  ⚠️ 两者**不必相等**：日志数是「闸跑的次数」（同一尝试可多次），"
          "复算数是「末轮交付物的份数」。\n"
          "     真正要核的是：**B 臂末轮交付物里仍有会被拦的份数 ≤ 已修好的份数**，"
          "且 =0 臂的这个数**不低于** B 臂（关掉闸 ⇒ 没被修）。")

    print("\n=== S8 成本（代理台账 = 生成次数；时间取自跑批记录）===")
    for nm, px, tp in (("A =0", args.a_proxy, args.a_time), ("B =2", args.b_proxy, args.b_time)):
        pc = proxy_calls(Path(px))
        secs = ""
        try:
            _t, t0, t1 = Path(tp).read_text().split()
            secs = f"{int(t1) - int(t0)}s"
        except OSError:
            secs = "?"
        print(f"  {nm}: 生成请求 {pc['n']} 次  tok={pc['tok']}  墙钟={secs}")

    print("\n=== S9 判负检查（PREREG 第四节；按主张分组）===")
    ra, rb = A["run"]["report"], B["run"]["report"]
    oracle_ok = False
    try:
        t = Path(args.oracle_log).read_text(encoding="utf-8", errors="replace")
        m = re.search(r"上界参照）@1x n=\d+ mean=([\d.]+)", t)
        oracle_ok = m is not None and float(m.group(1)) == 1.0
    except OSError:
        pass
    #: 主张「有差异」要过的线
    need_diff = [
        ("N5 |Δ| > 本 run 极差", abs(delta) > rng),
        ("N6 |Δ| > 3 格（0.0234）", abs(delta) > 3 / 128),
    ]
    #: 主张「无差异」要过的线（等效性；N5/N6 在这里**反向**用 —— 见下方自踩）
    per_task_equal = all(abs(sum(ka[t]) / len(ka[t]) - sum(kb[t]) / len(kb[t])) < 1e-12 for t in ka)
    need_eq = [
        ("逐题 × 逐 rep 全等", per_task_equal),
        ("|Δ| < 3 格（等效界）", abs(delta) < 3 / 128),
    ]
    base = [
        ("N1 接线", ra["attempted"] == ra["recorded"] and ra["failed_scorer"] == 0
         and rb["attempted"] == rb["recorded"] and rb["failed_scorer"] == 0),
        ("N2 oracle=1.0（日志）", oracle_ok),
        ("N3 B 臂真机会 > 0", lb["opportunities"] > 0),
        ("N4 A 臂零闸行（关真的关上了）", la["gate_lines"] == 0),
        ("口径只差 verify_static", only_vst),
        ("N8 复算器与内核同源（S0-B 已证）", True),
    ]
    for nm, ok in base + need_diff + need_eq:
        print(f"  {'✅' if ok else '⛔'} {nm}")
    print("  ⚠️ 「有差异」组与「无差异」组**互斥**：前者要 |Δ| 大，后者要 |Δ| 小。"
          "只对其中一组下结论。")

    print("\n=== S10 交付物级对照（sha；比分数更硬的一层）===")
    def bytask(d):
        o: dict[str, list] = {}
        for a in d["ans"]:
            o.setdefault(a["task_id"], []).append(a)
        return o
    ta, tb = bytask(A), bytask(B)
    print("  题     A 唯一 sha            B 唯一 sha            末条相同  交付物变了?")
    changed = []
    for tid in sorted(ta):
        sa = {x["sha256"][:8] for x in ta[tid]}
        sb = {x["sha256"][:8] for x in tb[tid]}
        same_last = ta[tid][-1]["sha256"] == tb[tid][-1]["sha256"]
        if sa != sb:
            changed.append(tid)
        print(f"  {tid:5s} {','.join(sorted(sa)):22s} {','.join(sorted(sb)):22s} "
              f"{str(same_last):8s}  {'变了' if sa != sb else '—'}")
    print(f"  ⇒ 交付物 sha 集合有变化的题：{changed or '无'}")

    print("\n=== S11 闸到底改了什么（只看被闸碰过的题）===")
    touched = {F2T.get(b["task"], b["task"]) for b in lb["blocks"] if b["fails"]}
    print(f"  被闸碰过的题（由函数名映射题号）：{sorted(touched)}")
    for tid in sorted(touched):
        print(f"  {tid}：逐条交付物（行序=尝试序）")
        for nm, tt in (("A =0", ta.get(tid, [])), ("B =2", tb.get(tid, []))):
            for i, a in enumerate(tt, 1):
                bad = would_block(a["answer"])
                print(f"    {nm} #{i} sha={a['sha256'][:8]} len={len(a['answer']):4d} "
                      f"会被拦={[n for _f, n in bad] if bad else '放行'}")

    print("\n=== S12 判读 ===")
    ok_base = all(ok for _, ok in base)
    if ok_base and all(ok for _, ok in need_diff) and delta <= -3 / 128:
        print("  ⇒ H1 未被否证：`=2` 比 `=0` 高 ≥ 3 格（盲报值那一次额外生成）")
    elif ok_base and all(ok for _, ok in need_diff) and delta >= 3 / 128:
        print("  ⇒ 第三类：`=0` 反而更高 ⇒ 闸在**制造**伤害（⛔ 不许并进 H0 说成「差不多」）")
    elif ok_base and all(ok for _, ok in need_eq):
        print("  ⇒ H0 未被否证：两臂逐题逐 rep 全等、|Δ| < 3 格 ⇒ "
              "盲报在这把尺子上是**纯开销**"
              f"（生成 {proxy_calls(Path(args.b_proxy))['n']} vs "
              f"{proxy_calls(Path(args.a_proxy))['n']} 次）")
        print("  ⚠️ 这是**等效性**结论，不是「证明相等」：本 run 极差 = %.4f 只说明"
              "「本时段抖动为 0」，不等于「任何 Δ 都可分辨」（§63）。" % rng)
    else:
        print("  ⇒ ⛔ 有判负项不满足 ⇒ UNKNOWN；不许写成「略好」或「无效」")

    if args.out:
        print(f"\n（本页由 analyze_v1.py 生成；口径 see PREREG.md；输出副本 {args.out}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

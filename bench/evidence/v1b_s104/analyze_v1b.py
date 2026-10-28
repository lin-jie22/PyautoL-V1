#!/usr/bin/env python3
"""§74 / V1b 的读数器：`verify_static` **同晚三臂** 0 / 2 / 1，`tasks_v4`。

预注册见同目录 `PREREG.md`（写于跑批之前）。本脚本**只做读数与判负检查**；
每条结论都必须能指回下面某个数。

⚠️ 检测器（`would_block`）与日志切块（`log_stats`）**直接从 §73 的读数器 import**，
   连它的 S0 自检一起带过来 —— 一份装置、一次自检，不复制（复制会分叉）。
   本轮新写的只有「三臂」这一层：`verdict()` 与它的 **S0-D 反证**（喂合成读数看判据会不会翻方向）。

⭐ `verdict()` 必须自带反证（§73.5.1 的教训：判据方向错会让「无差异」永远判不出来）：
   S0-D 用三组合成 mean 打它，其中一组是「`=2` 最高」——判据必须不说 H1。

用法：
  python3 bench/evidence/v1b_s104/analyze_v1b.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench/evidence/v1_s103"))

from analyze_v1 import (  # noqa: E402  —— 同一份装置，不复制
    fn_to_task,
    gate_payload,
    latent,
    load,
    log_stats,
    proxy_calls,
    selftest,
    task_scores,
    would_block,
)

THREE = 3 / 128  #: 「3 格」的分数当量


# ── 判据本体（三臂）────────────────────────────────────────────────────────
def verdict(m0: float, m2: float, m1: float, rng: float) -> tuple[str, str]:
    """返回 (标签, 一句话)。标签 ∈ H1 / H0 / UNKNOWN。

    H1 = 报名字(=1) 最高，且高出 max(0,2) 超过 max(本 run 极差, 3 格)
    H0 = 报名字(=1) 不高出 max(0,2) + 3 格（即「不再最高」）
    其余 = UNKNOWN（差值落在 3 格与极差之间的灰带）
    """
    thr = max(rng, THREE)
    if m1 - m0 > thr and m1 - m2 > thr:
        return "H1", f"mean(1)={m1:.4f} 比 0/2 都高 > {thr:.4f}"
    if m1 <= max(m0, m2) + THREE:
        return "H0", f"mean(1)={m1:.4f} ≤ max(0,2)+3格"
    return "UNKNOWN", f"mean(1)={m1:.4f} 落在灰带（> {THREE:.4f} 但不 > {thr:.4f}）"


def selftest_verdict() -> bool:
    """S0-D：喂合成读数，检查判据方向。**这一条是给判据本身写的反证。**

    ⚠️ 第一版这里写错了 4 号用例的**期望**（我指望它落 UNKNOWN）。规则（PREREG 第二节）是
       「高出不到 3 格 ⇒ 不能主张更高 ⇒ 判 H0」，3 格是**分辨力下限**、不是灰带下缘；
       灰带只存在于「极差 > 3 格」时（增益超过分辨力但仍不超过极差）。改的是**测试**，
       不是让实现去迁就测试 —— 并且补了 5 号用例把灰带**真的走一遍**，否则「灰带可达」
       这句话没有任何证据，与「空集与没发生同形」是同一类错。
    """
    cases = [
        # (m0, m2, m1, rng, 期望标签, 说明)
        (0.5469, 0.5469, 0.6250, 0.0000, "H1", "§72/§73 的预期形状（+10 格）"),
        (0.5469, 0.5469, 0.5469, 0.0000, "H0", "=1 塌回基线 ⇒ 必须判 H0"),
        (0.5469, 0.6250, 0.5469, 0.0000, "H0", "⭐ 反证：=2 最高时**不许**判 H1"),
        (0.5469, 0.5469, 0.5703, 0.0000, "H0", "恰好 3 格 = 分辨力下限 ⇒ 不能主张「更高」"),
        (0.5469, 0.5469, 0.5969, 0.0800, "UNKNOWN", "⭐ 灰带可达：增益 6.4 格 > 3 格但不 > 极差 0.08"),
    ]
    ok = True
    print("=== S0-D 判据反证（5 组合成读数）===")
    for m0, m2, m1, rng, want, note in cases:
        got, why = verdict(m0, m2, m1, rng)
        good = got == want
        ok &= good
        print(f"  {'PASS' if good else '**FAIL**':8s} 0/2/1={m0:.4f}/{m2:.4f}/{m1:.4f} "
              f"⇒ {got:8s} 期望={want:8s} {note}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="", help="含三个子目录 a0_v4/a2_v4/a1_v4 的目录（默认本目录）")
    ap.add_argument("--t1b", default=str(ROOT / "bench/evidence/artifact_run_s101/t1b_fixed_ruler"))
    ap.add_argument("--suite", default=str(ROOT / "bench/specgen/tasks_v4.jsonl"))
    args = ap.parse_args()

    here = Path(args.dir) if args.dir else Path(__file__).resolve().parent
    tags = [("0", "a0_v4"), ("2", "a2_v4"), ("1", "a1_v4")]

    F2T = fn_to_task(Path(args.suite))

    t1b = load(Path(args.t1b))
    t1b_at04 = next(a["answer"] for a in t1b["ans"] if a["task_id"] == "at04")
    if not selftest(t1b_at04):
        print("⛔ S0-A/B 检测器自检失败 ⇒ 不打印任何结论")
        return 2
    if not selftest_verdict():
        print("⛔ S0-D 判据反证失败 ⇒ 不打印任何结论")
        return 2

    D, L = {}, {}
    for lv, tag in tags:
        D[lv] = load(here / tag)
        L[lv] = log_stats(here / tag / "run.log")

    print("\n=== S1 接线层（N1）===")
    for lv, tag in tags:
        r = D[lv]["run"]["report"]
        print(f"  ={lv} [{tag}]: attempted={r['attempted']} recorded={r['recorded']} "
              f"skipped_no_answer={r['skipped_no_answer']} failed_scorer={r['failed_scorer']} "
              f"| 日志尝试块={L[lv]['attempts']}")

    print("\n=== S2 口径（唯一自变量 = verify_static；端口必须相同）===")
    for lv, tag in tags:
        c = D[lv]["run"]["caliber"]
        print(f"  ={lv}: sig={D[lv]['run']['caliber_sig']} suite={c['suite_sha'][:12]} "
              f"endpoint={c['llm_cond'].get('endpoint')} "
              f"verify_static={c['limits'].get('verify_static')}")

    def flat(d, pre=""):
        out = {}
        for k, v in d.items():
            if isinstance(v, dict):
                out.update(flat(v, pre + k + "."))
            else:
                out[pre + k] = v
        return out

    fl = {lv: flat(D[lv]["run"]["caliber"]) for lv, _ in tags}
    only_vst = True
    for i in range(len(tags)):
        for j in range(i + 1, len(tags)):
            a, b = tags[i][0], tags[j][0]
            d = {k: (fl[a].get(k), fl[b].get(k))
                 for k in sorted(set(fl[a]) | set(fl[b])) if fl[a].get(k) != fl[b].get(k)}
            okk = set(d) == {"limits.verify_static"}
            only_vst &= okk
            print(f"  ={a} vs ={b} 差异项 = {d}")

    print("\n=== S3 分数（三臂）===")
    M = {}
    for lv, _ in tags:
        m = D[lv]["run"]["measurement"]
        M[lv] = m["mean"]
        print(f"  ={lv}: mean={m['mean']:.4f} std={m['std']:.4f} "
              f"lo={m['lo']:.4f} hi={m['hi']:.4f} 极差={m['hi'] - m['lo']:.4f}")
    rng = max(D[lv]["run"]["measurement"]["hi"] - D[lv]["run"]["measurement"]["lo"] for lv, _ in tags)
    print(f"  三臂最大极差 = {rng:.4f}")
    for x, y in (("1", "2"), ("1", "0"), ("0", "2")):
        d = M[x] - M[y]
        print(f"  Δ({x}−{y}) = {d:+.4f} ≡ {d * 128:+.1f} 格 / 128")

    print("\n=== S4 逐题 × 逐 rep ===")
    K = {lv: task_scores(D[lv], 3) for lv, _ in tags}
    print("  题     =0 (r1,r2,r3)           =2 (r1,r2,r3)           =1 (r1,r2,r3)           谁高")
    for tid in K["0"]:
        cells = []
        for lv in ("0", "2", "1"):
            cells.append(" ".join(f"{x:.4f}" for x in K[lv].get(tid, [])))
        means = {lv: sum(K[lv].get(tid, [0])) / max(len(K[lv].get(tid, [1])), 1) for lv in ("0", "2", "1")}
        top = max(means, key=lambda k: (means[k], k == "1"))
        mark = "" if len({round(v, 6) for v in means.values()}) == 1 else f"  ← ={top}"
        print(f"  {tid:5s} {cells[0]:23s} {cells[1]:23s} {cells[2]:23s}{mark}")

    print("\n=== S5 真机会（日志；机会 = 触发静态判据的尝试）===")
    for lv, _ in tags:
        l = L[lv]
        print(f"  ={lv}: 机会={l['opportunities']} 咬合={l['engaged']} "
              f"| 打回={l['retries']} 收尾未过闸={l['closeout_fail']} "
              f"收尾过闸={l['closeout_pass']} | 载荷={l['payloads']}")
        for i, b in enumerate(l["blocks"]):
            if b["fails"]:
                print(f"      块{i + 1:02d} {F2T.get(b['task'], b['task']):6s}({b['task']}) "
                      f"失败{len(b['fails'])}次 {b['fails']} 打回={b['retries']} "
                      f"收尾未过={b['closeout_fail']} "
                      f"⇒ {'重写后过闸' if len(b['fails']) == 1 else '仍失败'}")

    print("\n=== S6 潜在机会（交付物离线复算；只给 =0 臂补分母，不加进真机会）===")
    for lv, _ in tags:
        lat = latent(D[lv])
        print(f"  ={lv}: 交付 {lat['n_rows']} 条 / 唯一 {lat['n_uniq']} 条 ⇒ 会被拦 {lat['n_blocked']} 条 "
              f"按题={ {k: len(v) for k, v in lat['blocked'].items()} } 缺名={lat['names']}")

    print("\n=== S7 成本（代理台账 = 生成次数；时间取自跑批记录）===")
    cost = {}
    for lv, tag in tags:
        pc = proxy_calls(here / tag / "proxy.jsonl")
        cost[lv] = pc
        secs = "?"
        try:
            _t, t0, t1 = (here / tag / "time.txt").read_text().split()
            secs = f"{int(t1) - int(t0)}s"
        except OSError:
            pass
        print(f"  ={lv}: 生成 {pc['n']} 次  tok={pc['tok']}  墙钟={secs}")

    print("\n=== S8 交付物级对照（sha；比分数更硬的一层）===")
    def bytask(d):
        o: dict[str, list] = {}
        for a in d["ans"]:
            o.setdefault(a["task_id"], []).append(a)
        return o
    T = {lv: bytask(D[lv]) for lv, _ in tags}
    print("  题     =0 唯一 sha            =2 唯一 sha            =1 唯一 sha            三者全同?")
    diff_tasks = []
    for tid in sorted(T["0"]):
        sa = {x["sha256"][:8] for x in T["0"].get(tid, [])}
        sb = {x["sha256"][:8] for x in T["2"].get(tid, [])}
        sc = {x["sha256"][:8] for x in T["1"].get(tid, [])}
        allsame = sa == sb == sc
        if not allsame:
            diff_tasks.append(tid)
        print(f"  {tid:5s} {','.join(sorted(sa)):22s} {','.join(sorted(sb)):22s} "
              f"{','.join(sorted(sc)):22s} {'是' if allsame else '否'}")
    print(f"  ⇒ 三臂交付物 sha 集合不全同的题：{diff_tasks or '无'}")

    print("\n=== S9 被闸碰过的题（三臂逐条交付物，行序=尝试序）===")
    touched = sorted({F2T.get(b["task"], b["task"]) for lv, _ in tags
                      for b in L[lv]["blocks"] if b["fails"]})
    print(f"  被闸碰过的题：{touched or '无'}")
    for tid in touched:
        for lv, _ in tags:
            for i, a in enumerate(T[lv].get(tid, []), 1):
                bad = would_block(a["answer"])
                print(f"    ={lv} #{i} sha={a['sha256'][:8]} len={len(a['answer']):4d} "
                      f"会被拦={[n for _f, n in bad] if bad else '放行'}")

    print("\n=== S10 判负检查（PREREG 第四节）===")
    oracle_ok = False
    try:
        t = (here / "oracle_v4.log").read_text(encoding="utf-8", errors="replace")
        m = re.search(r"上界参照）@1x n=\d+ mean=([\d.]+)", t)
        oracle_ok = m is not None and float(m.group(1)) == 1.0
    except OSError:
        pass
    base = [
        ("N1 接线（三臂）", all(D[lv]["run"]["report"]["attempted"] == D[lv]["run"]["report"]["recorded"]
                              and D[lv]["run"]["report"]["failed_scorer"] == 0 for lv, _ in tags)),
        ("N2 oracle=1.0（日志）", oracle_ok),
        ("N3 口径两两只差 verify_static", only_vst),
        ("N4 =0 臂零闸行", L["0"]["gate_lines"] == 0),
        ("N5 =2/=1 机会数都 > 0", L["2"]["opportunities"] > 0 and L["1"]["opportunities"] > 0),
    ]
    claim = [
        ("N6 |Δ(1−2)| 与 |Δ(1−0)| 都 > 三臂最大极差",
         abs(M["1"] - M["2"]) > rng and abs(M["1"] - M["0"]) > rng),
        ("N7 两者都 > 3 格", abs(M["1"] - M["2"]) > THREE and abs(M["1"] - M["0"]) > THREE),
    ]
    for nm, ok in base + claim:
        print(f"  {'✅' if ok else '⛔'} {nm}")
    print("  ⚠️ N6/N7 只在主张「=1 最高」时需要；若 =1 没高，它们**本来就该红**，"
          "不构成对 H0 的否证（§73.5.1）。")

    print("\n=== S11 判读 ===")
    lab, why = verdict(M["0"], M["2"], M["1"], rng)
    ok_base = all(ok for _, ok in base)
    if not ok_base:
        print("  ⇒ ⛔ 前置不满足 ⇒ UNKNOWN；不许写成「略好」或「无效」")
    elif lab == "H1":
        print(f"  ⇒ **H1 未被否证**：{why}")
        print("     三档同晚：=1 最高 ⇒ §72.5 的机制解释在本 session 内独立复现")
        ok_at04 = K["1"].get("at04") and all(abs(x - 1.0) < 1e-9 for x in K["1"]["at04"])
        print(f"     PREREG 附加条件 at04(=1) 全 1.0000：{'✅' if ok_at04 else '⛔ 未达'}")
    elif lab == "H0":
        print(f"  ⇒ **H0 未被否证**：{why}")
        print("     ⛔ 必须**撤回** §72.5 的机制解释（DESIGN §74 + MEMORY.md 同步）")
    else:
        print(f"  ⇒ ⛔ UNKNOWN：{why}")

    rep02 = abs(M["0"] - M["2"])
    print(f"\n  附：=0 vs =2 本轮 Δ={M['0'] - M['2']:+.4f}（§73 为 +0.0000）"
          f" ⇒ {'复现' if rep02 < THREE else '**未复现**（§73 的「纯开销」要降级为「本时段未测出收益」）'}")
    print(f"  成本：生成 " + " / ".join(f"={lv}:{cost[lv]['n']}" for lv, _ in tags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

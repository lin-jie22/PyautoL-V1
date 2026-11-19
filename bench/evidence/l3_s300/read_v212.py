"""V212 读数：配对块（§280）。

⛔ 这个脚本只读三份东西：调度表、运行账本、答案日志。**不重算分数**——
   分数是 `run.py` 的产物，重算一遍就叫「用另一个谓词覆盖原读数」。
⛔ 判据的分母口径写在字段名里：`b`/`c` 是**块**数（20 块），不是题次。
"""
import json
import re
import sys
from math import comb

D = "bench/evidence/l3_s300"
TUP_ASSIGN = re.compile(r"\[\s*-1\s*\]\s*\[\s*1\s*\]\s*=")
TUP_REBUILD = re.compile(r"\[\s*-1\s*\]\s*=\s*\(")


def mcnamar(b: int, c: int) -> float:
    """精确双侧 McNemar（= 不一致对上做二项检验，p=0.5）。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def main() -> int:
    # ── 调度表与实跑顺序 ──────────────────────────────────────────
    sched = []
    for ln in open(f"{D}/schedule.tsv", encoding="utf-8"):
        if ln.strip():
            c = ln.rstrip("\n").split("\t")
            sched.append((int(c[0]), int(c[1]), c[2]))
    order = []
    for ln in open(f"{D}/order.txt", encoding="utf-8"):
        if not ln.strip():
            continue
        c = ln.rstrip("\n").split("\t")
        order.append((int(c[0]), int(c[1]), c[2], c[3], c[4]))
    p1a = len(order) == len(sched) == 40 and all(
        (o[0], o[1], o[2]) == s for o, s in zip(order, sched))
    print(f"P1a 调度表核对  {'✅' if p1a else '❌'}  表 {len(sched)} 行 / 实跑 {len(order)} 行"
          f" / 逐行相同={p1a}")
    bad = [o for o in order if o[3] != "rc=0"]
    print(f"    非零退出 {len(bad)} 次 {[b[:3] for b in bad]}")

    # ── 账本：按调度顺序取最后 40 条新 run ────────────────────────
    start = int(open(f"{D}/start_runs.txt", encoding="utf-8").read().strip())
    runs = [json.loads(l) for l in open("bench_out/runs.jsonl", encoding="utf-8") if l.strip()]
    new = runs[start:]
    p1b_n = len(new) == 40
    ts = []
    for r in new:
        try:
            ts.append(int(r["run_id"].split("@")[1]))
        except Exception:
            ts.append(-1)
    p1b_t = all(x <= y for x, y in zip(ts, ts[1:])) and len(ts) == 40
    p1b_rep = all(r.get("repeat") == 1 for r in new)
    print(f"P1b 账本        {'✅' if (p1b_n and p1b_t and p1b_rep) else '❌'}"
          f"  新 run {len(new)} 条 / ts 单调不减={p1b_t} / 全 repeat=1={p1b_rep}")

    # ── 装置正面路径：评审块只在 prev 臂出现 ──────────────────────
    cnt = {}
    for arm in ("off", "prev"):
        n_state = n_blk = 0
        for ln in open(f"{D}/state_{arm}.jsonl", encoding="utf-8"):
            if not ln.strip():
                continue
            n_state += 1
            n_blk += sum(1 for x in json.loads(ln)["state"].splitlines()
                         if x.startswith("[外部评审]"))
        cnt[arm] = (n_state, n_blk)
    p1c = cnt["prev"][1] > 0 and cnt["off"][1] == 0
    print(f"P1c 装置路径    {'✅' if p1c else '❌'}  off 行{cnt['off'][0]}/块{cnt['off'][1]}"
          f"  prev 行{cnt['prev'][0]}/块{cnt['prev'][1]}")

    # ── 逐块读数 ─────────────────────────────────────────────────
    ans = [json.loads(l) for l in open("bench_out/answers.jsonl", encoding="utf-8") if l.strip()]
    ans = [x for x in ans if x.get("answer") and x.get("task_id")]
    rows = []
    for (blk, pos, arm, _rc, _t), r in zip(order, new):
        got = {}
        for tid in ("t01_parse_duration", "t04_merge_ranges"):
            vs = [x["vec"] for x in r["report"]["per_task"] if x["id"] == tid]
            got[tid] = vs[0] if vs else None
        a4 = [x["answer"] for x in ans
              if x.get("run_id") == r["run_id"] and x["task_id"].startswith("t04")]
        shape = "?"
        if a4:
            m = TUP_ASSIGN.search(a4[0])
            r_ = TUP_REBUILD.search(a4[0])
            shape = ("赋值+重建" if (m and r_) else "赋值" if m
                     else "重建" if r_ else "其他")
        rows.append({"blk": blk, "arm": arm, "t01": got["t01_parse_duration"],
                     "t04": got["t04_merge_ranges"], "shape": shape})

    print("\n块  off→t04[0]  prev→t04[0]   off t01[1..3]  prev t01[1..3]   off形状 / prev形状")
    by = {}
    for r in rows:
        by.setdefault(r["blk"], {})[r["arm"]] = r
    for k in sorted(by):
        o, p = by[k]["off"], by[k]["prev"]
        print(f"{k:2d}   {int(bool(o['t04'][0]))}/1          {int(bool(p['t04'][0]))}/1"
              f"            {sum(o['t01'][1:4])}/3           {sum(p['t01'][1:4])}/3"
              f"        {o['shape']:6s} / {p['shape']}")

    # ── 判据 ─────────────────────────────────────────────────────
    b = sum(1 for k in by if by[k]["prev"]["t04"][0] and not by[k]["off"]["t04"][0])
    c = sum(1 for k in by if by[k]["off"]["t04"][0] and not by[k]["prev"]["t04"][0])
    p = mcnamar(b, c)
    p2 = b >= 7 and p < 0.05
    print(f"\nP2 t04[0] 配对  {'✅' if p2 else '❌'}  b(prev通过/off不过)={b}"
          f"  c(off通过/prev不过)={c}  McNemar 精确 p={p:.4f}")
    off4 = sum(1 for k in by if by[k]["off"]["t04"][0])
    prev4 = sum(1 for k in by if by[k]["prev"]["t04"][0])
    p3 = off4 <= 3
    print(f"P3 对照物非空    {'✅' if p3 else '❌'}  off t04[0]={off4}/20  prev={prev4}/20")

    # ── R1 漂移规则 ──────────────────────────────────────────────
    ks = sorted(by)
    early = [sum(by[k]["off"]["t01"][1:4]) for k in ks[:10]]
    late = [sum(by[k]["off"]["t01"][1:4]) for k in ks[10:]]
    de = sum(early) / 30 - sum(late) / 30
    print(f"\nR1 off 臂漂移   前10块 {sum(early)}/30  后10块 {sum(late)}/30"
          f"  差={de:+.4f}  {'⚠️ 超 0.10 ⇒ P2/P3 降级 UNKNOWN' if abs(de) > 0.10 else '在 0.10 内'}")
    o_all = sum(sum(by[k]['off']['t01'][1:4]) for k in ks)
    p_all = sum(sum(by[k]['prev']['t01'][1:4]) for k in ks)
    print(f"   （读数，非判据）t01[1..3]  off {o_all}/60  prev {p_all}/60  "
          f"Δ={(p_all-o_all)/60:+.4f}")

    # ── 机制读数（形状）──────────────────────────────────────────
    print("\n机制（非判据）：t04 答案形状")
    for arm in ("off", "prev"):
        sh = [r["shape"] for r in rows if r["arm"] == arm]
        print(f"  {arm:4s} n={len(sh)}  " + "  ".join(f"{k}={v}" for k, v in sorted(
            {s: sh.count(s) for s in set(sh)}.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""V144 读数：账本里的结论能不能被第三方从明细重算。

判据见 PREREG.md §3（跑前落盘）。本脚本只跑预注册过的格子，
额外一格（J7）是**边界读数**，不是判据：它量化审计能抓到什么、抓不到什么。
"""
import copy
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw import bench as B  # noqa: E402

LEDGER = ROOT / "bench_out" / "runs.jsonl"
OUT = Path(__file__).resolve().parent


def load():
    return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]


def pick(rows, need=10):
    """挑一行「有足够明细」的 VERIFIED 记录，避免删一条就删空。"""
    for r in reversed(rows):
        pt = (r.get("report") or {}).get("per_task") or []
        if len(pt) >= need and B.audit_measurement(r)[0] == "VERIFIED":
            return r
    raise SystemExit("找不到可用的样本行")


def main():
    rows = load()
    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    emit("=" * 68)
    emit("V144 读数 · 账本 %s · %d 行" % (LEDGER.name, len(rows)))
    emit("=" * 68)

    # ── J1 正例 + W4 ──────────────────────────────────────────────
    st = B.ledger_audit_strata(LEDGER)
    emit("\n[J1] 真实账本按可重算性分层")
    emit("  " + json.dumps(st, ensure_ascii=False, sort_keys=True))
    emit("  守恒 VERIFIED+MISMATCH+UNVERIFIABLE == rows : %s"
         % (st["VERIFIED"] + st["MISMATCH"] + st["UNVERIFIABLE"] == st["rows"]))
    emit("  W4 押「VERIFIED > 0 且 < 145」                      : %s (%d)"
         % (0 < st["VERIFIED"] < st["rows"], st["VERIFIED"]))

    # 逐行复核：重算值 vs 写下的值（证明 J1 不是「都没算」）
    checked = 0
    for r in rows:
        if B.audit_measurement(r)[0] == "VERIFIED":
            checked += 1
    emit("  J1 押「不符数 == 0」                              : %s (checked=%d)"
         % (st["MISMATCH"] == 0, checked))

    base = pick(rows)
    emit("\n样本行 run_id=%r  attempted=%d recorded=%d 明细=%d 轮=%d"
         % (base.get("run_id"), base["report"]["attempted"], base["report"]["recorded"],
            len(base["report"]["per_task"]), base.get("repeat")))

    # ── J2 删一条明细 ─────────────────────────────────────────────
    a = copy.deepcopy(base)
    a["report"]["per_task"] = a["report"]["per_task"][:-1]
    sa, wa = B.audit_measurement(a)
    got_a = B.recompute_mean(a)
    emit("\n[J2] 删掉一条 per_task（⛔ 不许补 0、不许跳过）")
    emit("  判定 = %s" % sa)
    emit("  理由 = %s" % wa)
    emit("  仍算得出一个数 = %s" % (got_a[0] if got_a else None))
    emit("  W1 押「理由落在 R4（条数）」                      : %s"
         % ("明细条数" in wa))
    emit("  W1 押「不是落在 R6（数值）」                      : %s"
         % ("重算" not in wa.split("：")[0]))

    # ── J2b 删掉一整轮 ───────────────────────────────────────────
    b = copy.deepcopy(base)
    if base.get("repeat", 1) > 1:
        b["report"]["per_task"] = [x for x in b["report"]["per_task"] if x["rep"] != base["repeat"]]
    else:
        b["report"]["per_task"] = []
    sb, wb = B.audit_measurement(b)
    emit("\n[J2b] 删掉整轮明细")
    emit("  判定 = %s  理由 = %s" % (sb, wb))

    # ── J3 改一个分数（关键：重算值必须跟着变）───────────────────
    c = copy.deepcopy(base)
    old = c["report"]["per_task"][0]["score"]
    c["report"]["per_task"][0]["score"] = round(float(old) + 0.01, 6)
    sc_, wc = B.audit_measurement(c)
    got_c = B.recompute_mean(c)
    emit("\n[J3] 改掉一个 score（%r → %r）" % (old, c["report"]["per_task"][0]["score"]))
    emit("  判定 = %s" % sc_)
    emit("  理由 = %s" % wc)
    emit("  重算值随改动变化 = %s (%r vs 原 %r)"
         % (got_c and got_c[0] != float(base["measurement"]["mean"]),
            got_c[0] if got_c else None, base["measurement"]["mean"]))

    # ── J4 删掉 mean ─────────────────────────────────────────────
    d = copy.deepcopy(base)
    d["measurement"].pop("mean", None)
    sd, wd = B.audit_measurement(d)
    emit("\n[J4] 删掉 measurement.mean")
    emit("  判定 = %s  理由 = %s" % (sd, wd))

    # ── J5 缺席不许冒充 ─────────────────────────────────────────
    e = copy.deepcopy(base)
    e["report"]["per_task"] = []
    se, we = B.audit_measurement(e)
    emit("\n[J5] 明细置空（缺席）")
    emit("  判定 = %s（⛔ 必须是 UNVERIFIABLE，不是 VERIFIED）  %s"
         % (se, se == "UNVERIFIABLE"))

    # ── J6 求和算法 ─────────────────────────────────────────────
    real_fold = B._fold_sum
    B._fold_sum = lambda xs: float(sum(xs))  # noqa: E731
    try:
        bad = 0
        n = 0
        for r in rows:
            if not (r.get("report") or {}).get("per_task"):
                continue
            n += 1
            if B.audit_measurement(r)[0] != "VERIFIED":
                bad += 1
    finally:
        B._fold_sum = real_fold
    emit("\n[J6] 把 _fold_sum 换成内置 sum()（⛔ 补偿求和）")
    emit("  不符/不可复核 %d / %d" % (bad, n))
    emit("  W2 押「不符数 > 0」                               : %s" % (bad > 0))
    emit("  换回朴素左折叠后不符数 = %d" % (0 if B.ledger_audit_strata(LEDGER)["MISMATCH"] == 0 else 1))

    # ── 反证组 1：拿 mean 跟自己比 ───────────────────────────────
    real_rec = B.recompute_mean
    B.recompute_mean = lambda rec: (float(rec["measurement"]["mean"]),  # noqa: E731
                                   {"n_tasks": 0, "rounds": rec.get("repeat", 1),
                                    "rows": len(rec["report"].get("per_task") or []),
                                    "repeat": rec.get("repeat", 1)})
    try:
        f1 = B.audit_measurement(copy.deepcopy(base) if False else _strip_one(base))
        f2 = B.audit_measurement(c)
    finally:
        B.recompute_mean = real_rec
    emit("\n[反证1] 让 recompute_mean 直接返回写下的 mean（= 只验字段在不在）")
    emit("  删一条   ⇒ %s（预期**仍是** MISMATCH：R4 对账不依赖重算）" % f1[0])
    emit("  改分数   ⇒ %s（预期 VERIFIED：重算被蒙混）" % f2[0])
    emit("  反证成立 = %s" % (f1[0] == "MISMATCH" and f2[0] == "VERIFIED"))
    emit("  ⇒ 一 changed 一 unchanged 正好证明两条规则各自独立工作：")
    emit("     J2 抓的是**对账**（不依赖重算），J3 抓的是**重算**（不依赖对账）。")
    emit("  ⚠️ 本格判读方向在第一次跑时写反了（见 READINGS_v1_misread.txt）：")
    emit("     我当时把「两条都必须 MISMATCH」当成了反证成立 —— 那是把")
    emit("     J2 的机制误当成了 J3 的机制。PREREG §5 的措辞也有歧义，如实登记。")

    # ── J3b 对称格：只改结论，不改明细 ─────────────────────────
    h = copy.deepcopy(base)
    h["measurement"]["mean"] = round(float(h["measurement"]["mean"]) + 0.001, 6)
    sh_, wh_ = B.audit_measurement(h)
    emit("\n[J3b] 只改 mean（改结论不改证据，与 J3 对称）")
    emit("  判定 = %s  理由 = %s" % (sh_, wh_))

    # ── 反证组 2：去掉 R4 后 J2 落到哪 ──────────────────────────
    st3, w3 = _audit_without_r4(a)
    emit("\n[反证2] 删掉 R4（条数对账）后，删一条明细落到哪条规则")
    emit("  判定 = %s  理由 = %s" % (st3, w3))

    # ── J7 边界读数（不是判据）：连明细带结论一起改 ──────────────
    g = copy.deepcopy(base)
    g["report"]["per_task"][0]["score"] = round(float(g["report"]["per_task"][0]["score"]) + 0.01, 6)
    gv, _meta = B.recompute_mean(g)
    g["measurement"]["mean"] = gv
    sg, wg = B.audit_measurement(g)
    emit("\n[J7] 边界（⛔ 不是判据）：改明细**同时**把 mean 改成重算值")
    emit("  判定 = %s" % sg)
    emit("  ⇒ 审计保证的是**内部一致**，不是**真实性**：")
    emit("     抓得到「改了明细没改结论」与「改了结论没改明细」，")
    emit("     抓不到「两者一起改」。这条必须写进结果，不许说「账本不可篡改」。")

    # ── 查无此账 ────────────────────────────────────────────────
    miss = B.ledger_audit(LEDGER, "no-such-run-id@0")
    emit("\n[缺席] ledger_audit 查一个不存在的 run_id")
    emit("  判定 = %s  理由 = %s" % miss)

    # ── W3 通电检查（调用点，不是测试）──────────────────────────
    emit("\n[W3] 审计函数在非测试代码里的调用点")
    hits = _call_sites(ROOT / "run.py")
    emit("  run.py 里 ledger_audit/audit_measurement 调用点 = %d %s"
         % (len(hits), hits))

    (OUT / "READINGS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {"strata": st, "j2": [sa, wa], "j2b": [sb, wb], "j3": [sc_, wc],
               "j3b": [sh_, wh_],
               "j4": [sd, wd], "j5": [se, we], "j6_bad": [bad, n], "j7": [sg, wg],
               "rebuttal": [f1[0], f2[0]], "rebuttal_no_r4": [st3, w3],
               "call_sites": hits}
    (OUT / "readings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                       encoding="utf-8")


def _strip_one(base):
    a = copy.deepcopy(base)
    a["report"]["per_task"] = a["report"]["per_task"][:-1]
    return a


def _audit_without_r4(rec):
    """就地复现「没有 R4」的判定：跳过条数对账，直接进重算。"""
    got = B.recompute_mean(rec)
    if got is None:
        return "UNVERIFIABLE", "分母推不出来"
    val, _meta = got
    m = float(rec["measurement"]["mean"])
    if val != m:
        return "MISMATCH", (f"重算 {val!r} ≠ 写下的 {m!r}"
                            f"（Δ={abs(val - m):.3e}）⇒ 结论与明细打架")
    return "VERIFIED", "重算 == 写下的值"


def _call_sites(path):
    """按名字数调用点。⛔ 不用正则数 `\\.` —— 只认 Attribute/Name 两种形状。"""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"ledger_audit", "audit_measurement", "ledger_audit_strata",
             "baseline_recompute_gate"}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            nm = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else None)
            if nm in names:
                out.append(f"{nm}@{node.lineno}")
    return out


if __name__ == "__main__":
    main()

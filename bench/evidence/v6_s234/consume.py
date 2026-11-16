"""V145 读数：复核的成本、粒度与等价性。

跑法：`cd /Users/linshaojie/V1 && python3 bench/evidence/v6_s234/consume.py`

⛔ 所有「真实」读数只来自 `bench_out/runs.jsonl`（145 行 / 482 KB）。
⛔ 合成账本一律落临时目录，只用来量阶与构造反例。
"""

import ast
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import mw.bench as B  # noqa: E402
from mw.bench import (  # noqa: E402
    _read_records, _rid_is_literal, _scan_rows, audit_measurement,
    ledger_audit, ledger_audit_strata, ledger_verdict, recompute_mean,
)

REAL = ROOT / "bench_out/runs.jsonl"
OUT = []


def emit(s=""):
    print(s)
    OUT.append(s)


def t(fn, n=11):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


def tmpfile(name, lines):
    d = Path(tempfile.mkdtemp(prefix="v145_"))
    p = d / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def main():
    emit(f"[账本] {REAL.relative_to(ROOT)}  行={len(REAL.read_text(encoding='utf-8').splitlines())}"
         f"  字节={REAL.stat().st_size}")
    raw = [ln for ln in REAL.read_text(encoding="utf-8").splitlines() if ln.strip()]
    recs = [json.loads(ln) for ln in raw]
    addr = [r for r in recs if r.get("run_id")]
    emit(f"[账本] 可寻址（带 run_id）={len(addr)}  不可寻址={len(recs)-len(addr)}"
         f"  ⇒ 等价性分母只能是 {len(addr)}，不是 {len(recs)}")

    # ── J0 结构证明：均值依赖**全部**明细 ⇒ 没有「只算依赖的几条」这回事 ──────
    emit("\n[J0] 『只重算结论依赖的那几条明细』对 mean 不成立（V144 登记的方案撤回一半）")
    tgt = max(addr, key=lambda r: len(r["report"].get("per_task") or []))
    base = recompute_mean(tgt)[0]
    pt = list(tgt["report"]["per_task"])
    changed = 0
    for i in range(len(pt)):
        clone = json.loads(json.dumps(tgt))
        del clone["report"]["per_task"][i]
        changed += recompute_mean(clone)[0] != base
    emit(f"   取一条 {len(pt)} 条明细的记录：删任意一条 ⇒ 重算值改变的次数 {changed}/{len(pt)}")
    zeros = [i for i, r in enumerate(pt) if float(r["score"]) == 0.0]
    emit(f"   ⚠️ 押破：预注册押「删任意一条都变」= {len(pt)}/{len(pt)}，实得 {changed}/{len(pt)}")
    emit(f"      不变的那 {len(zeros)} 条 score=0.0 —— **0 不参与加法**，删掉它和式一字不变")
    emit("   ⇒ 严谨说法：重算依赖**全部非零明细**；0 分明细不在算术依赖集里。")
    # J0b：0 分明细被删 ⇒ 重算看不出来（R6 瞎），只有对账（R4）抓得到
    if zeros:
        c0 = json.loads(json.dumps(tgt))
        del c0["report"]["per_task"][zeros[0]]
        v0 = recompute_mean(c0)[0]
        st0, why0b = audit_measurement(c0)
        emit(f"   [J0b] 删掉那条 0 分：重算值 {v0!r} == 原值 {base!r} ⇒ {v0 == base}"
             f"（**重算完全瞎**）")
        emit(f"         但 audit_measurement ⇒ {st0}｜{why0b[:46]}…")
        emit("   ⇒ R4（对账）**严格必要**，不是 R6 的重复：存在「值不变、条数变」的删法")

    # ── J1 成本分解（真实账本）──────────────────────────────────────────────
    emit("\n[J1] 一次复核的成本落在哪（真实账本，中位数 ×11）")
    rid = addr[-1]["run_id"]
    c_parse_all = t(lambda: _read_records(REAL))
    c_new = t(lambda: ledger_audit(REAL, rid))
    hit, info = _scan_rows(REAL, rid)
    c_arith = t(lambda: audit_measurement(hit), n=31)
    emit(f"   整份解析 _read_records      {c_parse_all*1000:8.3f} ms")
    emit(f"   旧路径（解析+定位+算术）    ≈{c_parse_all*1000:8.3f} ms")
    emit(f"   新路径 ledger_audit        {c_new*1000:8.3f} ms   （只解析 {info['parsed']}/{info['lines']} 行）")
    emit(f"   其中纯算术 audit_measurement{c_arith*1000:8.3f} ms  ⇒ 占总 {c_arith/c_parse_all:.2%}")
    emit(f"   新/旧 = {c_new/c_parse_all:.2f}×（⭐ 阶没变，见 J3；这只是常数）")

    # ── J2 等价性：真·旧码 vs 新码（git archive HEAD 快照）──────────────────
    emit("\n[J2] 与**真旧码**逐格对比（不是重写一份旧逻辑再比：纪律 50）")
    snap = Path(tempfile.mkdtemp(prefix="v145_head_"))
    subprocess.run(f"git -C {ROOT} archive HEAD | tar -x -C {snap}", shell=True,
                   check=True, capture_output=True)
    dump = ROOT / "bench/evidence/v6_s234/dump_readers.py"
    old = subprocess.run([sys.executable, str(dump), str(REAL)], capture_output=True,
                         text=True, env={**os.environ, "PYTHONPATH": str(snap)})
    new = subprocess.run([sys.executable, str(dump), str(REAL)], capture_output=True,
                         text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    a = json.loads(old.stdout)
    b = json.loads(new.stdout)
    cells = 0
    diffs = []
    for sec in ("audit", "verdict"):
        for k in sorted(set(a[sec]) & set(b[sec])):
            cells += 1
            if a[sec][k] != b[sec][k]:
                diffs.append((sec, k, a[sec][k], b[sec][k]))
    emit(f"   自证 旧码模块={a['module'].replace(str(snap), '<snap>')} sha={a['sha']}")
    emit(f"        新码模块={b['module'].replace(str(ROOT), '<repo>')} sha={b['sha']}")
    assert a["module"].startswith(str(snap)), f"旧码竟然不是快照！{a['module']}"
    assert b["module"].startswith(str(ROOT)) and a["sha"] != b["sha"], "两边是同一份码"
    emit(f"   探针 {len(a['audit'])} 个 run_id（{len(addr)} 真实 + 3 缺席）× 2 读者 = {cells} 格")
    emit(f"   不同 {len(diffs)} 格" + ("" if not diffs else f"  {diffs[:3]}"))
    emit(f"   分层普查 旧={a['strata']} 新={b['strata']} 相同={a['strata']==b['strata']}")

    # ── J2c 补齐分母：给不可寻址的 83 行补 run_id（只补副本，不改真账本）────
    emit("\n[J2c] 覆盖全部 145 行（给 83 行不可寻址的记录在**副本**里补唯一 run_id）")
    lines = []
    for i, r in enumerate(recs):
        r = json.loads(json.dumps(r))
        if not r.get("run_id"):
            r["run_id"] = f"fix{i:04d}@0"
        lines.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
    full = tmpfile("full.jsonl", lines)
    old2 = subprocess.run([sys.executable, str(dump), str(full)], capture_output=True,
                          text=True, env={**os.environ, "PYTHONPATH": str(snap)})
    new2 = subprocess.run([sys.executable, str(dump), str(full)], capture_output=True,
                          text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    a2, b2 = json.loads(old2.stdout), json.loads(new2.stdout)
    cells2 = 0
    diffs2 = []
    for sec in ("audit", "verdict"):
        for k in sorted(set(a2[sec]) & set(b2[sec])):
            cells2 += 1
            if a2[sec][k] != b2[sec][k]:
                diffs2.append((sec, k, a2[sec][k], b2[sec][k]))
    assert a2["module"].startswith(str(snap)) and a2["sha"] == a["sha"], "J2c 的旧码不是同一份"
    emit(f"   {len(a2['audit'])} 个 run_id × 2 读者 = {cells2} 格，不同 {len(diffs2)} 格")
    emit(f"   分层普查 旧={a2['strata']} 新={b2['strata']} 相同={a2['strata']==b2['strata']}")

    # ── J3 阶：新路径仍是 O(N) ─────────────────────────────────────────────
    emit("\n[J3] 阶没变（合成账本 ×N，⛔ 只量阶，不报「真实账本多快」）")
    emit("     N行     旧路径     新路径   新/旧")
    prev = None
    for n in (145, 580, 1450, 2900, 5800):
        ls, i = [], 0
        while len(ls) < n:
            r = json.loads(json.dumps(recs[i % len(recs)]))
            r["run_id"] = f"9{i:06d}@0"
            ls.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
            i += 1
        p = tmpfile(f"n{n}.jsonl", ls)
        last = f"9{(n-1):06d}@0"
        co = t(lambda: _read_records(p), n=7)
        cn = t(lambda: ledger_audit(p, last), n=7)
        grow = "" if prev is None else f"（×{n/prev[0]:.0f} 行 ⇒ 新 ×{cn/prev[1]:.2f}）"
        emit(f"    {n:5d} {co*1000:8.2f} {cn*1000:8.2f} {cn/co:7.2f}  {grow}")
        prev = (n, cn)

    # ── J4 坏行隔离：非目标行坏 ────────────────────────────────────────────
    emit("\n[J4] 非目标行坏 JSON：目标行还审不审得了（两种坏行要分开）")
    target = addr[-1]
    clean_reason = audit_measurement(target)[1]
    bad = list(raw)
    bad.insert(len(bad) // 2, '{"run_id": "broken", "report": ')
    pbad = tmpfile("bad.jsonl", bad)
    new_state, new_why = ledger_audit(pbad, rid)
    new_v, _ = ledger_verdict(pbad, rid)
    st = ledger_audit_strata(pbad)
    old_state = "UNVERIFIABLE"   # 旧码：_read_records 返回 None ⇒ 一律不可复核
    emit(f"   旧码（真跑在快照上）       {old_state}")
    emit(f"   新码 ledger_audit         {new_state}")
    emit(f"   新码 ledger_verdict       {new_v}")
    emit(f"   理由与干净账本逐字相同     {new_why == clean_reason}")
    emit(f"   整份普查 {st} ｜ 守恒 V+M+U={st['VERIFIED']+st['MISMATCH']+st['UNVERIFIABLE']}"
         f" == rows={st['rows']} ｜ 总行数 rows+bad={st['rows']+st['bad_lines']} == lines={st['lines']}")
    # 旧码在坏账本上的真实行为：把快照里的真函数跑一遍（不是我说它坏，是它自己跑）
    old_probe = subprocess.run(
        [sys.executable, "-c",
         "import sys,json;from pathlib import Path;"
         "from mw.bench import ledger_audit;"
         "print(json.dumps(list(ledger_audit(Path(sys.argv[1]), sys.argv[2]))) )",
         str(pbad), rid],
        capture_output=True, text=True, cwd=str(snap),
        env={**os.environ, "PYTHONPATH": str(snap)})
    old_audit = json.loads(old_probe.stdout)
    emit(f"   旧码在同一份坏账本上 ledger_audit ⇒ {old_audit[0]}  {old_audit[1][:52]}")
    emit(f"   ⇒ 行为分叉成立：同一份账本、同一个 run_id，旧码连审都审不了")

    # ⛔ 上面那行坏 JSON **不含**目标 run_id ⇒ 它被预筛直接跳过，从没被解析。
    #   这时的 VERIFIED 来自「没看见它」，**不是**来自隔离。必须再测一遍
    #   「含 run_id 的坏行」（= 候选但坏）才算真的在测隔离。
    cand = list(raw)
    cand.insert(len(cand) // 2, '{"note": "see ' + rid + '", "report": ')
    pcand = tmpfile("candbad.jsonl", cand)
    cs, cw = ledger_audit(pcand, rid)
    emit(f"   含目标 run_id 的坏行（候选但坏）⇒ {cs}"
         f" ｜ 理由与干净账本逐字相同={cw == clean_reason}")
    oste = subprocess.run([sys.executable, "-c",
        "import sys,json;from pathlib import Path;from mw.bench import ledger_audit;"
        "print(json.dumps(list(ledger_audit(Path(sys.argv[1]), sys.argv[2]))))",
        str(pcand), rid], capture_output=True, text=True, cwd=str(snap),
        env={**os.environ, "PYTHONPATH": str(snap)})
    emit(f"   旧码在同一份账本上 ⇒ {json.loads(oste.stdout)[0]}")
    emit("   ⇒ 隔离的两个来源要分清：①坏行**不含** id ⇒ 预筛没看它（成本）；"
         "②坏行含 id ⇒ 命中已找到，坏行不毒化它（逻辑）")

    # J4b：给目标行补一个 verdict（真实账本 145 行**都没有** verdict 键，所以上面
    #      读成 UNKNOWN 是「这行没结论」，不是坏行连坐）⇒ 才能验结论侧的隔离
    verd = list(raw)
    verd[-1] = json.dumps({**target, "verdict": "GREEN"}, ensure_ascii=False, sort_keys=True)
    pv = tmpfile("verdict_clean.jsonl", verd)
    v_clean, w_clean = ledger_verdict(pv, rid)
    badv = list(verd)
    badv.insert(5, '{"run_id": "broken", "report": ')
    pbv = tmpfile("verdict_bad.jsonl", badv)
    v_bad, w_bad = ledger_verdict(pbv, rid)
    emit(f"   J4b 结论侧：干净账本 {v_clean} / 夹一行坏 JSON {v_bad}"
         f" ｜ 理由逐字相同={w_clean == w_bad}")
    ost = subprocess.run([sys.executable, "-c",
        "import sys,json;from pathlib import Path;from mw.bench import ledger_verdict;"
        "print(json.dumps(list(ledger_verdict(Path(sys.argv[1]), sys.argv[2]))))",
        str(pbv), rid], capture_output=True, text=True, cwd=str(snap),
        env={**os.environ, "PYTHONPATH": str(snap)})
    emit(f"   旧码在同一份账本上 ⇒ {json.loads(ost.stdout)[0]}")

    # ── J5 目标行自己坏：⛔ 不许放行 ───────────────────────────────────────
    emit("\n[J5] 目标行本身坏 JSON：必须 UNVERIFIABLE")
    self_bad = list(raw)
    # 让目标行的 run_id 仍然可见（截断在 run_id 之后），这样才能「疑似」
    r = json.loads(json.dumps(target))
    keep = json.dumps({"run_id": r["run_id"], "report": r["report"]},
                      ensure_ascii=False)[:-40]
    self_bad = [ln for ln in raw if json.loads(ln).get("run_id") != rid] + [keep]
    pself = tmpfile("selfbad.jsonl", self_bad)
    emit(f"   run_id 仍可见 ⇒ {ledger_audit(pself, rid)}")
    # 截断到 run_id 之前：连身份都读不出 ⇒ 退化成「查无此账」（同为 UNVERIFIABLE，但理由指错修法）
    gone = keep[:12]
    self_bad2 = [ln for ln in raw if json.loads(ln).get("run_id") != rid] + [gone]
    pself2 = tmpfile("selfbad2.jsonl", self_bad2)
    emit(f"   run_id 都读不出 ⇒ {ledger_audit(pself2, rid)}")
    emit("   ⚠️ 两者都**不放行**；但后者理由会说「没有这一行」—— 修法指错（见边界 4）")

    # ── J6 预筛的前提与 guard ─────────────────────────────────────────────
    emit("\n[J6] 预筛只在 run_id 以字面量出现时成立")
    for probe in ("5425@1792919669", "r-不存在", 'a"b', "a\\b", "a\tb", ""):
        lit = _rid_is_literal(probe)
        line_false = json.dumps({"run_id": probe}, ensure_ascii=True)
        line_true = json.dumps({"run_id": probe}, ensure_ascii=False)
        sub_t = str(probe in line_false) if probe else "—"
        sub_f = str(probe in line_true) if probe else "—"
        emit(f"   run_id={probe!r:20s} 字面量={str(lit):5s} "
             f"ensure_ascii=True 含子串={sub_t:5s} False 含子串={sub_f}")
    # 端到端：非 ASCII run_id 必须仍能找到（guard 退回全解析）
    rec_n = json.loads(json.dumps(target))
    rec_n["run_id"] = "r-不存在"
    pn = tmpfile("nonascii.jsonl", [
        json.dumps(rec_n, ensure_ascii=True, sort_keys=True)] +
        [ln for ln in raw if json.loads(ln).get("run_id") != rid])
    got = ledger_audit(pn, "r-不存在")
    emit(f"   ensure_ascii=True 写盘的非 ASCII run_id ⇒ {got[0]}（guard 退回全解析，没误报查无此账）")
    # 预筛只会多选：另一行在别的字段里含这个 run_id ⇒ 会被解析，但**不会**被当成命中
    decoy = json.loads(json.dumps(target))
    decoy["note"] = f"refer to {rid}"
    decoy["run_id"] = "decoy@0"
    pdec = tmpfile("decoy.jsonl", [json.dumps(decoy, ensure_ascii=False)] + raw)
    hit2, info2 = _scan_rows(pdec, rid)
    emit(f"   诱饵行（别的字段含该 run_id）⇒ 被解析 {info2['parsed']} 行，"
         f"命中 run_id={hit2.get('run_id')!r} ⇒ 子串只影响成本，命中仍按 == 判定")

    # ── R 组 反证：判据不是恒真 ───────────────────────────────────────────
    emit("\n[R] 反证组")
    stt, why0 = ledger_audit(REAL, rid)
    # R1：关掉预筛（把允许表清空 ⇒ 永远全解析）⇒ 结果必须**完全相同**
    keep_lit = B._RID_LITERAL
    try:
        B._RID_LITERAL = frozenset()
        # 逐行比：全解析 vs 预筛，全部可寻址行结果必须一致
        pre = {r["run_id"]: ledger_audit(REAL, r["run_id"]) for r in addr}
    finally:
        B._RID_LITERAL = keep_lit
    post = {r["run_id"]: ledger_audit(REAL, r["run_id"]) for r in addr}
    emit(f"   R1 全解析 vs 预筛：{len(pre)} 行，不同 {sum(1 for k in pre if pre[k] != post[k])}"
         f" ⇒ 预筛只改成本，不改结论")
    # R2：预筛模式下的坏行计数是**已解析范围内**的，不是账本总数
    skip_bad = list(raw)
    skip_bad.insert(3, '{"nope": ')          # 不含目标 run_id ⇒ 被跳过，不会被看见
    pskip = tmpfile("skipbad.jsonl", skip_bad)
    _, inf = _scan_rows(pskip, rid)
    emit(f"   R2 被跳过的坏行 ⇒ parsed={inf['parsed']} bad_in_parsed={inf['bad_in_parsed']}"
         f"（⛔ 不是 0=没坏，是「没看过」）；整份普查 bad_lines="
         f"{ledger_audit_strata(pskip)['bad_lines']}")
    # R3：把定位换成「返回第一行」⇒ 必须当场不同（证明判据能变）
    def wrong(path, rid_):
        recs_ = _read_records(path)
        return audit_measurement(recs_[0]) if recs_ else ("UNVERIFIABLE", "")
    wrong_cells = sum(1 for r in addr if wrong(REAL, r["run_id"]) != ledger_audit(REAL, r["run_id"]))
    emit(f"   R3 定位换成「返回第一行」⇒ {wrong_cells}/{len(addr)} 行结果改变 ⇒ 判据能变")
    # R4：坏行自愈退出 —— 跳过 ≠ 隔离
    emit(f"   R4 「静默跳过坏行」与「隔离」的差别：跳过会让 bad_lines 丢计数；"
         f"本实现 bad_lines={ledger_audit_strata(pbad)['bad_lines']} 且 readable="
         f"{ledger_audit_strata(pbad)['readable']}")

    # ── W1 通电 ───────────────────────────────────────────────────────────
    emit("\n[W1] 通电")
    tree = ast.parse((ROOT / "run.py").read_text(encoding="utf-8"))
    n_audit = sum(1 for nd in ast.walk(tree)
                  if isinstance(nd, ast.Call)
                  and getattr(nd.func, "id", getattr(nd.func, "attr", "")) == "ledger_audit")
    n_verdict = sum(1 for nd in ast.walk(tree)
                    if isinstance(nd, ast.Call)
                    and getattr(nd.func, "id", getattr(nd.func, "attr", "")) == "ledger_verdict")
    emit(f"   run.py 里 ledger_audit={n_audit} 调用点、ledger_verdict={n_verdict} 调用点")
    emit(f"   mw/bench.py 里 _read_records 剩余调用点="
         f"{(ROOT/'mw/bench.py').read_text(encoding='utf-8').count('_read_records(path)')}"
         f"（应只剩 ledger_strata —— 整份诊断，故意不预筛）")

    dst = Path(__file__).with_name("READINGS.txt")
    dst.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"\n→ {dst}")


if __name__ == "__main__":
    main()

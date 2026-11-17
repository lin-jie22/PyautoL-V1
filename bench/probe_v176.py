#!/usr/bin/env python3
"""probe_v176 —— 表现型空间扩容（sys_small）主实验。判据 = PREREG_v176 §2。

P1 套件结构 / P2 校准（真尺子）/ P3 准入 A1~A6 / P4 变异体有牙 /
P5 准入器有牙（四种坏读数必须全拒）/ P6 部署态（oracle 参照真跑）/ P7 确定性。

⛔ 判据写在 PREREG 里，本文件只负责执行与读数落盘。
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

EV = ROOT / "bench" / "evidence" / "l0_s264"
SUITE_PATH = ROOT / "bench" / "tasks" / "sys_small.jsonl"
SCORER_PATH = ROOT / "bench" / "scorers" / "code_exec.py"

RESULTS: list[dict] = []


def check(tag: str, claim: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"tag": tag, "claim": claim, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {tag}  {claim}")
    if detail:
        print(f"         {detail}")
    return bool(ok)


def main() -> int:
    t0 = time.time()
    print("═" * 74)
    print("probe_v176  表现型空间扩容：sys_small（PREREG_v176 §2）")
    print("═" * 74)

    from mw.bench import ExternalScorer, Suite, calibrate
    sys.path.insert(0, str(ROOT / "bench"))
    import admit_sys as AS

    scorer = ExternalScorer([sys.executable, str(SCORER_PATH)], source=SCORER_PATH)
    suite = Suite.load(SUITE_PATH)

    # ── P1 套件结构 ────────────────────────────────────────────────
    print("\n[P1] 套件结构")
    ids = suite.ids()
    meta_ok = all(dict(t.meta).get("format") in ("single", "files")
                  and dict(t.meta).get("kind") in ("stateful", "pure")
                  for t in suite.tasks)
    mat_ok = all(t.oracle.strip() and t.partial.strip() and t.null == ""
                 for t in suite.tasks)
    files_tests_ok = all(
        all("FILES" in x for x in t.tests) if dict(t.meta).get("format") == "files"
        else all("FILES" not in x for x in t.tests)
        for t in suite.tasks)
    check("P1a", "Suite.load 6 题、id 唯一、三臂材料齐（oracle/partial 非空、null 空）",
          len(suite) == 6 and len(set(ids)) == 6 and mat_ok,
          f"n={len(suite)} ids={ids} 材料齐={mat_ok}")
    check("P1b", "meta 词表封闭：format∈{single,files}、kind∈{stateful,pure}",
          meta_ok, "｜".join(f"{t.id}:{dict(t.meta).get('format')}/{dict(t.meta).get('kind')}"
                             for t in suite.tasks))
    check("P1c", "测试形态与 format 对齐：files 题测试含 FILES 物化、single 题不引用 FILES",
          files_tests_ok, f"对齐={files_tests_ok}")

    # ── P2 校准（真尺子）────────────────────────────────────────────
    print("\n[P2] 三臂校准（真 code_exec）")
    cal = calibrate(scorer, suite)
    per = {r["id"]: r for r in cal.per_task}
    mid_all = all(per[t.id]["null"] < per[t.id]["partial"] < per[t.id]["oracle"] == 1.0
                  for t in suite.tasks)
    check("P2a", "calibrate 过：6/6 两极可分 + 中段严格有序 + oracle 全满分",
          cal.ok and mid_all and cal.middle_n == 6,
          f"margin={cal.margin:.4f} middle_n={cal.middle_n} 逐题="
          + "｜".join(f"{t.id} {per[t.id]['oracle']:.2f}/{per[t.id]['partial']:.2f}/{per[t.id]['null']:.2f}"
                      for t in suite.tasks))

    # ── P3 + P4 准入与变异体有牙 ─────────────────────────────────────
    print("\n[P3/P4] 准入 A1~A6 与变异体有牙")
    adm = AS.admit(SUITE_PATH)
    check("P3", "admit() 全绿（A1 oracle 满分、A2 null=0、A3 中段严格、A4 变异体全被抓、"
                "A5 复打分逐位相同、A6 注册表覆盖）",
          adm["ok"], "failed=" + json.dumps(adm["failed"], ensure_ascii=False))

    stateful = [t.id for t in suite.tasks if dict(t.meta).get("kind") == "stateful"]
    files_ids = [t.id for t in suite.tasks if dict(t.meta).get("format") == "files"]
    rd = adm["readings"]
    wipe_rows, flat_rows = [], []
    all_below = True
    for t in suite.tasks:
        for name, score in sorted(rd[t.id]["mutants"].items()):
            if score >= rd[t.id]["oracle"] - 1e-9:
                all_below = False
            if name.startswith("wipe_"):
                wipe_rows.append((t.id, name, score))
            if name.startswith("flat_"):
                flat_rows.append((t.id, name, score))
    wipe_ok = all(s <= 0.9 + 1e-9 for tid, n, s in wipe_rows if tid in stateful)
    flat_ok = all(abs(s - 0.0) <= 1e-9 for tid, n, s in flat_rows) and \
        {tid for tid, n, s in flat_rows} == set(files_ids)
    check("P4a", "每个注册变异体得分 < oracle（反真空）",
          all_below,
          "｜".join(f"{tid}:{n}={s:.2f}" for tid, n, s in wipe_rows + flat_rows))
    check("P4b", "wipe_* 对有状态题掉分 ≤0.9（状态/行为语义被测到）",
          wipe_ok and len([r for r in wipe_rows if r[0] in stateful]) >= len(stateful),
          "｜".join(f"{tid}:{n}={s:.2f}" for tid, n, s in wipe_rows if tid in stateful))
    check("P4c", "flat_* 对 files 题恰好 0.0 且逐题覆盖（格式强制：语义等价单文件答案拿 0）",
          flat_ok,
          "｜".join(f"{tid}:{n}={s:.2f}" for tid, n, s in flat_rows))

    # ── P5 准入器有牙（四种坏读数必须全拒）──────────────────────────
    print("\n[P5] 准入器有牙（A7 反证：坏读数必须被判拒）")
    base = copy.deepcopy(rd)

    def reject(name: str, mutate) -> bool:
        bad = copy.deepcopy(base)
        mutate(bad)
        v = AS.verdict(bad)
        ok = (not v["ok"]) and v["failed"]
        detail = v["failed"][0]["why"][:60] if v["failed"] else "（居然通过了）"
        return check(f"P5-{name}", f"坏读数 {name} ⇒ verdict 必须拒", ok, detail)

    p5a = reject("oracle≠1.0", lambda b: b["s01_kv_expiry"].__setitem__("oracle", 0.99))
    p5b = reject("partial==null", lambda b: b["s02_limiter"].__setitem__(
        "partial", b["s02_limiter"]["null"]))
    p5c = reject("变异体==oracle", lambda b: b["m03_tiered"]["mutants"].__setitem__(
        "wipe_state", b["m03_tiered"]["oracle"]))
    def _drop(b):
        b.pop("m02_vfs")
    p5d = reject("注册表漏题", _drop)
    check("P5", "四种坏读数全部判拒（准入器不是只输出 ✅ 的装饰）",
          all((p5a, p5b, p5c, p5d)), f"a={p5a} b={p5b} c={p5c} d={p5d}")

    # ── P6 部署态（隔离目录，oracle 参照真跑）────────────────────────
    print("\n[P6] 部署态：run.py --bench --offline --solver oracle --suite sys_small")
    st = Path("/tmp/v1_sys_probe_state")
    bh = Path("/tmp/v1_sys_probe_bench")
    for d in (st, bh):
        if d.exists():
            import shutil
            shutil.rmtree(d)
        d.mkdir(parents=True)
    env = dict(os.environ, V1_STATE_DIR=str(st), V1_BENCH_DIR=str(bh))
    proc = subprocess.run(
        [sys.executable, "run.py", "--bench", "--offline", "--solver", "oracle",
         "--suite", "bench/tasks/sys_small.jsonl"],
        capture_output=True, text=True, timeout=600, env=env, cwd=str(ROOT))
    out = proc.stdout
    dep_ok = (proc.returncode == 0 and "可信=✅" in out and "mean=1.0000" in out
              and "VERIFIED" in out)
    check("P6", "部署态：退出码 0、oracle 逐题满分（mean=1.0000）、可信=✅、读数复核 VERIFIED",
          dep_ok,
          f"rc={proc.returncode} 可信={'可信=✅' in out} "
          f"mean1={'mean=1.0000' in out} verified={'VERIFIED' in out}")

    # ── P7 确定性 ───────────────────────────────────────────────────
    print("\n[P7] 确定性")
    v1 = AS.verdict(rd)
    v2 = AS.verdict(rd)
    pure_ok = v1 == v2
    adm2 = AS.admit(SUITE_PATH)
    same = adm2["readings"] == rd
    check("P7a", "verdict 纯函数双调用逐字相同",
          pure_ok, f"相同={pure_ok}")
    check("P7b", "admission 双跑 readings 逐字相同（分数确定性）",
          same and adm2["ok"],
          f"readings 相同={same} 二跑 ok={adm2['ok']}"
          + ("" if same else "｜差异=" + json.dumps(
              {k: (rd.get(k), adm2['readings'].get(k)) for k in set(rd) | set(adm2['readings'])
               if rd.get(k) != adm2['readings'].get(k)}, ensure_ascii=False)[:300]))

    # ── 落盘 ────────────────────────────────────────────────────────
    verdicts = {"verdicts": RESULTS, "readings": rd,
                "calibrate": {"ok": cal.ok, "margin": cal.margin,
                              "middle_n": cal.middle_n, "per_task": cal.per_task},
                "deploy_rc": proc.returncode,
                "wall_s": round(time.time() - t0, 3)}
    (EV / "readings_v176.json").write_text(
        json.dumps(verdicts, ensure_ascii=False, indent=2), encoding="utf-8")
    n_pass = sum(1 for r in RESULTS if r["ok"])
    print(f"\n{'═' * 74}\n判据 {n_pass}/{len(RESULTS)} PASS"
          f"｜读数落盘 {EV / 'readings_v176.json'}")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

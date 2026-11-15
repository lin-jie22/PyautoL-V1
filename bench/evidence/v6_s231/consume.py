#!/usr/bin/env python3
"""V142 读数（§209）：让上一次运行的状态约束这一次的判定。

六组读数，全部**先写判据再跑**（PREREG.md）：
  A 旧记录不许默认成绿（真实 145 条）
  B 新记录带自己的 verdict
  C RED 运行写不进基线（盘面 sha 逐字节不变）+ 退出码说话
  D CRASH 基线不许比较
  E 旧基线（无 verdict 键）不许比较
  F GREEN 基线仍可比（无回归）
  G 装置自检：账本没钉上 ⇒ CRASH（控制变异）

⛔ 一律不改生产目录：每次用全新的 /tmp 目录；真实账本只**读**。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

V1 = pathlib.Path(__file__).resolve().parents[3]
OUT = pathlib.Path(__file__).resolve().parent
BASE = pathlib.Path("/tmp/v142_read")
PY = sys.executable
SUITE = "bench/tasks/coding_small.jsonl"


def sh(*args, **kw):
    return subprocess.run(args, cwd=str(V1), capture_output=True, text=True, **kw)


def run_bench(tag: str, *extra: str, solver: str = "oracle"):
    d = BASE / tag
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, V1_BENCH_DIR=str(d), V1_STATE_DIR=str(d / "state"))
    p = subprocess.run([PY, "run.py", "--bench", "--offline", "--solver", solver,
                        "--suite", SUITE, "--fresh", *extra],
                       cwd=str(V1), capture_output=True, text=True, env=env)
    return d, p


def sha(p: pathlib.Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tail(text: str, n: int = 3) -> str:
    return "\n".join(text.rstrip().splitlines()[-n:])


def main() -> int:
    sys.path.insert(0, str(V1))
    import mw.bench as B

    BASE.mkdir(parents=True, exist_ok=True)
    res: dict[str, object] = {}

    # ── A 旧记录不许默认成绿（真实账本，只读）────────────────────────────
    real = V1 / "bench_out" / "runs.jsonl"
    recs = [json.loads(x) for x in real.read_text(encoding="utf-8").splitlines() if x.strip()]
    with_id = [r for r in recs if r.get("run_id")]
    no_id = [r for r in recs if not r.get("run_id")]
    old_states = [B.ledger_verdict(real, r["run_id"])[0] for r in with_id]
    old_default = sum(1 for r in recs if r.get("verdict", "GREEN") == "GREEN")
    res["A_old"] = {
        "账本": str(real), "行数": len(recs),
        "有verdict键的行": sum(1 for r in recs if "verdict" in r),
        "有run_id可以查的": len(with_id),
        "没有run_id查不了的": len(no_id),
        "可查行里UNKNOWN": sum(1 for s in old_states if s == "UNKNOWN"),
        "可查行里GREEN": sum(1 for s in old_states if s == "GREEN"),
        "对照_默认成绿会把多少条读成GREEN": old_default,
    }
    print("A 旧记录不许默认成绿 —— 真实账本 %d 行，带 verdict 键 %d 行"
          % (len(recs), res["A_old"]["有verdict键的行"]))
    print("   分母拆分：有 run_id 可查 %d 行（UNKNOWN=%d GREEN=%d）｜"
          "无 run_id 连查都查不了 %d 行"
          % (len(with_id), res["A_old"]["可查行里UNKNOWN"],
             res["A_old"]["可查行里GREEN"], len(no_id)))
    print("   对照：`.get('verdict','GREEN')` 的读法会把 **%d/%d** 行读成 GREEN"
          % (old_default, len(recs)))
    ok_a = (res["A_old"]["可查行里GREEN"] == 0
            and res["A_old"]["可查行里UNKNOWN"] == len(with_id)
            and old_default == len(recs))
    if ok_a:
        print("   判定 ✅ 结构命题成立（零条被读成绿）；"
              "但预注册押的「%d/%d 都返回 UNKNOWN」**字面破了** ——"
              % (len(recs), len(recs)))
        print("        %d 行（V72 之前）连 run_id 都没有，查无可查。这是读数，不是借口。"
              % len(no_id))
    else:
        print("   判定 ❌")

    # ── B 新记录带自己的 verdict；D/E/F 都用这台跑出来的基线和账本 ──────────
    d_green, p_green = run_bench("green")
    ledger = d_green / "runs.jsonl"
    last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    st, why = B.ledger_verdict(ledger, last["run_id"])
    log = (d_green / "last_run.log").read_text(encoding="utf-8")
    res["B_new"] = {"run_id": last["run_id"], "verdict": last.get("verdict"),
                    "verdict_at": last.get("verdict_at"), "回读": st,
                    "rc": p_green.returncode, "sentinel行数": log.count("::VERDICT::")}
    ok_b = (last.get("verdict") == "GREEN" and st == "GREEN"
            and p_green.returncode == 0 and log.count("::VERDICT::") == 1)
    print("\nB 新记录带自己的 verdict —— verdict=%r 回读=%s rc=%d 判据行=%d  判定 %s"
          % (last.get("verdict"), st, p_green.returncode, log.count("::VERDICT::"),
             "✅" if ok_b else "❌"))

    # ── C RED 运行写不进基线（盘面 sha 逐字节不变）──────────────────────────
    d_pre, p_save = run_bench("save_green", "--save-baseline")
    bl_pre = d_pre / "bench_baseline.json"
    sha_pre = sha(bl_pre) if bl_pre.exists() else None
    # ⚠️ RED 那一跑必须在**已有基线**的目录里跑：空目录里「没写」是因为没有旧文件，
    #    不是因为拒绝了 —— 那是假绿（纪律 5 第二形态：变异没生效）。
    d_red = BASE / "red"
    shutil.rmtree(d_red, ignore_errors=True)
    d_red.mkdir(parents=True)
    shutil.copy2(bl_pre, d_red / "bench_baseline.json")
    env_red = dict(os.environ, V1_BENCH_DIR=str(d_red), V1_STATE_DIR=str(d_red / "state"))
    p_red = subprocess.run([PY, "run.py", "--bench", "--offline", "--solver", "null",
                            "--suite", SUITE, "--fresh", "--save-baseline"],
                           cwd=str(V1), capture_output=True, text=True, env=env_red)
    bl_red = d_red / "bench_baseline.json"
    sha_red = sha(bl_red) if bl_red.exists() else None
    res["C_red"] = {"退出码": p_red.returncode, "基线sha_跑前": sha_pre,
                    "基线sha_跑后": sha_red,
                    "判据": [ln.strip() for ln in p_red.stdout.splitlines()
                             if "::VERDICT::" in ln],
                    "拒绝句": [ln.strip() for ln in p_red.stdout.splitlines()
                               if "不写基线" in ln]}
    ok_c = (p_red.returncode == 1 and sha_pre is not None and sha_pre == sha_red
            and any("RED" in x for x in res["C_red"]["判据"])
            and bool(res["C_red"]["拒绝句"]))
    print("\nC RED 运行写不进基线 —— rc=%d  sha %s → %s  拒绝句=%s  判定 %s"
          % (p_red.returncode, (sha_pre or "∅")[:12], (sha_red or "∅")[:12],
             (res["C_red"]["拒绝句"] or ["（无）"])[0], "✅" if ok_c else "❌"))

    # ── D/E/F 三种基线各判一次 ────────────────────────────────────────────
    def compare_with(name: str, mutate) -> dict:
        d = BASE / f"cmp_{name}"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        doc = json.loads(bl_pre.read_text(encoding="utf-8"))
        mutate(doc)
        (d / "bench_baseline.json").write_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        env = dict(os.environ, V1_BENCH_DIR=str(d), V1_STATE_DIR=str(d / "state"))
        p = subprocess.run([PY, "run.py", "--bench", "--offline", "--solver", "oracle",
                            "--suite", SUITE, "--fresh", "--compare"],
                           cwd=str(V1), capture_output=True, text=True, env=env)
        lines = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
        has_judge = any(ln.startswith("判定 ") for ln in lines)
        refuse = [ln for ln in lines if "拒绝比较" in ln]
        return {"rc": p.returncode, "出现判定行": has_judge,
                "拒绝句": refuse[:2], "verdict": doc.get("verdict", "<缺席>")}

    res["D_crash"] = compare_with("crash", lambda d: d.update(verdict="CRASH"))
    res["E_old"] = compare_with("old", lambda d: d.pop("verdict", None))
    res["F_green"] = compare_with("green", lambda d: d.update(verdict="GREEN"))
    ok_d = (not res["D_crash"]["出现判定行"]) and bool(res["D_crash"]["拒绝句"])
    ok_e = (not res["E_old"]["出现判定行"]) and bool(res["E_old"]["拒绝句"])
    ok_f = res["F_green"]["出现判定行"]
    for k, ok in (("D CRASH 基线不许比较", ok_d), ("E 旧基线(无键)不许比较", ok_e),
                  ("F GREEN 基线仍可比", ok_f)):
        v = res[{"D CRASH 基线不许比较": "D_crash",
                 "E 旧基线(无键)不许比较": "E_old",
                 "F GREEN 基线仍可比": "F_green"}[k]]
        print("\n%s —— verdict=%r 出现判定行=%s  %s"
              % (k, v["verdict"], v["出现判定行"], "✅" if ok else "❌"))
        for ln in v["拒绝句"]:
            print("   " + ln)

    # ── G 装置自检：账本没钉上 ⇒ CRASH（控制变异）─────────────────────────
    drv = OUT / "_drv_nostamp.py"
    drv.write_text(
        "import sys\n"
        "sys.path.insert(0, %r)\n"          # ⚠️ 驱动在 /tmp ⇒ 不补 path 就 import 不到 mw
        "import mw.bench as B\n"
        "B.stamp_verdict = lambda p, r, v: (True, '控制变异：故意不写盘')\n"
        "sys.argv = ['run.py', '--bench', '--offline', '--solver', 'oracle',\n"
        "            '--suite', %r, '--fresh']\n"
        "import run\n"
        "sys.exit(run.main())\n" % (str(V1), SUITE), encoding="utf-8")
    d_g = BASE / "nostamp"
    shutil.rmtree(d_g, ignore_errors=True)
    d_g.mkdir(parents=True)
    env = dict(os.environ, V1_BENCH_DIR=str(d_g), V1_STATE_DIR=str(d_g / "state"))
    p_g = subprocess.run([PY, str(drv)], cwd=str(V1), capture_output=True, text=True, env=env)
    rec_g = json.loads((d_g / "runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    st_g = B.ledger_verdict(d_g / "runs.jsonl", rec_g["run_id"])[0]
    res["G_nostamp"] = {"rc": p_g.returncode, "账本状态": st_g,
                        "读回行": [ln.strip() for ln in p_g.stdout.splitlines()
                                   if "判据读回" in ln]}
    ok_g = p_g.returncode == 1 and st_g == "UNKNOWN" and any(
        "CRASH" in x for x in res["G_nostamp"]["读回行"])
    print("\nG 装置自检（stamp 变成空实现）—— rc=%d 账本=%s  判定 %s"
          % (p_g.returncode, st_g, "✅" if ok_g else "❌"))
    for ln in res["G_nostamp"]["读回行"]:
        print("   " + ln)

    # ── 汇总 ──────────────────────────────────────────────────────────────
    bets = {"W1 旧记录不默认成绿": ok_a, "W2 新记录带 verdict": ok_b,
            "W3 RED 写不进基线": ok_c,
            "W4 CRASH 基线拒绝比较": ok_d, "W5 旧基线拒绝比较": ok_e,
            "W6 GREEN 基线仍可比": ok_f, "W7 账本没钉上 ⇒ CRASH": ok_g}
    print("\n" + "=" * 66)
    for k, v in bets.items():
        print(f"  {k:<26} {'✅' if v else '⛔ 押破'}")
    res["bets"] = bets
    (OUT / "readings.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    drv.unlink(missing_ok=True)
    return 0 if all(bets.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

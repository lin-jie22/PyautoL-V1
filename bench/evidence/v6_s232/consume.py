#!/usr/bin/env python3
"""V143（§210）读数：账本才是权威 —— 基线的可信度必须由账本复核。

五个押注都在真跑的进程里取，不用合成数据（除 W1 的普查本身是读真实账本）。
脚本自包含：每次从空目录起，跑完不写仓库里的任何活文件。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent.parent.parent
OUT = Path(__file__).resolve().parent
SUITE = "bench/tasks/coding_small.jsonl"
RES: dict = {}


def run(tag: str, *extra: str, solver: str = "oracle",
        bench_dir: Path | None = None) -> tuple[Path, subprocess.CompletedProcess]:
    d = bench_dir or (Path("/tmp") / f"v143_{tag}")
    if bench_dir is None and d.exists():
        shutil.rmtree(d)
    bd, sd = d / "bench", d / "state"
    env = dict(os.environ, V1_BENCH_DIR=str(bd), V1_STATE_DIR=str(sd))
    p = subprocess.run([sys.executable, "run.py", "--bench", "--offline",
                        "--solver", solver, "--suite", SUITE, "--fresh", *extra],
                       cwd=V1, env=env, capture_output=True, text=True)
    return d, p


def lines(p: subprocess.CompletedProcess, *keys: str) -> list[str]:
    out = []
    for ln in (p.stdout + p.stderr).splitlines():
        if any(k in ln for k in keys):
            out.append(ln.strip())
    return out


#: ⛔ 判据行必须**按形状**认，不能按「出现了这两个字」认。
#:    实测踩过：`判据 分母=1 道闸（读数可否用于**判定**）` 这行也含「判定」，
#:    于是「拒绝比较」的三格全被读成「出了判定」—— 检测器不认识自己的目标
#:    （纪律 5 第三形态，第二次在同一个词上栽）。
_VERDICT_LINE = re.compile(r"(?m)^\s*判定 (?:改进成立|不成立) —")


def has_verdict_line(txt: str) -> bool:
    return bool(_VERDICT_LINE.search(txt))


# ── W1 账本分层普查（真实账本，只读）────────────────────────────────────
sys.path.insert(0, str(V1))
from mw.bench import ledger_strata  # noqa: E402

real = V1 / "bench_out" / "runs.jsonl"
st = ledger_strata(real)
cons = st["no_run_id"] + st["GREEN"] + st["RED"] + st["CRASH"] + st["UNKNOWN"]
recs = [json.loads(x) for x in real.read_text(encoding="utf-8").splitlines() if x.strip()]
old_green = sum(1 for r in recs if r.get("verdict", "GREEN") == "GREEN")
RES["W1"] = {
    "账本": str(real), **st,
    "守恒": cons, "守恒成立": cons == st["rows"],
    "对照_默认成绿读法": old_green,
    "基线文件存在": (V1 / "bench_out" / "bench_baseline.json").exists(),
}
print("W1 账本分层普查（真实账本 %d 行）" % st["rows"])
print("   无 run_id %d ｜ GREEN %d ｜ RED %d ｜ CRASH %d ｜ UNKNOWN %d ｜ 守恒 %s"
      % (st["no_run_id"], st["GREEN"], st["RED"], st["CRASH"], st["UNKNOWN"],
         "✅" if cons == st["rows"] else f"❌ {cons}≠{st['rows']}"))
print("   对照：`.get('verdict','GREEN')` 会把 %d/%d 行读成 GREEN" % (old_green, st["rows"]))
print("   判定 %s" % ("✅ 分层自洽，且历史里没有一条能自证量到了东西"
                      if (cons == st["rows"] and st["GREEN"] == 0
                          and st["UNKNOWN"] == st["rows"] - st["no_run_id"])
                      else "（见上表）"))

# ── W4 正例：GREEN 运行写基线 → 账本复核通过 → 出现「判定」────────────
d4, p4 = run("w4", "--save-baseline")
bl4 = d4 / "bench" / "bench_baseline.json"
b4 = json.loads(bl4.read_text(encoding="utf-8")) if bl4.exists() else {}
_, p4c = run("w4", "--compare", bench_dir=d4)   # 同目录，不重建
t4 = p4c.stdout + p4c.stderr
RES["W4"] = {"rc_save": p4.returncode, "rc_compare": p4c.returncode,
             "基线run_id": b4.get("run_id", ""), "基线verdict": b4.get("verdict"),
             "账本复核行": lines(p4c, "账本复核"),
             "出现判定": has_verdict_line(t4), "拒绝比较": "拒绝比较" in t4}
print("\nW4 正例（纪律 62：闸必须有走得通的路）")
print("   写基线 rc=%s run_id=%r verdict=%r" % (p4.returncode, b4.get("run_id"), b4.get("verdict")))
print("   %s" % (RES["W4"]["账本复核行"] or ["（没有账本复核行）"]))
print("   判定 %s" % ("✅ 账本复核通过并出「判定」行"
                      if RES["W4"]["出现判定"] and not RES["W4"]["拒绝比较"] else "❌"))

# ── W2 同一份读数、只因账本查得到就换结论 ───────────────────────────────
d2 = Path("/tmp/v143_w2b")
if d2.exists():
    shutil.rmtree(d2)
shutil.copytree(d4, d2)
p2bl = d2 / "bench" / "bench_baseline.json"
_b = json.loads(p2bl.read_text(encoding="utf-8"))
_b.pop("run_id", None)                            # V142 形态：有 verdict、无身份证
p2bl.write_text(json.dumps(_b, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
_, p2 = run("w2b", "--compare", bench_dir=d2)
t2 = p2.stdout + p2.stderr
# 同一份文件补回身份证（账本里是 GREEN）⇒ 应当放行
_b2 = dict(_b, run_id=b4.get("run_id", ""))
p2bl.write_text(json.dumps(_b2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
_, p2b = run("w2b", "--compare", bench_dir=d2)
t2b = p2b.stdout + p2b.stderr
RES["W2"] = {"无身份证_拒绝": "拒绝比较" in t2, "无身份证_出现判定": has_verdict_line(t2),
             "无身份证_理由": lines(p2, "身份证"),
             "有身份证_出现判定": has_verdict_line(t2b), "有身份证_拒绝": "拒绝比较" in t2b}
print("\nW2 同一份读数，只因账本查得到就换结论")
print("   无身份证 ⇒ 拒绝=%s 出判定=%s ｜ %s" % (RES["W2"]["无身份证_拒绝"],
                                                RES["W2"]["无身份证_出现判定"],
                                                RES["W2"]["无身份证_理由"][:1]))
print("   补身份证 ⇒ 出判定=%s 拒绝=%s" % (RES["W2"]["有身份证_出现判定"],
                                          RES["W2"]["有身份证_拒绝"]))
print("   判定 %s" % ("✅ 结论随账本变，不随文件自述变"
                      if RES["W2"]["无身份证_拒绝"] and not RES["W2"]["无身份证_出现判定"]
                      and RES["W2"]["有身份证_出现判定"] else "❌"))

# ── W3 账本推翻自述：真跑一次 RED，伪造 GREEN 指向它 ───────────────────
d3, p3a = run("w3", "--save-baseline", solver="null")     # 天然 RED
led3 = [json.loads(x) for x in (d3 / "bench" / "runs.jsonl").read_text(
    encoding="utf-8").splitlines() if x.strip()]
red_id = next((r["run_id"] for r in led3 if r.get("verdict") == "RED"), "")
p3b = run("w3", "--save-baseline", bench_dir=d3)          # 同目录再跑 GREEN 并存基线
bl3 = json.loads((d3 / "bench" / "bench_baseline.json").read_text(encoding="utf-8"))
d3c = Path("/tmp/v143_w3f")
if d3c.exists():
    shutil.rmtree(d3c)
shutil.copytree(d3, d3c)
(bl3_forged := (d3c / "bench" / "bench_baseline.json")).write_text(
    json.dumps(dict(bl3, run_id=red_id), ensure_ascii=False, sort_keys=True) + "\n",
    encoding="utf-8")
_, p3 = run("w3f", "--compare", bench_dir=d3c)
t3 = p3.stdout + p3.stderr
RES["W3"] = {"账本行": [(r.get("run_id"), r.get("verdict")) for r in led3],
             "RED_null_rc": p3a.returncode, "RED不写基线": "不写基线" in p3a.stdout,
             "伪造后拒绝": "拒绝比较" in t3, "伪造后出现判定": has_verdict_line(t3),
             "理由": lines(p3, "打架")}
print("\nW3 账本推翻自述（真跑一次 RED，再伪造一份自称 GREEN 的基线指向它）")
print("   账本行 %s ｜ null 跑 rc=%s 不写基线=%s"
      % (RES["W3"]["账本行"], p3a.returncode, RES["W3"]["RED不写基线"]))
print("   伪造后 拒绝=%s 出判定=%s ｜ %s" % (RES["W3"]["伪造后拒绝"],
                                            RES["W3"]["伪造后出现判定"],
                                            RES["W3"]["理由"][:1]))
print("   判定 %s" % ("✅ 两处打架时保守的那个赢"
                      if RES["W3"]["伪造后拒绝"] and not RES["W3"]["伪造后出现判定"] else "❌"))

# ── W5 账本查无此账 ─────────────────────────────────────────────────────
d5 = Path("/tmp/v143_w5b")
if d5.exists():
    shutil.rmtree(d5)
shutil.copytree(d3, d5)
(d5 / "bench" / "bench_baseline.json").write_text(
    json.dumps(dict(bl3, run_id="不存在@0"), ensure_ascii=False, sort_keys=True) + "\n",
    encoding="utf-8")
_, p5 = run("w5b", "--compare", bench_dir=d5)
t5 = p5.stdout + p5.stderr
RES["W5"] = {"拒绝": "拒绝比较" in t5, "出现判定": has_verdict_line(t5),
             "理由": lines(p5, "自说自话")}
print("\nW5 账本查无此账")
print("   拒绝=%s 出判定=%s ｜ %s" % (RES["W5"]["拒绝"], RES["W5"]["出现判定"],
                                    RES["W5"]["理由"][:1]))
print("   判定 %s" % ("✅ 身份证对不上没人认账"
                      if RES["W5"]["拒绝"] and not RES["W5"]["出现判定"] else "❌"))

(OUT / "readings.json").write_text(json.dumps(RES, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
print("\n读数已落盘", OUT / "readings.json")

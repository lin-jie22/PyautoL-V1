#!/usr/bin/env python3
"""V255 预检：冻结 D1 分母 + 尺子三档校准 + 提示逐字落盘（0 次模型调用）。

D1 规则见 PREREG_v255.md §1，**四条全部由代码判**，不靠人挑：
  1) 在 V253 的 13 个低分格里
  2) verify_exec=1 时派不出 `example` 判据
  3) V253 那份答案能编译
  4) 交付是单模块（题面不要求 FILES）
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))

from mw.bench import ExternalScorer, Task            # noqa: E402
from mw.reflect import bare_code                     # noqa: E402
from mw.verify import derive, extract_code           # noqa: E402

EV = ROOT / "bench/evidence/l19_s325"
SUITE = ROOT / "bench/evidence/l17_s323/suite_all39.jsonl"
ANSWERS = ROOT / "bench/evidence/l17_s323/answers_a.jsonl"
ANALYSE = ROOT / "bench/evidence/l17_s323/analyse_v253.json"
SCORER = ROOT / "bench/scorers/code_exec.py"

SYS = "你是测试工程师。只输出一个 python 代码块，不要任何解释。"

USER_TMPL = """题面：
{spec}

请只根据上面的题面，写一份自测脚本，用来检验一份实现是否满足题面要求。

格式要求：
- 输出一个 Python 模块，定义 `def check():`；函数体里用 assert 检查题面明确规定的行为与边界。
- 被测实现已保存在同目录的 `sol.py` 中，直接 `from sol import <题面要求的名字>`。
- 不读文件、不联网、不用 time / random。
- 测试脚本总行数不超过 60 行；挑最有代表性的用例，不要穷举。
- 只输出代码，不要解释。"""


def main() -> int:
    suite = [json.loads(l) for l in SUITE.read_text(encoding="utf-8").splitlines() if l.strip()]
    tasks = {r["id"]: r for r in suite}
    low = list(json.loads(ANALYSE.read_text(encoding="utf-8"))["low"])

    ans: dict[str, str] = {}
    for line in ANSWERS.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("{"):
            continue
        r = json.loads(line)
        if r.get("task_id"):
            ans[r["task_id"]] = r.get("answer") or ""

    # ⚠️ `cmd` 是 `Sequence[str]`：传 str 会被拆成单字符 ⇒ `PermissionError: '/'`
    #    （V253 §4 自踩 3 同一个坑，这次是第二次）
    sc = ExternalScorer([sys.executable, str(SCORER)], source=SCORER)

    rows, d1, excluded = [], [], []
    for tid in low:
        t = tasks[tid]
        code = bare_code(extract_code(ans.get(tid, "")))
        kinds = [c.kind for c in derive(t["prompt"], static=0, exec_=1)]
        blind = "example" not in kinds
        try:
            compile(code, "<ans>", "exec")
            compiles, cerr = True, ""
        except SyntaxError as e:
            compiles, cerr = False, f"{type(e).__name__}: {e.msg}"
        wants_files = "FILES" in t["prompt"]

        why = []
        if not blind:
            why.append("闸能判对错")
        if not compiles:
            why.append("答案不编译")
        if wants_files:
            why.append("多文件交付")
        ok = not why
        if ok:
            d1.append(tid)
        else:
            excluded.append({"cell": tid, "why": why})

        got = {"oracle": sc.score(t, t["oracle"]).score,
               "partial": sc.score(t, t["partial"]).score,
               "null": sc.score(t, t["null"]).score}
        cal_ok = got["oracle"] == 1.0 and got["null"] == 0.0 and got["partial"] < 1.0
        rows.append({"cell": tid, "in_d1": ok, "excluded_because": why,
                     "blind": blind, "compiles": compiles, "compile_err": cerr,
                     "wants_files": wants_files, "checks": kinds,
                     "calib": got, "calib_ok": cal_ok,
                     "ans_chars": len(code)})

    print("=" * 96)
    print("A. D1 冻结（四条规则逐格判，不靠人挑）")
    print("=" * 96)
    print(f"{'cell':24}{'D1':>4}{'闸盲':>6}{'编译':>6}{'单模块':>7}{'chars':>7}  排除理由")
    for r in rows:
        print(f"{r['cell']:24}{'Y' if r['in_d1'] else '.':>4}"
              f"{'Y' if r['blind'] else '.':>6}{'Y' if r['compiles'] else '.':>6}"
              f"{'.' if r['wants_files'] else 'Y':>7}{r['ans_chars']:>7}  "
              f"{','.join(r['excluded_because'])}")
    print(f"\nD1 = {len(d1)} 格: {d1}")
    print(f"排除 {len(excluded)}: " + "; ".join(f"{e['cell']}({','.join(e['why'])})" for e in excluded))

    bad_cal = [r["cell"] for r in rows if not r["calib_ok"]]
    print()
    print("=" * 96)
    print("B. 尺子三档校准（oracle=1.0 / null=0.0 / partial<1.0）")
    print("=" * 96)
    for r in rows:
        g = r["calib"]
        print(f"{r['cell']:24} oracle={g['oracle']:.4f} partial={g['partial']:.4f} "
              f"null={g['null']:.4f}  {'OK' if r['calib_ok'] else '❌'}")
    print(f"\n校准失败 {len(bad_cal)}: {bad_cal}")

    # ── 提示逐字落盘（判据要抗事后改措辞） ────────────────────────────────
    prompts = {tid: {"system": SYS, "user": USER_TMPL.format(spec=tasks[tid]["prompt"])}
               for tid in d1}
    psha = {tid: hashlib.sha256((SYS + "\x00" + p["user"]).encode()).hexdigest()[:16]
            for tid, p in prompts.items()}
    print()
    print("=" * 96)
    print("C. 提示（逐字固定，只含题面）")
    print("=" * 96)
    for tid in d1:
        print(f"{tid:24} prompt_sha={psha[tid]}  user_chars={len(prompts[tid]['user'])}")

    out = {"d1": d1, "excluded": excluded, "rows": rows,
           "prompts": prompts, "prompt_sha": psha,
           "prompt_system": SYS, "prompt_user_tmpl": USER_TMPL,
           "calib_fail": bad_cal, "scorer_sha": sc.sha()}
    (EV / "preflight_v255.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n⇒ 写 {EV / 'preflight_v255.json'}")
    ok = len(d1) >= 5 and not bad_cal
    print(f"PREFLIGHT {'OK' if ok else 'FAIL'}（D1={len(d1)}，校准失败={len(bad_cal)}）")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())

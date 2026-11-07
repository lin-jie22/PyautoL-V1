#!/usr/bin/env python3
"""V78 读数 —— 打回边的**可触发面**普查（三个套件各 1 跑）。

口径逐条对应 `PREREG.md`，⛔ 不在这里另发明字段：

  S1 `kernel_rework.carried`         打回真的发生过几次
  S2 `kernel_verify.runs / failed`   闸拒过几次（打回的**上游**）
  S3 四元恒等式                      `carried==0 ⇒ redelivered==same==diff==passed_after==0`
  S4 `suite_sha` + 逐题分数          确认尺子没被换
  S5 日志里 `走**交付协议**` 的行数  必须等于 `via_answer`

⛔ 本轮无三值判决（没有两臂）。结论形式只有「在 X 套题的 Y 题上，打回发生过 Z 次」。
⛔ 单跑 ⇒ 「0 次」只能读成「这一跑没发生」，不能读成「永远不发生」。
"""
import json
from pathlib import Path

EV = Path("bench/evidence/v6_s167")
SUITES = [
    ("struct", "8e3d642d5b25", "struct_convert_heldout"),
    ("g7j_n4", "ade419351103", "g7j_n4_heldout"),
    ("numlogic", "8a052e6cd79b", "numeric_logic_heldout"),
]
KEYS = ("carried", "redelivered", "same", "diff", "passed_after",
        "via_answer", "dup_skipped", "rework_gap")


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def main() -> int:
    bad = 0
    print("=" * 74)
    print("V78 打回边可触发面普查（REJECT_CARRY=5，每套 1 跑，--repeat 1）")
    print("=" * 74)
    for tag, want_sha, name in SUITES:
        runs = load(EV / f"runs_v78_{tag}.jsonl")
        log_path = EV / f"core_v78_{tag}.log"
        log_txt = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        protocol_hits = log_txt.count("走**交付协议**")
        for rec in runs:
            cal = rec.get("caliber") or {}
            got_sha = str(cal.get("suite_sha") or "")
            rw = rec.get("kernel_rework") or {}
            vf = rec.get("kernel_verify") or {}
            rep = rec.get("report") or {}
            per = rep.get("per_task") or []
            scores = {x["id"]: x.get("score") for x in per}
            mean = sum(scores.values()) / len(scores) if scores else 0.0
            ok_sha = got_sha[:12] == want_sha
            bad += 0 if ok_sha else 1
            print(f"\n── {name}  suite_sha={got_sha[:12]} "
                  f"{'✅' if ok_sha else '⛔ 与预注册不符'}")
            print(f"   mean={mean:.4f}  n={len(per)}  elapsed={rep.get('elapsed_s')}s")
            print(f"   S1 carried={rw.get('carried')}   "
                  f"S2 verify.runs={vf.get('runs')} failed={vf.get('failed')}")
            print("   S3 " + "  ".join(f"{k}={rw.get(k)}" for k in KEYS))
            print(f"   S5 日志『走交付协议』={protocol_hits}  via_answer={rw.get('via_answer')}"
                  f"  {'✅' if protocol_hits == (rw.get('via_answer') or 0) else '⛔ 不等'}")
            print("   S4 " + "  ".join(f"{k}={v}" for k, v in scores.items()))
            if (rw.get("carried") or 0) == 0:
                zero = {k: rw.get(k) for k in ("redelivered", "same", "diff", "passed_after")}
                same_zero = all((v or 0) == 0 for v in zero.values())
                print(f"      恒等式（carried=0 ⇒ 四元全 0）：{'✅' if same_zero else '⛔ ' + str(zero)}")
                if (vf.get("failed") or 0) > 0:
                    print("      ⚠️ 闸拒过但没重交 ⇒ 断点在『拒→重写』之间，不是『没拒』")
    print("\n" + "=" * 74)
    print(f"sha 不符的套件数 = {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

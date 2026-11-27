"""V253 分析：39 题同口径分布 + 13 空洞格三次重跑（判 P1/P2/P3/P4）。

输入（全部只读）：
  /tmp/v253_a/bench/{runs,answers}.jsonl          Stage A（39 题 ×1）
  /tmp/v253_a2_1/bench/{runs,answers}.jsonl       Stage A2 run1（13 题）
  /tmp/v253_a2_2/bench/{runs,answers}.jsonl       Stage A2 run2（13 题）

输出：stdout 表格 + `analyse_v253.json`
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
EV = ROOT / "bench/evidence/l17_s323"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
ARMS = [
    ("A", Path("/tmp/v253_a/bench")),
    ("A2r1", Path("/tmp/v253_a2_1/bench")),
    ("A2r2", Path("/tmp/v253_a2_2/bench")),
]


def read_run(bench: Path):
    """取该目录 runs.jsonl 里最后一条带 per_task 的报告。"""
    last = None
    p = bench / "runs.jsonl"
    if not p.exists():
        return None
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (d.get("report") or {}).get("per_task"):
            last = d
    return last


def read_answers(bench: Path):
    """{task_id: (sha, answer, chars, truncated)}"""
    out = {}
    p = bench / "answers.jsonl"
    if not p.exists():
        return out
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        a = json.loads(line)
        tid = a.get("task_id")
        if tid and a.get("kind", "answer") == "answer":
            out[tid] = (str(a.get("sha256") or "")[:12], a.get("answer") or "",
                        a.get("chars"), a.get("truncated"))
    return out


def rescore(task: dict, answer: str) -> dict:
    payload = json.dumps({"task": task, "answer": answer}, ensure_ascii=False)
    proc = subprocess.run([sys.executable, str(SCORER)], input=payload,
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        return {"score": None, "why": f"rc={proc.returncode} {proc.stderr[:120]}"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"score": None, "why": "bad json"}


def main():
    tasks = {}
    for line in open("/tmp/v253_suite/all39.jsonl", encoding="utf-8"):
        line = line.strip()
        if line:
            t = json.loads(line)
            tasks[t["id"]] = t

    per, prov, recon = {}, {}, {}
    for name, bench in ARMS:
        run = read_run(bench)
        if not run:
            print(f"⚠️ {name}: 没有 runs.jsonl/per_task")
            continue
        rep = run["report"]
        p = rep.get("provenance") or {}
        prov[name] = {"model": p.get("model"), "temperature": p.get("temperature"),
                      "llm_kind": p.get("llm_kind"), "budget": rep.get("budget_tag"),
                      "attempted": rep.get("attempted"), "recorded": rep.get("recorded"),
                      "skipped": rep.get("skipped_no_answer"),
                      "failed": rep.get("failed_scorer")}
        ans = read_answers(bench)
        for t in rep.get("per_task") or []:
            cid = t.get("id")
            sha, body, chars, trunc = ans.get(cid, ("-", "", None, None))
            per.setdefault(cid, {})[name] = {
                "score": t.get("score"), "why": (t.get("why") or "")[:200],
                "sha": sha, "trunc": trunc}

    print("=== 口径（每次跑独立）")
    for k, v in prov.items():
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")

    low = sorted([c for c, d in per.items()
                  if isinstance((d.get("A") or {}).get("score"), (int, float))
                  and d["A"]["score"] < 1.0])
    print(f"\n=== Stage A: {len(per)} 格，<1.0 的 {len(low)} 格")

    print(f"\n{'cell':<24}{'A':>7}{'r1':>7}{'r2':>7}   sha(A/r1/r2)                同 sha")
    same3 = 0
    for c in low:
        d = per[c]
        s = [d.get(k, {}).get("score") for k in ("A", "A2r1", "A2r2")]
        sh = [d.get(k, {}).get("sha", "-") for k in ("A", "A2r1", "A2r2")]
        eq = len(set(sh) - {"-"}) == 1 and "-" not in sh
        same3 += 1 if eq else 0
        f = lambda x: f"{x:.4f}" if isinstance(x, (int, float)) else "  -  "  # noqa: E731
        print(f"{c:<24}{f(s[0]):>7}{f(s[1]):>7}{f(s[2]):>7}   "
              f"{'/'.join(x[:8] for x in sh):<26} {'YES' if eq else 'no'}")

    full = [c for c in per if c not in low]
    print(f"\n=1.0 的格 {len(full)}：{full}")

    # P4：Stage A 产物离线重打分，与记录分数对拍
    print("\n=== P4 离线重打分（Stage A 产物 vs 记录分数）")
    ansA = read_answers(ARMS[0][1])
    mism, ok, noart = [], 0, []
    for cid, d in per.items():
        rec = (d.get("A") or {}).get("score")
        if cid not in ansA:
            noart.append(cid)
            continue
        r = rescore(tasks[cid], ansA[cid][1])
        if r.get("score") is None:
            mism.append((cid, rec, "SCORER_FAIL"))
        elif abs(float(r["score"]) - float(rec)) > 1e-9:
            mism.append((cid, rec, r["score"]))
        else:
            ok += 1
    print(f"  一致 {ok} / 不一致 {len(mism)} / 无产物 {len(noart)}")
    for m in mism:
        print("    ✗", m)
    if noart:
        print("    无产物:", noart)

    print("\n=== 汇总判据")
    p1 = "成立" if len(low) >= 3 else ("不成立" if not low else "不足")
    z02 = [per.get("z02_to_csv", {}).get(k, {}).get("score") for k in ("A", "A2r1", "A2r2")]
    p2 = "成立" if all(isinstance(x, (int, float)) and x == 0.0 for x in z02) else "不成立/未测"
    p3 = f"三次同 sha 的格 {same3}/{len(low)}"
    print(f"  P1 空洞格数={len(low)} ⇒ {p1}")
    print(f"  P2 z02 三次分数={z02} ⇒ {p2}")
    print(f"  P3 {p3}")
    print(f"  P4 重打分一致={ok}/{len(per)}")

    out = {"provenance": prov, "per_cell": per, "low": low, "full": full,
           "p1": p1, "p2": p2, "p3": p3,
           "p4": {"ok": ok, "mismatch": mism, "no_answer": noart}}
    (EV / "analyse_v253.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n落盘 {EV/'analyse_v253.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

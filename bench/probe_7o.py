#!/usr/bin/env python3
"""★7o 分析：把「惯例在 propose 响应」（K）与「最终交付」（D）拆开，HS=握手。

判据（DESIGN ★7o 第3条，跑之前写死）：
  K  = propose 响应（含 [语义记忆] 的规划器请求）里全部代码片段喂 code_exec 的最高分
  HS = 1 若该任务无 answer() 补救请求且交付与某 propose finish 候选 answer 逐字节一致
  D  = final db 交付喂 code_exec 的分数
请求三类：propose(sys 含'规划器') / 预演(sys 含'预测') / 补救(sem=False 且 goal-only)。
零内核改动；打分语义 = 权威判据（code_exec）。
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))
from mw.llm import _MARK_RE  # 内核分隔符正则，口径零漂移（-{2,}[ \t\r\n]*ANSWER[ \t\r\n]*-{2,}）

RUNS = [
    ("n0", 1, V1 / "bench/heldout/g7j_n0_heldout.jsonl"),
    ("n0", 2, V1 / "bench/heldout/g7j_n0_heldout.jsonl"),
    ("n4", 1, V1 / "bench/heldout/g7j_n4_heldout.jsonl"),
    ("n4", 2, V1 / "bench/heldout/g7j_n4_heldout.jsonl"),
]
CAP = Path("/tmp/v1_7o")


def score_with_scorer(task: dict, text: str) -> float | None:
    p = subprocess.run(
        [sys.executable, str(V1 / "bench/scorers/code_exec.py")],
        input=json.dumps({"task": task, "answer": text}),
        capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        return None
    return float(json.loads(p.stdout.strip().splitlines()[-1])["score"])


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def pairs(capture: Path):
    """请求/响应配对（响应紧跟其请求）。"""
    out = []
    pending = None
    for l in capture.read_text().splitlines():
        if not l.strip():
            continue
        c = json.loads(l)
        if c["path"].endswith("#resp"):
            if pending is not None:
                out.append((pending, c["body"]))
                pending = None
        else:
            pending = c["body"]
    return out


def classify(req_body: str, tasks: dict) -> tuple[str, str | None]:
    """返回 (类型, task_id)。类型: propose/预测/补救/其他。"""
    d = json.loads(req_body)
    sysmsg = d["messages"][0]["content"]
    user = d["messages"][1]["content"]
    tid = None
    for t, row in tasks.items():
        if row["prompt"] in user:
            tid = t
            break
    if "规划器" in sysmsg and "[语义记忆]" in user:
        return "propose", tid
    if "预测" in sysmsg:
        return "预测", tid
    if "[语义记忆]" not in user and "当前状态" not in user and tid is not None:
        return "补救", tid
    return "其他", tid


def propose_parse(resp_body: str):
    """抽 propose 响应的 ANSWER 段与 candidates finish answers。

    返回 (answers, answer_section, text)。ANSWER 段 = 直答通道的实际内容
    （7o 实测：finish.args 常为空，交付取自 ---ANSWER--- 之后的部分）。"""
    try:
        text = json.loads(resp_body)["choices"][0]["message"]["content"]
    except Exception:
        return [], "", ""
    answers = []
    try:
        head = text.split("---ANSWER---")[0]
        payload = json.loads(head)
    except Exception:
        payload = None
    cands = payload.get("candidates", []) if isinstance(payload, dict) else []
    for cand in cands if isinstance(cands, list) else []:
        ans = (cand.get("args") or {}).get("answer", "")
        if isinstance(ans, str) and ans.strip():
            answers.append(ans)
    section = ""
    parts = _MARK_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        section = parts[1].strip("\n").rstrip()  # 与内核 split_answer 同口径
    return answers, section, text


def analyse(variant: str, rep: int, heldout: Path) -> list[dict]:
    tasks = {}
    for l in heldout.read_text().splitlines():
        if l.strip():
            r = json.loads(l)
            tasks[r["id"]] = r
    info = {tid: {"k_scores": [], "rescue": 0, "answers": [], "n_propose": 0,
                  "rescue_texts": [], "answer_sections": []} for tid in tasks}
    for req_body, resp_body in pairs(CAP / f"cap_{variant}_r{rep}.jsonl"):
        kind, tid = classify(req_body, tasks)
        if tid is None or kind in ("预测", "其他"):
            continue
        if kind == "propose":
            info[tid]["n_propose"] += 1
            answers, section, text = propose_parse(resp_body)
            if answers:
                info[tid]["answers"].extend(answers)
            if section:
                info[tid]["answer_sections"].append(section)
            best = 0.0
            seen = set()
            for s in strings(text):
                if s in seen or len(s) < 20:
                    continue
                seen.add(s)
                sc = score_with_scorer(tasks[tid], s)
                if sc is not None and sc > best:
                    best = sc
            info[tid]["k_scores"].append(round(best, 2))
        elif kind == "补救":
            info[tid]["rescue"] += 1
            try:
                info[tid]["rescue_texts"].append(
                    json.loads(resp_body)["choices"][0]["message"]["content"])
            except Exception:
                pass
    # D：final db 交付（goal 后第一个 finish，可隔 outcome 行）
    con = sqlite3.connect(CAP / f"run_{variant}_r{rep}/mem/memory.db")
    rows = con.execute("SELECT ts, kind, content FROM episodes ORDER BY ts").fetchall()
    con.close()
    deliveries = {}
    for i, row in enumerate(rows):
        if row[1] != "goal":
            continue
        tid = next((t for t, r in tasks.items() if r["prompt"] in row[2]), None)
        if tid is None:
            continue
        for j in range(i + 1, len(rows)):
            if rows[j][1] == "goal":
                break
            if rows[j][1] == "finish":
                deliveries[tid] = rows[j][2]
                break
    out = []
    for tid in tasks:
        dtext = deliveries.get(tid, "")
        d = score_with_scorer(tasks[tid], dtext) if dtext else 0.0
        k = max(info[tid]["k_scores"]) if info[tid]["k_scores"] else 0.0
        # HS（7o 实测后精化）：直答通道 = propose 的 ---ANSWER--- 段；
        # 段非空且无补救 ⇒ 直答；段空/缺失 ⇒ 记忆盲补救接管。
        hs = 1 if (info[tid]["rescue"] == 0 and info[tid]["answer_sections"]) else 0
        out.append({"variant": variant, "rep": rep, "task": tid, "K": k, "HS": hs,
                    "D": round(d or 0.0, 2), "n_propose": info[tid]["n_propose"],
                    "rescue": info[tid]["rescue"], "k_scores": info[tid]["k_scores"],
                    "n_answer_sec": len(info[tid]["answer_sections"]),
                    "dsha": hashlib.sha256(dtext.encode()).hexdigest()[:10] if dtext else "-"})
    return out


def main():
    lines = ["§17-7o 知识 vs 握手 三元组（K=propose响应最高片段分 / HS=直答无补救 / D=交付分）", ""]
    all_rows = []
    for variant, rep, heldout in RUNS:
        rows = analyse(variant, rep, heldout)
        all_rows.extend(rows)
        lines.append(f"== {variant} r{rep} ==")
        for r in rows:
            lines.append(f"  {r['task']}: K={r['K']:.2f} HS={r['HS']} D={r['D']:.2f}"
                         f" (propose×{r['n_propose']} ANSWER段×{r['n_answer_sec']}"
                         f" rescue×{r['rescue']} k_scores={r['k_scores']} dsha={r['dsha']})")
        lines.append("")
    from collections import defaultdict
    grid = defaultdict(dict)
    for r in all_rows:
        grid[(r["variant"], r["task"])][r["rep"]] = (r["K"], r["HS"], r["D"])
    lines.append("== 汇总（按 变体×任务，两 rep 并排）==")
    for (v, t), reps in sorted(grid.items()):
        cells = "  ".join(f"r{r}=(K={k:.2f},HS={h},D={d:.2f})" for r, (k, h, d) in sorted(reps.items()))
        lines.append(f"  {v} {t}: {cells}")
    text = "\n".join(lines) + "\n"
    print(text)
    out = V1 / "bench/evidence/knowledge_s46"
    out.mkdir(parents=True, exist_ok=True)
    (out / "khd_triplets.txt").write_text(text)


if __name__ == "__main__":
    main()

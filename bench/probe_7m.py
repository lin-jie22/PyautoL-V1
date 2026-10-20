#!/usr/bin/env python3
"""§17-7m-α —— n4 抹除效应的步级取证（**零模型调用**，复盘 7k 冻结 states）。

7l 量清了 n0/n4 的装置侧差异（候选 A=第1步语义块形状、D=第2步起尾200闸），
但两者因果方向未测。本轮不跑模型、不造新格子，把 7k 冻结在 s39 的
H 臂 states（r1–r3 全步）逐步切开取证：

  ① 真实 recall 回显宽度（7l ③ 的 205 字是纯 text join 的**低估**——
     真实 recall 工具输出是 as_line 格式 `[abstract c=0.50 x{hits}] {text}`
     以换行 join，多约 22 字符/条前缀）。零模型复算真值。
  ② 每格 (task, r, it) 的上下文存活表：
     - [语义记忆] 块：条数/总长/本条全文是否逐字节在（候选 A 的逐步轨迹）；
     - [工作记忆] 块：recall 回显条数（`[abstract c=` 前缀指纹）、
       四条惯例的钥匙段存活位；
     - 本条惯例的两检测器存活：KEY=`（ZKn）`、TXT=正文前 20 字（跳钥匙段）。
       （⛔ 钥匙检测只在语义/工作记忆块内做——[目标] 块含题面钥匙，会假阳。）
  ③ 交付步汇总：每格该题最大 it 时本条惯例是否还在上下文里。
  ④ R-noise 逐位复核：同 (task, it) 的 state 在 r1/r2/r3 的 sha256 是否一致
     （7k 判 R-noise=0.0000 ⇒ 本应逐位同；不一致 = 环境漂移告警）。

判读规则见 DESIGN.md ★7m-α 事前登记（写死）：交付步本条消失 ⇒ 抹除点定位
到步级；本条在场但行为 0 ⇒ "看得到但没用"，A 嫌疑升级、D 降级。
"""

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V1))
sys.path.insert(0, str(V1 / "bench"))

from bench.build_g7d_seed import VARIANTS           # noqa: E402
from bench.build_g7j_heldout import KEYS            # noqa: E402
from state_audit import state_blocks                # noqa: E402

FACTS = VARIANTS["7j"]
ORDER = list(FACTS)                                  # ZK1→ZK4 固定排列
S39 = V1 / "bench" / "evidence" / "knowledge_s39"


def section(title: str) -> None:
    print(f"\n== {title} ==")


def key_of(tid: str) -> str:
    return f"（{KEYS[tid]}）"


def txt20(tid: str) -> str:
    """正文前 20 字（跳过 5 字钥匙段 `（ZKn）`）。"""
    return FACTS[tid][5:25]


def sem_items(sem: str) -> list[str]:
    """语义块按 ` ｜ ` 切条（块头后正文）。"""
    body = sem[len("[语义记忆] "):] if sem.startswith("[语义记忆] ") else sem
    return [seg for seg in body.split(" ｜ ") if seg.strip()]


def wm_recall_lines(wm: str) -> list[str]:
    """工作记忆块里含 as_line 前缀指纹的片段计数。

    回显是**单条**工作记忆条目（一次 recall 调用，内含换行），
    渲染层已按 `w[-200:]` 截过 —— 这里按换行切开数指纹行。
    """
    return [ln for ln in wm.splitlines() if ln.lstrip().startswith("[abstract c=")]


def key_alive_map(block: str) -> dict[str, bool]:
    """四条惯例的钥匙段在该块里的存活。"""
    return {tid: (key_of(tid) in block) for tid in ORDER}


def fmt_yn(b: bool) -> str:
    return "✓" if b else "✗"


def load_states(ev: str, arm: str, r: int) -> list[dict]:
    p = S39 / ev / f"states_{arm}_r{r}.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def main() -> int:
    # ── ① 真实 recall 回显宽度（7l ③ 的修正）────────────────────
    section("① 真实 recall 工具输出宽度（as_line 格式，零模型复算）")
    from mw.memory import MemoryStore
    tmp = Path(tempfile.mkdtemp())
    m = MemoryStore(tmp / "m.db")
    for t in FACTS.values():
        m.commit_fact(t, source="abstract", confidence=0.5)
    heldout = {}
    for name in ("g7j_n0", "g7j_n4"):
        heldout[name] = {
            json.loads(l)["id"]: json.loads(l)["prompt"]
            for l in (V1 / "bench" / "heldout" / f"{name}_heldout.jsonl").read_text().splitlines()
            if l.strip()
        }
    for name in ("g7j_n0", "g7j_n4"):
        for tid in ORDER:
            got = m.recall(heldout[name][tid], k=6)
            real = "\n".join(f.as_line() for f in got)
            tail = real[-200:]
            dead = [f"ZK{i+1}" for i, t2 in enumerate(ORDER)
                    if FACTS[t2] not in tail]
            print(f"  {name} {tid:22s} hit={len(got)}条 真实输出={len(real):3d}字 "
                  f"尾200后丢={dead or '无'}")
    m.close()
    print("  ⚠️ 7l ③ 的 205 字是纯 text join；真实输出含 as_line 前缀"
          "（[abstract c=0.50 x{n}] ≈ 22 字/条）——上面的数字是本轮真值。")

    # ── ② 逐步轨迹表（H 臂，r1–r3 全步）──────────────────────────
    for ev in ("ev_n0", "ev_n4"):
        section(f"② {ev} H 臂逐步轨迹（本条 = 该题对应惯例）")
        for r in (1, 2, 3):
            recs = load_states(ev, "H", r)
            for d in recs:
                tid = d["task"]
                blocks = state_blocks(d["state"] or "")
                sem = blocks.get("[语义记忆]", "")
                wm = blocks.get("[工作记忆]", "")
                sems = sem_items(sem)
                recalls = wm_recall_lines(wm)
                kmap = key_alive_map(sem + "\n" + wm)
                own_key = kmap[tid]
                own_txt = (txt20(tid) in sem) or (txt20(tid) in wm)
                own_full = FACTS[tid] in sem
                km = " ".join(f"ZK{i+1}:{fmt_yn(kmap[t2])}"
                              for i, t2 in enumerate(ORDER))
                print(f"  r{r} {tid:22s} it{d['it']} "
                      f"sem={len(sems)}条/{len(sem)-6:3d}字 "
                      f"本条全文:{fmt_yn(own_full)} "
                      f"wm回显={len(recalls)}条 {km} "
                      f"本条TXT:{fmt_yn(own_txt)}"
                      f"{' ⚠️本条KEY丢' if not own_key else ''}")

    # ── ③ 交付步汇总 ────────────────────────────────────────────
    section("③ 交付步（每格最大 it）本条存活汇总")
    for ev in ("ev_n0", "ev_n4"):
        for r in (1, 2, 3):
            recs = load_states(ev, "H", r)
            last: dict[str, dict] = {}
            for d in recs:
                if d["task"] not in last or d["it"] > last[d["task"]]["it"]:
                    last[d["task"]] = d
            for tid in ORDER:
                d = last[tid]
                blocks = state_blocks(d["state"] or "")
                sem = blocks.get("[语义记忆]", "")
                wm = blocks.get("[工作记忆]", "")
                sems = sem_items(sem)
                in_sem = FACTS[tid] in sem
                key_wm = key_of(tid) in wm
                txt_wm = txt20(tid) in wm
                verdict = ("在场" if (in_sem or key_wm or txt_wm) else "❌消失")
                print(f"  {ev} r{r} {tid:22s} 交付步 it{d['it']} "
                      f"sem={len(sems)}条 本条全文@sem:{fmt_yn(in_sem)} "
                      f"KEY@wm:{fmt_yn(key_wm)} TXT@wm:{fmt_yn(txt_wm)} ⇒ {verdict}")

    # ── ④ R-noise 逐位复核（r1/r2/r3 同格 state sha256）──────────
    section("④ R-noise 逐位复核（同 (task,it) 的 state sha256，r1–r3）")
    drift = 0
    total = 0
    for ev in ("ev_n0", "ev_n4"):
        runs = {}
        for r in (1, 2, 3):
            for d in load_states(ev, "H", r):
                runs.setdefault((d["task"], d["it"]), {})[r] = d["state"]
        for (tid, it), m3 in sorted(runs.items()):
            if len(m3) < 3:
                continue
            total += 1
            shas = {r: hashlib.sha256((m3[r] or "").encode()).hexdigest()[:12]
                    for r in (1, 2, 3)}
            if len(set(shas.values())) != 1:
                drift += 1
                print(f"  ⚠️ {ev} {tid} it{it} 漂移: {shas}")
    print(f"  {total} 格同位对比，漂移 {drift} 格"
          + ("  ✅（与 7k 判读 R-noise=0.0000 一致）" if drift == 0 else "  ❌环境漂移告警"))

    return 0


if __name__ == "__main__":
    sys.exit(main())

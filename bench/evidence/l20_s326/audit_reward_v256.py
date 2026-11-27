#!/usr/bin/env python3
"""V256 —— 奖励回灌通道的剂量审计（PREREG_v256.md）

0 次模型生成、0 行生产改动。**只用生产实现本体**，不写平行复刻。

三个相位：
  0 = 空账本（对照，证明 V253 的 13 格是空通道读数）
  1 = 模拟第二轮（39 条 reward 全写入后，看每题实际收到哪几行）
  2 = 上界（只写 13 条最有利历史，覆盖 _ERROR_CLASS_CHECK 全 7 键 + 6 个缺键）

用法：python3 audit_reward_v256.py [--out readings_v256.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.features import feature_set                     # noqa: E402
from mw.ledger import is_passed                          # noqa: E402
from mw.llm import StubLLM                               # noqa: E402
from mw.memory import MemoryStore                        # noqa: E402
from mw.worldmodel import (                              # noqa: E402
    REWARD_SOURCE, WorldModel, _ERROR_CLASS_CHECK, _error_class,
)

SUITE = ROOT / "bench/evidence/l17_s323/suite_all39.jsonl"
RUNS = Path("/tmp/v253_a/bench/runs.jsonl")

#: 阶段 2 的 13 类。前 7 个是 `_ERROR_CLASS_CHECK` 的键（`其他` 用抽不到类别的文本触发），
#: 后 6 个是它**没有键**、实测会落进 `其他` 的类别（V254 §7 已量到 SyntaxError 这一条）。
_UPPER_FAILURES = [
    ("NameError", "NameError: name 're' is not defined"),
    ("AttributeError", "AttributeError: 'VFS' object has no attribute 'root'"),
    ("TypeError", "TypeError: 'tuple' object does not support item assignment"),
    ("IndexError", "IndexError: list index out of range"),
    ("KeyError", "KeyError: 'a'"),
    ("AssertionError", "AssertionError: assert f(2) == 4"),
    ("其他", "手算与实现不一致（无可识别的异常类名）"),
    ("SyntaxError", "SyntaxError: unexpected character after line continuation character"),
    ("ModuleNotFoundError", "ModuleNotFoundError: No module named 'lru'"),
    ("ValueError", "ValueError: invalid literal for int() with base 10"),
    ("ZeroDivisionError", "ZeroDivisionError: division by zero"),
    ("RecursionError", "RecursionError: maximum recursion depth exceeded"),
    ("UnboundLocalError", "UnboundLocalError: local variable 'n' referenced before assignment"),
]

_NUM = re.compile(r"出现过 \d+ 次|均值 \d+\.\d+")


def load_tasks() -> list[dict]:
    return [json.loads(l) for l in SUITE.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_scores() -> dict[str, dict]:
    """每格取**最高分**那一行（与 V253 analyse 同规则），带回该行的 why。"""
    best: dict[str, dict] = {}
    for line in RUNS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        for t in (d.get("report") or {}).get("per_task") or []:
            cid, sc = t.get("id"), t.get("score")
            if not cid or not isinstance(sc, (int, float)):
                continue
            cur = best.get(cid)
            if cur is None or float(sc) > cur["score"]:
                best[cid] = {"score": float(sc), "why": str(t.get("why") or "")}
    return best


def fresh_wm(db: Path, echo: int = 1) -> WorldModel:
    return WorldModel(MemoryStore(db), StubLLM(), reward_echo=echo)


def probe(wm: WorldModel, tasks: list[dict]) -> list[dict]:
    """对 39 题各调一次 reward_guidance，返回原文与计数快照。"""
    out = []
    for t in tasks:
        f = feature_set(t)
        wm.rewards_seen = 0
        wm.rewards_skipped_self = 0
        wm.rewards_skipped_success = 0
        txt = wm.reward_guidance(features=f, task_id=str(t["id"]))
        out.append({
            "task_id": str(t["id"]),
            "rkey": ",".join(sorted(f)),
            "text": txt,
            "seen": int(wm.rewards_seen),
            "skipped_self": int(wm.rewards_skipped_self),
            "skipped_success": int(wm.rewards_skipped_success),
        })
    return out


def same_count(low: list[str], high: set[str]) -> int:
    return sum(1 for x in low if x in high)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).with_name("readings_v256.json")))
    a = ap.parse_args()

    tasks = load_tasks()
    scores = load_scores()
    assert len(tasks) == 39, f"题数不是 39：{len(tasks)}"
    missing = [t["id"] for t in tasks if t["id"] not in scores]
    assert not missing, f"缺分数：{missing}"

    low_ids = sorted([c for c, v in scores.items() if v["score"] < 1.0])
    high_ids = sorted([c for c, v in scores.items() if v["score"] >= 1.0])
    print(f"[分母] 题={len(tasks)}  low(<1.0)={len(low_ids)}  high(==1.0)={len(high_ids)}")
    print(f"[闸值] is_passed(1.0)={is_passed(1.0)}  is_passed(0.9999)={is_passed(0.9999)}")
    print(f"[low ] {low_ids}")
    print(f"[分组] low={len(low_ids)} high={len(high_ids)} 合计={len(low_ids)+len(high_ids)}")

    # 真实错误类别分布
    cls_of = {}
    for c in low_ids:
        cls_of[c] = _error_class(scores[c]["why"])
    from collections import Counter
    dist = Counter(cls_of.values())
    print(f"[类别] 13 格真实类别分布 = {dict(dist)}")
    for c in low_ids:
        print(f"        {c:26s} score={scores[c]['score']:<7} class={cls_of[c]}")

    tmp = Path(tempfile.mkdtemp(prefix="v256_"))

    # ---------------- 阶段 0：空账本 ----------------
    wm0 = fresh_wm(tmp / "p0.db")
    r0 = probe(wm0, tasks)
    empty0 = sum(1 for x in r0 if x["text"] == "")
    print(f"\n[阶段0] 空账本：返回空串 {empty0}/39  非空={[x['task_id'] for x in r0 if x['text']]}")
    print(f"[阶段0] DB 里 kind=reward 的 episode 数 = "
          f"{len(wm0.memory.episodes(kind='reward', limit=500))}")

    # ---------------- 阶段 1：模拟第二轮 ----------------
    wm1 = fresh_wm(tmp / "p1.db")
    written = rejected_src = rejected_missing = 0
    for t in tasks:
        cid = str(t["id"])
        sc = scores[cid]["score"]
        passed = is_passed(sc)
        fail = "" if passed else scores[cid]["why"][:160]
        if wm1.record_reward(task_id=cid, features=feature_set(t), passed=passed,
                             score=sc, failure=fail, source=REWARD_SOURCE):
            written += 1
        else:
            rejected_src += int(wm1.rewards_rejected_source > 0)
            rejected_missing += int(wm1.rewards_rejected_missing > 0)
    n_ep = len(wm1.memory.episodes(kind="reward", limit=500))
    print(f"\n[阶段1] 写入={written} rejected_source={wm1.rewards_rejected_source} "
          f"rejected_missing={wm1.rewards_rejected_missing} DB_reward_episodes={n_ep}")

    wm1b = fresh_wm(tmp / "p1.db")   # 新实例 ⇒ 计数从 0 起
    r1 = probe(wm1b, tasks)
    texts1 = {x["task_id"]: x["text"] for x in r1}
    uniq1 = sorted(set(texts1.values()))
    print(f"[阶段1] 去重文本种类 N = {len(uniq1)}")
    for i, u in enumerate(uniq1):
        print(f"  --- N#{i} ---")
        for ln in (u or "<空串>").splitlines():
            print(f"    {ln}")

    low_txt = [texts1[c] for c in low_ids]
    high_set = set(texts1[c] for c in high_ids)
    M = same_count(low_txt, high_set)
    print(f"\n[P4] 13 个低分格中，文本与某满分格**逐字节相同**的格数 M = {M}/13")
    for c in low_ids:
        hit = "同" if texts1[c] in high_set else "异"
        print(f"     {c:26s} 与满分格文本：{hit}  (len={len(texts1[c])})")

    # 去数值化复核：抹掉「出现过 N 次」「均值 x.xx」，只留句子骨架
    def skeleton(s: str) -> str:
        return _NUM.sub("<NUM>", s)
    low_sk = [skeleton(texts1[c]) for c in low_ids]
    high_sk = set(skeleton(texts1[c]) for c in high_ids)
    M_sk = same_count(low_sk, high_sk)
    print(f"[P4-去数值化] 抹掉计数/均值后：M_skel = {M_sk}/13；"
          f"骨架去重种类 = {len(set(low_sk) | high_sk)}")

    # ---------------- 阶段 2：上界（13 类全覆盖） ----------------
    wm2 = fresh_wm(tmp / "p2.db")
    for i, (_cls_name, fail) in enumerate(_UPPER_FAILURES):
        wm2.record_reward(task_id=f"__hist_{i:02d}", features=feature_set(tasks[i]),
                          passed=False, score=0.0, failure=fail, source=REWARD_SOURCE)
    print(f"\n[阶段2] 写入 {len(_UPPER_FAILURES)} 条最有利历史")
    print("[阶段2] 类别 → 归一后类别 映射表（触发 非单射 的地方要能看见）：")
    for cls_name, fail in _UPPER_FAILURES:
        got = _error_class(fail)
        print(f"     写成 {cls_name:22s} ⇒ 归一为 {got:18s} "
              f"句子={_ERROR_CLASS_CHECK.get(got, '?')[:28]}")

    wm2b = fresh_wm(tmp / "p2.db")
    r2 = probe(wm2b, tasks)
    texts2 = {x["task_id"]: x["text"] for x in r2}
    lines2 = sorted({ln for v in texts2.values() for ln in (v or "").splitlines()
                     if ln.startswith("- ")})
    print(f"[阶段2] 句子集合（按行拆，只数 `- ` 开头）S = {len(lines2)}")
    for ln in lines2:
        print(f"    {ln}")
    uniq2 = sorted(set(texts2.values()))
    print(f"[阶段2] 整段去重种类 = {len(uniq2)}")
    M2 = same_count([texts2[c] for c in low_ids], set(texts2[c] for c in high_ids))
    print(f"[阶段2] M（低分格文本也在满分集合里）= {M2}/13")

    # ---------------- 落盘 ----------------
    readings = {
        "prereg": "bench/evidence/l20_s326/PREREG_v256.md",
        "model_calls": 0,
        "suite": str(SUITE),
        "runs_source": str(RUNS),
        "n_tasks": len(tasks),
        "low_ids": low_ids,
        "high_ids": high_ids,
        "class_of_low": cls_of,
        "class_dist": dict(dist),
        "is_passed_1_0": bool(is_passed(1.0)),
        "is_passed_09999": bool(is_passed(0.9999)),
        "phase0": {"empty": empty0, "n": len(r0), "rows": r0,
                   "db_reward_episodes": len(wm0.memory.episodes(kind="reward", limit=500))},
        "phase1": {"written": written,
                   "rejected_source": wm1.rewards_rejected_source,
                   "rejected_missing": wm1.rewards_rejected_missing,
                   "db_reward_episodes": n_ep,
                   "n_unique_texts": len(uniq1),
                   "unique_texts": uniq1,
                   "M": M, "M_skeleton": M_sk,
                   "skeleton_unique": len(set(low_sk) | high_sk),
                   "rows": r1},
        "phase2": {"hist_written": len(_UPPER_FAILURES),
                   "mapping": [{"written_as": c, "normalized": _error_class(f),
                                "sentence": _ERROR_CLASS_CHECK.get(_error_class(f), "")}
                               for c, f in _UPPER_FAILURES],
                   "S_lines": len(lines2), "lines": lines2,
                   "unique_texts": uniq2, "M": M2},
        "verdict": {
            "P1_written39": written == 39 and wm1.rewards_rejected_source == 0
                            and wm1.rewards_rejected_missing == 0,
            "P2_empty_39of39": empty0 == 39,
            "P3_N_le_8": len(uniq1) <= 8,
            "P4_M_eq_13": M == 13,
            "P4_Mskel_eq_13": M_sk == 13,
            "P5_S_le_6": len(lines2) <= 6,
        },
    }
    Path(a.out).write_text(json.dumps(readings, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[DONE] {a.out}")
    print("[判定] " + json.dumps(readings["verdict"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

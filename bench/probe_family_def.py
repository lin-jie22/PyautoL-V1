#!/usr/bin/env python3
"""「族」到底是个真概念，还是「它在哪个文件里」的同义反复 —— 零成本实测。

## 问题

自我模型的核心断言是「知道族 ⇒ 更好地预测我会不会做对」。
而现在 `family = suite.name`（`mw/bench.py:769`）。题目是我按抽屉分装的，
然后数据告诉我"这些抽屉的通过率不同且可预测" —— **这测的是分装，不是题。**

## 实验

在**已经存在的真实账本**上（不跑一次新模型、零成本），把族换成
`mw/features.py` 从**题面结构**自动聚出来的类，重跑同一把尺子
（`metacog.measure_family_power`），两种定义逐项对照。

## ⛔ 事前声明（写定在读数之前，不许事后改）

1. 特征定义与簇数（`FEATURES_VERSION="f1"`、`K_CLUSTERS=3`）
   **先冻结、跑一次**。结果不论好坏都入档。
   ⇒ 禁止为了把读数变绿而回来调关键词 —— 那是尺子购物，
   与本项目的立论（尺子不可被内核/被我改写）直接冲突。
2. **本轮只测量，不改内核的族定义。** 换族定义 = 换判据量纲，
   与上一轮被否掉的"会话级量纲"同类风险：一旦同时又是设计者又是评判者，
   动机就再也区分不开。本轮出读数，替换与否按下面写死的规则决定。
3. 替换规则（**先于读数写定**）：
   · 结构族 snr ≥ 文件名族 snr ⇒ 替换（更强且更诚实，无争议）。
   · 否则 ⇒ **不替换**，并把"结构族更弱"写成一条负面证据入档
     （含义：现有族预测力有相当一部分来自套件的任务选择）。

用法
    python3 bench/probe_family_def.py [LEDGER_JSONL]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mw import features as F          # noqa: E402
from mw.ledger import ScoreLedger     # noqa: E402
from mw.metacog import measure_family_power  # noqa: E402

DEFAULT_LEDGER = "bench/evidence/score_ledger_real_qwen25_7b_T0_8sess.jsonl"


def load_task_index(*patterns: str) -> dict[str, tuple[str, dict]]:
    """`{task_id: (suite_name, task_dict)}`。

    ⛔ 刻意放在 `bench/` 而不是 `mw/features.py`：读任务集是**外部锚层**的活，
    内核里不该有一个"能把题集读进来"的函数（见 features.py 末尾的说明）。
    """
    import glob as _glob
    index: dict[str, tuple[str, dict]] = {}
    for pattern in patterns:
        for path in sorted(_glob.glob(pattern)):
            suite = path.split("/")[-1].replace(".jsonl", "")
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        d = json.loads(line)
                        index[str(d.get("id"))] = (suite, d)
    return index


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _rates(rows: list[dict]) -> str:
    acc: dict[str, list[int]] = {}
    for r in rows:
        acc.setdefault(str(r.get("family")), []).append(1 if r.get("passed") else 0)
    parts = []
    for fam in sorted(acc):
        v = acc[fam]
        parts.append(f"{fam}={sum(v)/len(v):.3f}(n={len(v)})")
    return "  ".join(parts)


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else DEFAULT_LEDGER)
    raw = _load(path)
    sigs = sorted({str(r.get("caliber_sig")) for r in raw})
    if len(sigs) != 1:
        print(f"❌ 账本里有 {len(sigs)} 种口径签名（{sigs}）⇒ 先按口径分开再比。")
        return 2
    ledger = ScoreLedger(path, caliber_sig=sigs[0])
    rows = ledger.admissible()
    print(f"账本 {path}")
    print(f"  原始 {len(raw)} 行 ｜ 受理 {len(rows)} 行 ｜ {ledger.stats.as_line()}")
    print(f"  会话 {sorted({r['session'] for r in rows})}")
    print(f"  特征定义 {F.FEATURES_VERSION} ｜ 指纹 {F.feature_version_hash()}")

    idx = load_task_index("bench/tasks/*.jsonl", "bench/heldout/*.jsonl",
                            "bench/tasks_lang/*.jsonl")
    missing = sorted({r["task_id"] for r in rows if r["task_id"] not in idx})
    if missing:
        print(f"❌ 账本里有 {len(missing)} 道题在任务集里找不到：{missing[:5]}")
        return 2
    tasks_in_ledger = [idx[t][1] for t in sorted({r["task_id"] for r in rows})]
    struct_fam = F.cluster(tasks_in_ledger)

    # —— 两套族定义 ——
    suite_rows = [dict(r) for r in rows]
    struct_rows = [dict(r, family=struct_fam.get(r["task_id"], "?")) for r in rows]

    print("\n族内通过率")
    print(f"  文件名族（现在内核用的）：{_rates(suite_rows)}")
    print(f"  结构族（题面自动聚类）  ：{_rates(struct_rows)}")

    # 交叉表：结构族是否真的**切断了**文件名的边界（若完全重合 ⇒ 换了个名字而已）
    cross: dict[tuple[str, str], int] = {}
    for a, b in zip(suite_rows, struct_rows):
        cross[(a["family"], b["family"])] = cross.get((a["family"], b["family"]), 0) + 1
    print("\n交叉表（文件名族 × 结构族）")
    for (a, b), n in sorted(cross.items()):
        print(f"  {a:16s} × {b:6s} = {n}")

    print("\n预测力（同一把尺子 measure_family_power，同一批行，只换族标签）")
    pa = measure_family_power(suite_rows)
    pb = measure_family_power(struct_rows)
    hdr = f"  {'':10s} {'状态':8s} {'Δ':>9s} {'阈值':>9s} {'snr':>7s} {'历史':>5s} {'未来':>5s}"
    print(hdr)
    for name, p in (("文件名族", pa), ("结构族", pb)):
        d = "-" if p.delta is None else f"{p.delta:+.4f}"
        s = "-" if p.snr is None else f"{p.snr:.3f}"
        print(f"  {name:10s} {p.state:8s} {d:>9s} {p.margin:>9.4f} {s:>7s} "
              f"{p.n_history:>5d} {p.n_future:>5d}")
    print(f"\n  文件名族理由：{pa.reason}")
    print(f"  结构族理由  ：{pb.reason}")

    sa = pa.snr if pa.snr is not None else -1.0
    sb = pb.snr if pb.snr is not None else -1.0
    print("\n判定（按事前写死的规则）")
    if sb >= sa:
        print("  结构族 snr ≥ 文件名族 ⇒ 替换内核族定义（更强且更诚实）。")
    else:
        print("  结构族 snr < 文件名族 ⇒ **不替换**；此结果作为负面证据入档：")
        print("  现有族预测力有一部分来自「套件里挑了哪些题」，不是题面结构规律。")
    print("  ⛔ 无论哪一边，本文件跑出的数字都不许反过来改 `mw/features.py` 的规则。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

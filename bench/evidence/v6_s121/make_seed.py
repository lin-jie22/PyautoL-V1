"""V31 —— 从一份冻结的种子账本派生五个实验条件（零 LLM、零网络、确定性）。

## 为什么要派生，而不是让模型自己生成评审

V31 的问题不是「评审有没有用」，而是**这台装置能不能分辨一条已知充分的可行动指令**。
要回答它，载荷必须是**受控的常量**：
  · 内容固定（不随模型这一轮的输出漂移）；
  · 长度固定（否则「多出 208 字」这个已量过的扰动会和内容混在一起）；
  · 位置固定（只在**送达窗口内/外**之间切换，其余一字不动）。

种子 = `seed_base/critique.jsonl`，是 V30 `p4` 臂跑完之后落盘的 6 条真评审
（`sha256=9c009fdc…`）。它的 `scorer_sha` / `suite_sha` 必须是本轮的**当前**值，
否则 `CritiqueLedger.admissible()` 会按 `cross_scorer`/`cross_suite` 全部拒收 ——
那会变成一次「载荷没送到」的假零（V29 踩过）。

## 送达窗口（`core.py:1443` 的 `crit[-3:]` × `critique.py:126` 的按 task_id 排序）

| 当前题 | 剔除自评后 | 末三（真正进 prompt 的三条） |
|---|---|---|
| e1wi | e1ni,e2ni,e2wi,e3ni,e3wi | e2wi, e3ni, e3wi |
| e1ni | e1wi,e2ni,e2wi,e3ni,e3wi | e2wi, e3ni, e3wi |
| e2wi | e1ni,e1wi,e2ni,e3ni,e3wi | e2ni, e3ni, e3wi |
| e2ni | e1ni,e1wi,e2wi,e3ni,e3wi | e2wi, e3ni, e3wi |
| e3wi | e1ni,e1wi,e2ni,e2wi,e3ni | e2ni, e2wi, e3ni |
| e3ni | e1ni,e1wi,e2ni,e2wi,e3wi | e2ni, e2wi, e3wi |

⇒ `e1ni` 与 `e1wi` 两条**永不送达任何人**（恒在字典序头部被切掉）。
`e3wi` 与 `e2wi` 出现在**除自己以外全部 5 个窗口**里。
这是 V31 全部装置的支点：**「同一段字，放进账本的不同条目」= 送达 / 不送达**，
而账本大小、条目数、其它五条内容全都不变 ⇒ 单变量。

## 五个条件

| 文件 | 改动 | 送达？ |
|---|---|---|
| `s0_off.jsonl` | 无（配 `--critique-scope=off`） | 不送（开关关） |
| `s1_base.jsonl` | 无 | 送 5 题 |
| `s2_fix_nondeliv.jsonl` | `e1ni.why = P`（该条永不送达） | **不送** |
| `s3_placebo.jsonl` | `e3wi.why = Q`（等长、零信息、送达） | 送 5 题 |
| `s4_fix.jsonl` | `e3wi.why = P`（等长、可行动、送达） | 送 5 题 |

`s2` 的作用：证明「账本里多了一段字」本身不改变任何 prompt（预测 `s2` 的 state
与 `s1` **逐字节相同**）。`s3` 的作用：把「多出 74 个字」与「那 74 个字说了什么」拆开。

用法：`python3 make_seed.py`（幂等；重复跑产出逐字节相同的文件）
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "seed_base" / "critique.jsonl"
OUT = HERE / "seeds"

#: 可行动载荷 P：**逐字**给出本题面真正缺的那两件事（整份输出 + 顶部 import）。
#: ⚠️ 必须是 `why` 的**前缀**：`critique.py:as_line()` 是 `why[:120]`，
#:    而 `core.py:1443` 再切 `[:140]` ⇒ 只有前 ~116 字有机会进 prompt。
#:    P 长 74 字，连同 `[tasks_v8/e3wi] 1.00 — ` 共 97 字，**完整可见**（探针会验证）。
P = ("骨架要整份原样输出（含 _split 等辅助函数），"
     "只在 BEGIN/END TODO 之间填代码，其余逐字保留；顶部补上本题用到的 import。")
#: 等长安慰剂 Q：同样是「关于这次交付的一段话」，但不含任何可执行的具体动作。
Q = ("上一轮交付的结构安排与本题面的约定之间并无实质差异，"
     "细节请按规则逐条落实即可，无需再做额外的调整或补充说明，"
     "也不必在文字里解释理由，或给出示例代码。")

#: P 里的一句话，用来在 state 里判定「载荷到了」。
MARKER = "整份原样输出"
#: Q 里的一句话，用来判定「安慰剂到了但载荷没到」。
PLACEBO_MARKER = "并无实质差异"

#: 条件 → (目标条目, 新 why, None 表示不动)
CONDITIONS = {
    "s0_off": (None, None),
    "s1_base": (None, None),
    "s2_fix_nondeliv": ("e1ni", P),
    "s3_placebo": ("e3wi", Q),
    "s4_fix": ("e3wi", P),
}


def load() -> list[dict]:
    return [json.loads(x) for x in BASE.read_text(encoding="utf-8").splitlines() if x.strip()]


def write(path: Path, rows: list[dict]) -> str:
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    rows = load()
    assert len(rows) == 6, f"种子必须是 6 条，实际 {len(rows)}"
    assert abs(len(P) - len(Q)) == 0, f"P/Q 不等长：{len(P)} vs {len(Q)}"
    assert MARKER in P and MARKER not in Q, "标记词必须只在 P 里"
    assert PLACEBO_MARKER in Q and PLACEBO_MARKER not in P, "安慰剂标记必须只在 Q 里"
    OUT.mkdir(parents=True, exist_ok=True)

    print("=" * 96)
    print("V31 种子派生（确定性；重复跑逐字节相同）")
    print("=" * 96)
    print(f"种子 {BASE.name}  sha256={hashlib.sha256(BASE.read_bytes()).hexdigest()[:16]}  {len(rows)} 条")
    print(f"  scorer_sha={rows[0]['scorer_sha'][:24]}…  suite_sha={rows[0]['suite_sha']}")
    print(f"\nP（可行动，{len(P)} 字）: {P}")
    print(f"Q（等长安慰剂，{len(Q)} 字）: {Q}")
    print(f"|P-Q| = {abs(len(P) - len(Q))}")
    head = "[tasks_v8/e3wi] 1.00 — "
    print(f"as_line 可视预算：head {len(head)} + why[:120] ⇒ 140 上限内 P 完整可见 = "
          f"{len(head) + len(P) <= 140}（实际 {len(head) + len(P)}）")

    print(f"\n{'条件':18s} {'改哪条':8s} {'新 why 长':>9s}  送达到哪几题（按窗口矩阵）")
    window = {  # 由 probe 复算核对，这里只作人读的对照
        "e1wi": ["e2wi", "e3ni", "e3wi"],
        "e1ni": ["e2wi", "e3ni", "e3wi"],
        "e2wi": ["e2ni", "e3ni", "e3wi"],
        "e2ni": ["e2wi", "e3ni", "e3wi"],
        "e3wi": ["e2ni", "e2wi", "e3ni"],
        "e3ni": ["e2ni", "e2wi", "e3wi"],
    }
    digests = {}
    for name, (tid, why) in CONDITIONS.items():
        out = json.loads(json.dumps(rows))
        if tid is not None:
            for r in out:
                if r["task_id"] == tid:
                    r["why"] = why
                    break
            else:
                raise SystemExit(f"{tid} 不在种子里")
        digests[name] = write(OUT / f"{name}.jsonl", out)
        targets = [t for t, w in window.items() if tid is not None and tid in w] if tid else []
        print(f"{name:18s} {str(tid or '—'):8s} {(len(why) if why else 0):9d}  "
              f"{','.join(targets) if targets else ('（开关关）' if name == 's0_off' else '（未改动，送 5 题）')}")

    print(f"\n落盘 {OUT}")
    for k, v in digests.items():
        print(f"  {k:18s} sha256[:16]={v}")
    #: s1 必须与种子**逐字节**相同（它是「什么都不改」的对照）
    assert digests["s1_base"] == hashlib.sha256(BASE.read_bytes()).hexdigest()[:16], \
        "s1_base 与种子不一致 ⇒ 派生脚本自己改了不该改的东西"
    print("\nVERDICT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

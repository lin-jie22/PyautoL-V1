#!/usr/bin/env python3
"""§17-7e 的**题面派生器**：把 7d 的 suite 改成"显式指向记忆"的 hint suite。

为什么要有这个文件（而不是手改一份 jsonl）：
    7e 的全部实验价值都压在一句话上 —— **只改了"指向"，没改别的**。
    手抄一次就给自己留了一个"顺手把句子改顺"的口子，而那正是
    "不是同一把尺子"的经典入口（7c 已用 R7 量过这类扰动的量级）。
    ⇒ 派生必须**可复现、可反向校验**：契约 AZ6/AZ7 每次都从 7d suite
      重新派生一遍，再与磁盘上的 hint suite 逐字段比对。

变更（唯一一处，逐字节）：
    `按本项目定下的`  →  `按记忆里记着的项目`（每题**恰好一处**，+2 字）

⛔ 句尾的 `X惯例处理（惯例不在题面里）` **原样保留**：它仍然是真话 ——
   惯例不在题面里，只是在记忆里，而题面现在告诉你**去哪儿找**。

用法：
    python3 bench/build_g7e_hint.py \
        --base bench/heldout/g7e_heldout.jsonl \
        --out  bench/heldout/g7e_hint_heldout.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE_CLAUSE = "按本项目定下的"
HINT_CLAUSE = "按记忆里记着的项目"


def hint_prompt(prompt: str) -> str:
    """把一处 `BASE_CLAUSE` 换成 `HINT_CLAUSE`；出现次数不是 1 就**报错**。

    ⛔ 这里返回错误而不是静默兜底：少替换（0 次）等于这题没有指向记忆，
       多替换（>1 次）等于改了不止一处 ⇒ 两种都让"唯一变更"不成立。
    """
    n = prompt.count(BASE_CLAUSE)
    if n != 1:
        raise ValueError(f"题面里 `{BASE_CLAUSE}` 出现 {n} 次，必须恰好 1 次")
    return prompt.replace(BASE_CLAUSE, HINT_CLAUSE)


def derive(rows: list[dict]) -> list[dict]:
    """逐字段显式复制 —— 除 `prompt` 外一个字节都不动。"""
    out: list[dict] = []
    for row in rows:
        out.append({
            "id": row["id"],
            "prompt": hint_prompt(row["prompt"]),
            "tests": row["tests"],
            "oracle": row["oracle"],
            "partial": row["partial"],
            "null": row["null"],
            "meta": row["meta"],
        })
    return out


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    base_path = Path(args.base).expanduser()
    out_path = Path(args.out).expanduser()
    rows = _load(base_path)
    derived = derive(rows)

    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in derived)
    print(f"== 7d suite  =={base_path}  n={len(rows)}  sha={_sha(base_path.read_text())}")
    for a, b in zip(rows, derived):
        delta = len(b["prompt"]) - len(a["prompt"])
        ok = delta == len(HINT_CLAUSE) - len(BASE_CLAUSE)
        print(f"  {'✅' if ok else '❌'} {a['id']:24s} 题面 {len(a['prompt'])} → "
              f"{len(b['prompt'])} 字（{delta:+d}）")
        print(f"      旧：…{a['prompt'][a['prompt'].find(BASE_CLAUSE):][:26]}…")
        print(f"      新：…{b['prompt'][b['prompt'].find(HINT_CLAUSE):][:28]}…")
    out_path.write_text(text, encoding="utf-8")
    print(f"== hint suite == {out_path}  n={len(derived)}  sha={_sha(text)}")
    print("== 校验：反向替换必须逐字节回到 7d 题面 ==")
    back_ok = all(
        b["prompt"].replace(HINT_CLAUSE, BASE_CLAUSE) == a["prompt"]
        for a, b in zip(rows, derived))
    print("  " + ("✅ 全部逐字节可逆" if back_ok else "❌ 有题面不可逆"))
    return 0 if back_ok else 2


if __name__ == "__main__":
    sys.exit(main())

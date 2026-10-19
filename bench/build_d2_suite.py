#!/usr/bin/env python3
"""§17-7c C1：单条判别式灵敏度闸的题面构造器（零模型调用）。

只做一件事：把**恰好一条**判别式（或等长异域安慰剂）贴到 `g7s2` 题面尾部，
`g7s4` 一个字节不改。D2 / D2b 都是**诊断**，改了题面就不是同一把尺子，
⛔ 它们的读数不许进任何 Δ。

自检（跑之前先证明构造器没写错）：
  1. g7s4 的 prompt 与 heldout 原文**逐字节相同**（特异性对照的前提）；
  2. 判别式与安慰剂长度差 ≤ 5 字（"只是多了一句话"的前提）；
  3. 断言集合一个字节没动（只改 prompt）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# 目标题：g7s2 —— 题面已写明"引号内 ';' ':' 不算分隔符"，实测 2/5 通过。
# 本条判别式**不增加信息**，只把条件/动作/反例压成一句（上界道具，非蒸馏产物）。
RULE = ("当值以双引号开头时，先整体取出引号内原文，再回到引号外继续扫描；"
        "反例：若见第一个分号就切分，带分号的引号值会被从中间切断。")

# 安慰剂：同形状（条件 + 动作 + 反例）、等长、**异域**（与字符串解析无关）。
PLACEBO = ("当整理书架时，先把同一作者的书聚在一起，再在组内按出版年份排序；"
           "反例：若直接按年份排，同一作者的书会被别的书硬生生地隔开。")

TARGET = "g7s2_parse_headers"
PREFIX = "\n\n[经验] "


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def build(tasks: list[dict], inject: str | None) -> list[dict]:
    """把 inject 贴到 TARGET 的题面尾部；其余题逐字节不动。"""
    out = []
    for t in tasks:
        t2 = dict(t)
        if inject is not None and t["id"] == TARGET:
            t2["prompt"] = t["prompt"] + PREFIX + inject
        out.append(t2)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heldout", default="bench/heldout/g7_heldout.jsonl")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tasks = _load(Path(args.heldout))

    diff = abs(len(RULE) - len(PLACEBO))
    print(f"判别式 {len(RULE)} 字 ｜ 安慰剂 {len(PLACEBO)} 字 ｜ 差 {diff} 字")
    if diff > 5:
        print("❌ 等长断言失败：两者差 > 5 字 ⇒ 安慰剂不成立")
        return 1

    variants = {"D2": RULE, "D2b": PLACEBO}
    for name, inject in variants.items():
        built = build(tasks, inject)
        # 自检 1：非目标题逐字节不动
        for a, b in zip(tasks, built):
            if a["id"] != TARGET:
                assert json.dumps(a, ensure_ascii=False, sort_keys=True) == \
                       json.dumps(b, ensure_ascii=False, sort_keys=True), \
                       f"❌ {name}: {a['id']} 被改动（特异性对照失效）"
        # 自检 2：断言集合一个字节没动
        for a, b in zip(tasks, built):
            assert a["tests"] == b["tests"], f"❌ {name}: {a['id']} 的断言被改动"
        p = out / f"{name}_suite.jsonl"
        p.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in built) + "\n",
                     encoding="utf-8")
        h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        lens = {t["id"]: len(t["prompt"]) for t in built}
        print(f"✅ {name}: {p} sha256={h} prompt_len={lens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

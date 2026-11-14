#!/usr/bin/env python3
"""V102 派生题面：从 `g7j_n4_heldout` **复制**出两份，只改一句话。

⛔ 为什么这不算「造新题集」（主线禁令）：
  - `tests` / `oracle` / `partial` / `null` / `meta` **逐字节复制**，一个打分点都没新增；
  - 题目正文一字未改，只改「按记忆里记着的…惯例处理（惯例不在题面里）」这一句的**说法**；
  - 产物落在 `bench/evidence/`，**不进 `bench/heldout/`** ⇒ `check_counts` 的 67 题口径不变。

⭐ 关键设计：**改动全部发生在 ZK 标号之后**。
`（ZK1）（ZK2）（ZK3）（ZK4）` 在题面里是第 13~16 个词元，改动句在它们后面
⇒ **24-token 检索窗口里的 token 与位置完全不变** ⇒ 召回条数应当不变，
于是「题面字面」和「召回条数」这两件事被**拆开**了（V101 §169.6 的混淆）。

用法：python3 mk_suite_w.py <输出目录>
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parents[3] / "bench" / "heldout" / "g7j_n4_heldout.jsonl"

#: 三份变体：P0 原样，P1/P2 各改一句（都在 ZK 标号**之后**）
TAG = "（ZK1）（ZK2）（ZK3）（ZK4）"
VARIANTS = {
    "P0": None,
    "P1": ("（惯例不在题面里）", "（这几条惯例没有写在题面上）"),
    "P2": (f"{TAG}惯例处理", f"{TAG}惯例来做"),
}


def main() -> None:
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v102/suites")
    out.mkdir(parents=True, exist_ok=True)
    src = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]
    tests_sha = {r["id"]: hashlib.sha256(
        json.dumps(r["tests"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8] for r in src}
    print("原套件 tests_sha：", tests_sha)

    for name, sub in VARIANTS.items():
        rows = []
        for r in src:
            r2 = json.loads(json.dumps(r, ensure_ascii=False))  # 深拷贝
            if sub is not None:
                old, new = sub
                assert r2["prompt"].count(old) == 1, f"{name}: {r['id']} 里 {old!r} 出现 {r2['prompt'].count(old)} 次"
                r2["prompt"] = r2["prompt"].replace(old, new)
            # 硬闸：除 prompt 外**所有字段**逐字节不变
            for k in r:
                if k == "prompt":
                    continue
                assert json.dumps(r2[k], sort_keys=True, ensure_ascii=False) == \
                       json.dumps(r[k], sort_keys=True, ensure_ascii=False), f"{name}: {r['id']} 的 {k} 被动了"
            rows.append(r2)
        p = out / f"{name}.jsonl"
        p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
        d = {t: len(x["prompt"]) for t, x in zip([r["id"] for r in rows], rows)}
        print(f"  {name} -> {p}  题面字数={d}")

    # 位置自检：ZK 标号之前的部分必须逐字节相同
    base = [r["prompt"] for r in src]
    for name, sub in VARIANTS.items():
        if sub is None:
            continue
        cur = [json.loads(l)["prompt"] for l in (out / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        for a, b, r in zip(base, cur, src):
            assert a.split(TAG)[0] == b.split(TAG)[0], f"{name}: {r['id']} 的 ZK 标号之前被改了 ⛔"
    print("✅ ZK 标号之前的部分逐字节相同（检索窗口里的 token 与位置不变）")


if __name__ == "__main__":
    main()

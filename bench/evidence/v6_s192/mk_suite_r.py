#!/usr/bin/env python3
"""V103 派生题面：**纯格式、零语义**的两种扰动。

| 份 | 改动 | 为什么是「零语义」 |
|---|---|---|
| P3 | `（惯例不在题面里）` → `(惯例不在题面里)` | 只把**全角括号**换成 ASCII 括号，一个字都没换 |
| P4 | `（惯例不在题面里）` → `（惯例不在题面里） ` （句号前加一个 ASCII 空格） | 只加一个空白 |

⛔ 与 V102 的 P1/P2 不同：那两句**换了词**，这两份**没换词**。
若 P3/P4 也把 g7d3 推到同一个盆 `5755e033` ⇒ **效应 = 扰动，与语义无关**（决定性）。

用法：python3 mk_suite_r.py <输出目录>
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parents[3] / "bench" / "heldout" / "g7j_n4_heldout.jsonl"
OLD = "（惯例不在题面里）"
VARIANTS = {
    "P3": (OLD, "(惯例不在题面里)"),
    "P4": (OLD, "（惯例不在题面里） "),
}


def main() -> None:
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v103/suites")
    out.mkdir(parents=True, exist_ok=True)
    src = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]
    print("原套件 tests_sha：", {r["id"]: hashlib.sha256(
        json.dumps(r["tests"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8] for r in src})
    for name, (old, new) in VARIANTS.items():
        rows = []
        for r in src:
            r2 = json.loads(json.dumps(r, ensure_ascii=False))
            assert r2["prompt"].count(old) == 1, f"{name}: {r['id']} 里 {old!r} 出现 {r2['prompt'].count(old)} 次"
            r2["prompt"] = r2["prompt"].replace(old, new)
            for k in r:
                if k == "prompt":
                    continue
                assert json.dumps(r2[k], sort_keys=True, ensure_ascii=False) == \
                       json.dumps(r[k], sort_keys=True, ensure_ascii=False), f"{name}: {r['id']} 的 {k} 被动了"
            rows.append(r2)
        (out / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
        print(f"  {name} -> 字数 {[len(x['prompt']) for x in rows]}")
    # 自检：ZK 标号之前逐字节相同
    base = [r["prompt"] for r in src]
    for name in VARIANTS:
        cur = [json.loads(l)["prompt"] for l in (out / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        for a, b in zip(base, cur):
            assert a.split("（ZK1）")[0] == b.split("（ZK1）")[0], f"{name}: ZK 标号之前被改了 ⛔"
    print("✅ ZK 标号之前逐字节相同（检索窗口里的 token 与位置不变）")


if __name__ == "__main__":
    main()

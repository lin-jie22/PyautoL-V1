#!/usr/bin/env python3
"""V101 种子：**换尺子** —— 用 V100 的四个种子库，去跑四套**题面不同、打分用例逐字节相同**的套件。

四个种子（只差 ZK2 里的一个字符）：

| 臂 | ZK2 改动 | 在 `g7j_n4` 上的 mean | 为什么选它 |
|---|---|---|---|
| A0 | 无（原规则） | 0.85 | 基线 |
| A1 | `#34` `、`→`,` | **1.00** | V100 闭环的胜者（= V99 O8） |
| A2 | `#39` `。`→`.` | **1.00** | V99 O1，**另一个**满分入口 |
| A3 | `#18` `，`→`,` | 0.85 | 同分不同字节的对照（g7d3 盆 = `419ec792`，与 A0 的 `075b4099` **不同盆同分**） |

⇒ 两组「**同分不同字节**」：(A0,A3) 在 0.85、(A1,A2) 在 1.00。
   这正是 V101 的核心问题：**换一套题面，能不能把它们分开？**

⚠️ 种子库文件**不可逐字节重建**（`facts` 表有 `ts`/`updated_ts` 两列），
   所以 ⛔ 别拿 `memory.db` 的 sha 当装置指纹 —— 指纹只能取**事实文本**与**答案**。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from mw.memory import MemoryStore  # noqa: E402

BASE = {
    "ZK1": "（ZK1）去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "ZK2": "（ZK2）金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "ZK3": "（ZK3）开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，其余无法识别的返回 None。",
    "ZK4": "（ZK4）截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），不超长时原样返回。",
}
ORDER = ("ZK1", "ZK2", "ZK3", "ZK4")

#: 臂 → ZK2 上的一处替换（位置, 旧, 新）；A0 = 不动
EDITS = {
    "A0": None,
    "A1": (34, "、", ","),
    "A2": (39, "。", "."),
    "A3": (18, "，", ","),
}


def body_of(arm: str) -> dict[str, str]:
    body = dict(BASE)
    e = EDITS[arm]
    if e is not None:
        i, old, new = e
        assert body["ZK2"][i] == old, f"{arm}: ZK2[{i}] 是 {body['ZK2'][i]!r}，期望 {old!r}"
        body["ZK2"] = body["ZK2"][:i] + new + body["ZK2"][i + 1:]
    return body


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v101/seeds")
    out.mkdir(parents=True, exist_ok=True)
    lens = set()
    for arm in EDITS:
        body = body_of(arm)
        assert len(body["ZK2"]) == 40, f"{arm}: ZK2 长度 {len(body['ZK2'])} ≠ 40"
        d = out / arm
        d.mkdir(parents=True, exist_ok=True)
        db = d / "memory.db"
        if db.exists():
            db.unlink()
        m = MemoryStore(db)
        for tag in ORDER:
            m.commit_fact(body[tag], source="abstract", confidence=0.6)
        m.close()
        lens.add(tuple(len(body[t]) for t in ORDER))
    assert len(lens) == 1, f"⛔ 四臂长度不齐：{lens}"
    print(f"✅ 四个种子已建（同长 {sorted(lens)[0]}，合计 {sum(sorted(lens)[0])} 字）：{out}")
    for arm in EDITS:
        print(f"   {arm}  ZK2 = {body_of(arm)['ZK2']}")


if __name__ == "__main__":
    main()

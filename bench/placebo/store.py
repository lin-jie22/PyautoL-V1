#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""磁盘层：把一个记忆库的某一层文本**整层替换**，产出一份新的记忆目录。

这是第十七轮 `bench/placebo.py` 里那一半（"跑完之后改库"），
第十八轮又需要另一半（"写入瞬间替换"，见 `patch.py`），
两半共用同一套等长闸门 —— 所以它们现在住在同一个包里。

★ 为什么整个目录复制而不是原地改
  实验的每一个臂都必须从**同一个字节**的记忆起跑。原地改会让先跑的臂
  把它自己答完题的答案、它自己沉淀的世界模型事实写进共享目录，
  污染后跑的臂 —— 这正是第十六轮的真实缺陷（§16.16①）：
  B/C 共用 `--memory-dir`，C 的起跑库里躺着 B 跑出来的 1 条 episode，
  同一个 C 从 0.8500 掉到 0.7500，一个 +0.40 的单题涨幅整个消失。

★ 三道闸（都在 `replace_texts` 里，任一失守就**带理由地**退出）
  1. 目标层 0 条 ⇒ 拒绝在空层上做安慰剂（那是"空臂 vs 空臂"）。
  2. 逐条等长（`allow_len_delta` 默认 0）⇒ 不补空格、不截断。
  3. 逐条相异 ⇒ 等于原文的不是安慰剂，是实验组本人。
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

#: 与 `mw.abstractor.ABSTRACT_SOURCE` / `mw.memory.Fact.source` 同源。
#: 这里不 import mw —— 本包是**实验工具**，不属于内核；硬编码常量并在
#: 契约里断言两侧同源（AO1），可以让"改一个忘了另一个"当场变红。
ABSTRACT_SOURCE = "abstract"


def copy_dir(src: Path, out: Path) -> None:
    """整目录复制。`out` 必须不存在 —— 存在就说明调用方意图不明（可能想覆盖旧数据）。"""
    if out.exists():
        raise SystemExit(f"❌ 目标目录已存在：{out}\n"
                         f"   每个臂应当是**全新的一份**，复用目录等于承认"
                         f"我不确定它里面到底跑过什么。")
    shutil.copytree(src, out)


def replace_texts(db: Path, fn, *, source: str = ABSTRACT_SOURCE,
                  allow_len_delta: int = 0) -> list[tuple[str, str]]:
    """把库里所有 `source=?` 的文本逐条替换。返回 [(原文, 新文)]。

    ⛔ FTS5 是**外部内容表之外的独立索引**，只 UPDATE facts 不改 facts_fts
    ⇒ 检索到的还是旧文本，而且**不会报错**（安静地答错题）。必须同步改。
    """
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, text FROM facts WHERE source=? ORDER BY id",
            (source,)).fetchall()
        if not rows:
            raise SystemExit(
                f"❌ `source={source}` 这一层 0 条 ⇒ 安慰剂比对退化成'空 vs 空'。\n"
                f"   这种读数最危险：它逐题全 0、看起来极其干净，\n"
                f"   而它证明的只有'这一段什么都没发生'。不跑。")

        pairs: list[tuple[str, str]] = []
        for r in rows:
            old = r["text"]
            new = fn(old)
            d = len(new) - len(old)
            if abs(d) > allow_len_delta:
                raise SystemExit(
                    f"❌ 长度不等（原文 {len(old)} 字，替换文 {len(new)} 字，"
                    f"差 {d:+d}，允许 ±{allow_len_delta}）\n"
                    f"   原文：{old}\n   替换：{new}\n"
                    f"   ⛔ 不补空格、不截断 —— 补出来的那几个字会成为第二个自变量，"
                    f"下场实验就得同时解释它。")
            if new == old:
                raise SystemExit(f"❌ 替换后与原文相同（{new!r}）—— 那是实验组本人，不是安慰剂。")
            pairs.append((old, new))

        for (old, new), r in zip(pairs, rows):
            fid = int(r["id"])
            # ON CONFLICT(key) 保证两条安慰剂撞车时也只是累加 hits，不会多一行
            key = hashlib.sha256(new.lower().encode("utf-8")).hexdigest()[:32]
            conn.execute("UPDATE facts SET text=?, key=? WHERE id=?", (new, key, fid))
            conn.execute("DELETE FROM facts_fts WHERE rowid=?", (fid,))
            conn.execute("INSERT INTO facts_fts(rowid, text) VALUES (?,?)", (fid, new))
        conn.commit()

        # 自检：读回来核对，**不**相信刚才那句 UPDATE
        back = conn.execute(
            "SELECT text FROM facts WHERE source=? ORDER BY id", (source,)).fetchall()
        got = [x["text"] for x in back]
        want = [n for _, n in pairs]
        if got != want:
            raise SystemExit(f"❌ 回读不一致：期望 {want} 实际 {got}")
        dup = conn.execute(
            "SELECT COUNT(*) c FROM (SELECT key FROM facts GROUP BY key HAVING COUNT(*)>1)"
        ).fetchone()["c"]
        if dup:
            raise SystemExit(f"❌ 去重约束破了：重复行 {dup} 条")
        return pairs
    finally:
        conn.close()


def cleanup_on_failure(out: Path) -> None:
    """失败就要留下**干净现场**：半成品目录比没有目录更骗人，
    它会让下一次运行因为"目录已存在"而失败，把原因藏起来。"""
    shutil.rmtree(out, ignore_errors=True)
    print(f"   已清理半成品目录：{out}", file=sys.stderr)

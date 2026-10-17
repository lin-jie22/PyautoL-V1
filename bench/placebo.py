#!/usr/bin/env python3
"""把一个记忆库的**抽象层**替换成"安慰剂"，产出一个新的记忆目录。

★ 它存在的唯一理由（§16.15④b / §17-13）
  第十六轮测到：同一个记忆状态、同一批考试题，读到抽象层的那一臂（C）
  比空记忆那一臂（A）高 **+0.05**，且地毯式砍掉一半抽象之后仍然成立。
  但那条幸存经验只有一句话，而且**与受益题的内容几乎无关**。
  ⇒ 有一种同样能解释数据的假说：**收益来自"上下文里多了一段文本"**
     （存在性效应），而不是那段文本的**内容**。

  ⛔ **T=0 保证确定性，不保证因果性。**
     同一个输入给出同一个确定的输出，只说明它是可复现的；
     不说明这个输出是被"经验"改的，还是被"多出来的 13 个字"改的。

  安慰剂臂就是用来拆开这两者的：**除内容以外什么都不变的第四臂**。

★ 三条硬约束（写进代码，不是约定）
  1. **等长**：替换文本与原文字符数必须**严格相等**。
     不等就报错退出 —— 绝不静默补空格/截断。长度不相等的话，
     "多写的这几个字"就变成第二个变量，实验立刻失去归因能力。
  2. **不许拿到空层**：抽象层 0 条 ⇒ 直接失败。
     在空层上做安慰剂等于拿空臂跟空臂比，产出的 Δ 是噪声，而且是
     看起来很干净（逐题全 0）的噪声 —— 那是本项目最危险的一种读数。
  3. **不许等于原文**：替换后若与原文相同 ⇒ 失败。那不是安慰剂，那是 C 臂本人。

★ 为什么整个目录复制而不是原地改
  实验的每一个臂都必须从**同一个字节**的记忆起跑。原地改会让先跑的臂
  把它自己答完题的答案、它自己沉淀的世界模型事实写进共享目录，
  污染后跑的臂 —— 这正是第十六轮的真实缺陷（见 §16.16①）。

用法：
    # D1 打乱：原文字符的一个排列（同字多重集、零语义、必然等长）
    python3 bench/placebo.py --src /tmp/v1_s16/mem --out /tmp/v1_s16/mem_D1 \\
            --variant scramble --seed 17

    # D2/D3：调用方指定等长文本
    python3 bench/placebo.py --src /tmp/v1_s16/mem --out /tmp/v1_s16/mem_D2 \\
            --variant text --text '编译报错时先读最底层栈信息'

返回值：0 成功；非 0 一定是**把理由念出来之后**才退的（R41：无静默兜底）。
"""

from __future__ import annotations

import argparse
import hashlib
import random
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: 与 `mw.abstractor.ABSTRACT_SOURCE` / `mw.memory.Fact.source` 同源。
#: 这里不 import mw —— 本脚本是**实验工具**，不属于内核；硬编码常量并在
#: 契约里断言两侧同源（AO1），可以让"改一个忘了另一个"当场变红。
ABSTRACT_SOURCE = "abstract"

VARIANTS = ("scramble", "text")


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def scramble(text: str, seed: int) -> str:
    """把 `text` 的字符打乱 —— **必然等长**，且字符多重集不变。

    为什么打乱是合格的安慰剂：它保留了全部**表层统计量**
    （长度、字集、字频），只摧毁**语序承载的语义**。
    ⇒ 若 C 仍优于打乱臂，则差别只能来自"语序里的意思"，不可能来自字数。

    自己写 Fisher–Yates 而不用 `random.shuffle`：后者跨 Python 版本的行为
    没有承诺。这里连种子都用原文的 sha256 派生，给定输入必然同一输出。
    """
    chars = list(text)
    for attempt in range(1000):
        r = random.Random(int.from_bytes(
            hashlib.sha256(f"{seed}:{attempt}:{md5(text)}".encode("utf-8")).digest()[:8],
            "big"))
        ys = list(chars)
        for i in range(len(ys) - 1, 0, -1):
            j = r.randrange(i + 1)
            ys[i], ys[j] = ys[j], ys[i]
        out = "".join(ys)
        if out != text:          # 概率极低也要挡：等于原文就不是安慰剂
            return out
    raise RuntimeError(f"打乱 1000 次都没能产生不同于原文的排列：{text!r}")


def copy_dir(src: Path, out: Path) -> None:
    """整目录复制。`out` 必须不存在 —— 存在就说明调用方意图不明（可能想覆盖旧数据）。"""
    if out.exists():
        raise SystemExit(f"❌ 目标目录已存在：{out}\n"
                         f"   每个臂应当是**全新的一份**，复用目录等于承认"
                         f"我不确定它里面到底跑过什么。")
    shutil.copytree(src, out)


def replace_abstract(db: Path, fn, *, allow_len_delta: int = 0) -> list[tuple[str, str]]:
    """把库里所有 `source=abstract` 的文本逐条替换。返回 [(原文, 新文)]。

    ⛔ FTS5 是**外部内容表之外的独立索引**，只 UPDATE facts 不改 facts_fts
    ⇒ 检索到的还是旧文本，而且**不会报错**（安静地答错题）。必须同步改。
    """
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, text FROM facts WHERE source=? ORDER BY id",
            (ABSTRACT_SOURCE,)).fetchall()
        if not rows:
            raise SystemExit(
                f"❌ 抽象层 0 条 ⇒ 安慰剂比对退化成'空 vs 空'。\n"
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
                raise SystemExit(f"❌ 替换后与原文相同（{new!r}）—— 那是 C 臂本人，不是安慰剂。")
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
            "SELECT text FROM facts WHERE source=? ORDER BY id",
            (ABSTRACT_SOURCE,)).fetchall()
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="把记忆库的抽象层换成安慰剂，产出一份新的记忆目录")
    p.add_argument("--src", required=True, help="源记忆目录（只读，不会被改）")
    p.add_argument("--out", required=True, help="新目录（必须不存在）")
    p.add_argument("--variant", required=True, choices=VARIANTS,
                   help="scramble=打乱原文字符；text=用 --text 指定的等长文本")
    p.add_argument("--text", default="", help="--variant text 时的替换文本")
    p.add_argument("--seed", type=int, default=17, help="打乱用的种子")
    p.add_argument("--allow-len-delta", type=int, default=0,
                   help="允许的字符数偏差（默认 0 = **严格等长**；"
                        "给非 0 只用于诊断，实验时必须为 0）")
    args = p.parse_args(argv)

    src = Path(args.src).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()

    if not src.is_dir():
        print(f"❌ 源目录不存在：{src}")
        return 2
    if not (src / "memory.db").exists():
        print(f"❌ 源目录里没有 memory.db：{src}")
        return 2
    if args.variant == "text" and not args.text.strip():
        print("❌ --variant text 必须给 --text")
        return 2
    if args.variant != "text" and args.text:
        print("❌ 给了 --text 却没选 --variant text（防止写了文本却没生效）")
        return 2

    copy_dir(src, out)

    def fn(old: str) -> str:
        if args.variant == "scramble":
            return scramble(old, args.seed)
        return args.text.strip()

    try:
        pairs = replace_abstract(out / "memory.db", fn,
                                 allow_len_delta=args.allow_len_delta)
    except SystemExit as e:
        print(e)
        # 失败就要留下**干净现场**：半成品目录比没有目录更骗人，
        # 它会让下一次运行因为"目录已存在"而失败，把原因藏起来。
        shutil.rmtree(out, ignore_errors=True)
        return 1

    print(f"源：{src}")
    print(f"产：{out}")
    print(f"变体：{args.variant}"
          + (f" seed={args.seed}" if args.variant == "scramble" else ""))
    print(f"抽象层替换 {len(pairs)} 条（等长约束 ±{args.allow_len_delta}）：")
    for old, new in pairs:
        print(f"   {len(old):>3d}字  {old}")
        print(f"   {len(new):>3d}字  {new}   md5 {md5(old)} → {md5(new)}")
    delta = sum(len(n) - len(o) for o, n in pairs)
    print(f"总字符差 {delta:+d}"
          f"（必然为 0 —— 它是上面那条**逐条**闸的推论，不是第二把锁）")
    # ⛔ 刻意**不**在这里再写一遍 `if delta != 0: return 1`。
    #   撤掉实现才知道原因：早先这里有两道闸（逐条 + 净差），
    #   撤回态验证时删掉逐条那一道，AO4 竟然**仍是绿的** —— 净差闸把测试挡住了。
    #   ⇒ 一把 assertion 永远测不到另一把 assertion 是不是还在。
    #   V5 迁移过来的第一条硬规则就是这个：**同一事实只声明一处**。
    #   这里只留一处闸门，净差降级为**读数**（读了不等于又判了一次）。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""§17-7d 三臂实验的**种子构建器**（DESIGN §17-7 ★7d 事前登记）。

与 7a/7b 的根本差别（必须先读懂这一句）：
    7a/7b 的 H 臂 facts 是**蒸馏产物**（学习链的终点）；
    **7d 的 H 臂 facts 是手写的项目惯例** —— 因为 7c 已证明那两道 heldout
    是 spec-complete（判别条件本来就写在题面里），记忆**不可能**增加解题信息。
    所以 7d 把信息差挪到题面之外，测的是**通道容量**，不是学习。
    ⛔ 因此本轮读数只能说"通道能/不能把题面缺失的判别条件送进决策"，
       不许说成"系统学会了"。

三臂（臂间唯一差异 = 起跑库里的 facts）：
    memH/  每题一条**手写惯例**（source=abstract，≤120 字，且文本里带函数名，
           好让 FTS 有词可中 —— 这不是作弊，是让"该送达的那条"可被检索到）
    memP/  与 H 逐条**等长、同条数、同 source** 的异域中性句（R7 的载体）
    memE/  空表

R1 送达预检（比 7a/7b 更严）：**每道 heldout 题都必须取回它自己那条惯例**，
    只数条数不够 —— `recall()` 的 FTS 命中非空时**不会**兜底 top-k，
    条数够但取回的是别人的惯例，等于没送达。

用法：
    python3 bench/build_g7d_seed.py --out-dir /tmp/v1_g7d/seed \
        --heldout bench/heldout/g7d_heldout.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BENCH = Path(__file__).resolve().parent
_ROOT = _BENCH.parent
for _p in (str(_ROOT), str(_BENCH)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mw.abstractor import ABSTRACT_SOURCE      # noqa: E402
from mw.memory import MemoryStore              # noqa: E402

#: 惯例的**唯一事实源**（手写；probe_g7d.py 也从这里读，避免两份文本漂移）。
#: 写法要求：①只说判别条件，不说怎么实现；②必须含函数名（FTS 可检索）；
#:          ③≤120 字（`core.py` 拼上下文时 `f.text[:120]` 会静默截断）。
FACTS: dict[str, str] = {
    # ⚠️ 装置修正⑨：文本里**不许出现函数名/题面里的标识符** —— 否则该条会被
    #    FTS 精确命中、H 臂只送 1 条，而 P 臂（无词可中）走兜底送全部 ⇒
    #    两臂**注入字符数不等**，R7 的"等长"在注入层就破了。
    "g7d2_dedupe_keep":
        "去重惯例：比较前先去掉首尾空白并转小写，遇到重复时保留第一次出现的那条原文。",
    "g7d3_fmt_amount":
        "金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。",
    "g7e1_parse_flag":
        "开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，"
        "其余无法识别的返回 None。",
    "g7e2_truncate_tail":
        "截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），"
        "不超长时原样返回。",
}

CAP = 120          # core.py 的注入截断

#: ★7f（第三十四轮）的**唯一变更**：把三条**未被采用**的惯例改写为
#: 「原句 + 一个输入→输出例子」的统一句式。`g7d2_dedupe_keep`（7e 已采用的那条）
#: **逐字节不动** —— 否则无法回答"是例子起作用，还是把已经会的那条改坏了"。
#:
#: ⛔ 装置修正⑪：例子**不许写函数名**。
#:   DESIGN 原模板是 `truncate_tail('0123456789', 4) == '…6789'`，含函数名 ⇒
#:   题面里的 `truncate_tail` 会被 FTS 精确命中该条 ⇒ H 臂注入从 4 条降到 1 条。
#:   P 臂（骨架安慰剂保留 ASCII）也会降到 1 条，两臂仍等长（R1 不报警）——
#:   但这时"文本形式"与"注入量/检索精度"**两个变量同时变了**，读数无法归因。
#:   ⇒ 例子只写输入→输出；契约 AZ10 钉住"与题面的 token 交集必须与 7d 逐题完全相同"。
FACTS_G7F: dict[str, str] = dict(FACTS)
FACTS_G7F.update({
    "g7d3_fmt_amount":
        "金额惯例：千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号。"
        "例：1234.5 要写成 1 234.50，-1234.5 写成 (1 234.50)。",
    "g7e1_parse_flag":
        "开关惯例：只把 y/yes/1 当成真，'true'、'on' 这类历史脏值一律算假，"
        "其余无法识别的返回 None。"
        "例：'YES' 为真、'on' 为假、'maybe' 为 None。",
    "g7e2_truncate_tail":
        "截断惯例：超长时保留最后 n 个字符，前面加一个省略号字符 '…'（U+2026），"
        "不超长时原样返回。例：'0123456789' 取 n=4 得 '…6789'。",
})

#: 变体表（`--variant` 选）。⛔ 默认 `7d`：7d/7e 的历史读数必须仍可复现；
#: 7f 只能通过显式 `--variant 7f` 进来，不许把默认值悄悄换掉。
VARIANTS: dict[str, dict[str, str]] = {"7d": FACTS, "7f": FACTS_G7F}

# ──────────────────────────────────────────────────────────────────────────
# ★7g（第三十五轮）：**邻域对照臂 N**（DESIGN §17-7 ★7g 事前登记）
#
# 要回答的问题：7f 那个 0.80（观察题 `g7d2`，邻域 182→291 字）是**局部**的还是
# **全局**的？做法：观察对象的惯例**逐字节冻结**，只改**其余三条**的注入长度。
#
# 为什么这条轴能成立（必读，否则会把读数读反）：`recall()` 有**两条路径**
#   · `g7d2`/`g7d3`：题面里没有任何能与 facts 对上的 token（`fts_query` 把中文
#     整段当一个 token，而 `_TOKEN` 把 CJK 并进了字符类）⇒ FTS 无命中 ⇒
#     **fallback top-k** ⇒ 上下文**恒 4 条**（`score=0.000` 就是这个分支的指纹），
#     且自己的惯例**排在最后**（`ORDER BY hits DESC, updated_ts DESC`）。
#   · `g7e1`/`g7e2`：FTS **精确命中**（`g7e1` 中 `None`、`g7e2` 中 `n`）⇒ 上下文**恒 1 条**。
# ⇒ 邻域只在 4 条族里"在场"；1 条族的曲线**必须**是平的 —— 这是可被推翻的断言。
# ──────────────────────────────────────────────────────────────────────────

#: 填充句：纯中文、**无 ASCII**（ASCII 会改 FTS 命中 ⇒ 与"只改长度"混成一锅）、
#: 无语义指向（不引入题面 token）。
PAD = "此处为长度档位所用的填充文字与本题无关。"

#: 档位 = **每条邻居**的注入字符数。上端 120 = `core.py` 里 `f.text[:120]` 的饱和点：
#: 再长也不会进上下文，所以轴在上端**按构造封顶**，不许假装还能往上走。
NEIGH_LEVELS: dict[str, int] = {"l40": 40, "l60": 60, "l84": 84, "l120": 120}

#: 观察对象 → (冻结键, 冻结文本的**事实源**)。
#: ⛔ o2 冻结的是 **7f 的 82 字形式**（含例子）：要问的是"7f 里 `g7e2` 的 1.00
#:   是不是邻域带出来的" ⇒ 必须在**它的文本已经含有例子**的前提下改邻域。
OBSERVERS: dict[str, tuple[str, dict[str, str]]] = {
    "o1": ("g7d2_dedupe_keep", FACTS),
    "o2": ("g7e2_truncate_tail", FACTS_G7F),
}


def _pad_to(text: str, target: int) -> str:
    """把一条惯例调到 `target` 字：**保留原句**，再用固定填充句补到长度；超长则截断。

    ⚠️ 只截断不重写：把原句换掉就变成"换了内容"，与"只改长度"这个单变量冲突。
    """
    if len(text) >= target:
        cut = text[:target].rstrip()          # ⚠️ 不许以空白结尾：normalize 会 strip
        if len(cut) < target:                 # 截在空格上时用填充补齐，保持字数为准
            cut += PAD[:target - len(cut)]
        return cut
    need = target - len(text)
    return text + (PAD * (need // len(PAD) + 1))[:need]


def build_neighborhood(level: str, observer: str) -> dict[str, str]:
    """一档邻域配置：观察对象**逐字节冻结**，其余三条调到 `level` 指定的长度。"""
    target = NEIGH_LEVELS[level]
    obs_key, pool = OBSERVERS[observer]
    return {k: (pool[k] if k == obs_key else _pad_to(FACTS[k], target))
            for k in FACTS}


for _obs in OBSERVERS:
    for _lvl in NEIGH_LEVELS:
        VARIANTS[f"7g_{_obs}_{_lvl}"] = build_neighborhood(_lvl, _obs)


#: 骨架安慰剂的汉字池（书架/天气域）—— 只用来替换汉字，不改 ASCII 与标点。
_SCRAMBLE_POOL = (
    "书架旅行手册目录标签阳台花盆天气雨伞抽屉螺丝刀格子傍晚光线整齐出版年份"
    "朝南方向周末小到中雨出门厨房上次用完放回原来"
)


def check_placebo(h_texts: list[str], p_texts: list[str]) -> list[str]:
    """安慰剂与处理的**等价性检查** —— 纯函数，返回违规清单（空 = 通过）。

    ⛔ 为什么要有这个函数，而不是在 `build_placebo` 里 `assert`：
       内联 assert 一触发就抛异常 ⇒ 锚层测试整文件崩掉（R13 判为"崩了不是判红"），
       AZ1–AZ3 也就无法逐条撤回。做成纯函数后，违规是**可读的数据**，
       构建器据此拒绝出种子，锚层据此逐条判红（第十九轮 AQ4/AQ6/AQ7 的教训）。
    """
    bad: list[str] = []
    if len(h_texts) != len(p_texts):
        bad.append(f"条数不等：处理 {len(h_texts)} vs 安慰剂 {len(p_texts)}")
        return bad
    for i, (a, b) in enumerate(zip(h_texts, p_texts)):
        if len(a) != len(b):
            bad.append(f"#{i} 长度不等 {len(a)} vs {len(b)}")
        if [c for c in a if ord(c) < 128] != [c for c in b if ord(c) < 128]:
            bad.append(f"#{i} ASCII 序列被改动（会改变 FTS 命中 ⇒ 不等价）")
        if a == b:
            bad.append(f"#{i} 安慰剂与原句相同（汉字没被打散）")
    return bad


def build_placebo(h_texts: list[str]) -> list[str]:
    """**骨架安慰剂**（装置修正⑨c）：逐字等长、ASCII token 与标点原位保留、
    汉字换成书架/天气域的确定性字符。

    为什么不用"另写一句异域话"：`recall()` 的 FTS 是 **OR + 精确 token**，
    只要惯例里出现题面里也有的 ASCII token（`true` / `None` / `n`），那条就被
    精确命中 ⇒ H 只送 1 条；而另写的中文安慰剂无词可中 ⇒ P 走兜底送全部 4 条。
    两臂**注入字符数不等**，R7 的"等长"在注入层就破了（实测：50 字 vs 231 字）。
    保留 ASCII token ⇒ 两臂的检索行为**按构造相同**，等长也按构造成立。
    """
    out = []
    for t in h_texts:
        buf = []
        for i, ch in enumerate(t):
            if ord(ch) < 128 or not ("\u4e00" <= ch <= "\u9fff"):
                buf.append(ch)                      # ASCII / 标点：原位保留
            else:
                buf.append(_SCRAMBLE_POOL[(ord(ch) * 31 + i) % len(_SCRAMBLE_POOL)])
        out.append("".join(buf))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out-dir", required=True)
    p.add_argument("--heldout", required=True)
    #: ★7f：默认 `7d`（历史可复现）。7f 必须显式选，不许偷偷换默认值。
    p.add_argument("--variant", default="7d", choices=sorted(VARIANTS))
    args = p.parse_args(argv)
    facts = VARIANTS[args.variant]

    out = Path(args.out_dir).expanduser()
    # 装置修正⑨b：**不许在已有种子的目录上重建** —— 第一次重建时旧 facts 与新
    # facts 混在同一个库里（H 臂 1 条旧 + 1 条新），R1 当场报警。宁可拒绝启动。
    stale = [p for p in (out / "memH" / "memory.db", out / "memP" / "memory.db")
             if p.exists()]
    if stale:
        print(f"❌ 输出目录已有种子库，拒绝在其上追加：{stale}\n"
              f"   ⇒ 先删掉 {out} 再重建（旧 facts 混入 = 处理不纯）")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    heldout = [json.loads(l) for l in
               Path(args.heldout).read_text().splitlines() if l.strip()]
    ids = [t["id"] for t in heldout]

    # ── 闸 0：题与惯例必须一一对应（漏一条 = 那道题注定被当"通道坏了"）──
    print(f"== 惯例变体 = {args.variant}（{len(facts)} 条）==")
    missing = [i for i in ids if i not in facts]
    if missing:
        print(f"❌ 这几道题没有对应的惯例：{missing} ⇒ 整组作废（不许跑）")
        return 1
    unused = [k for k in facts if k not in ids]
    if unused:
        print(f"   ⓘ 未入选的惯例 {len(unused)} 条（不注入）：{unused}")
    h_texts = [facts[i] for i in ids]
    over = [(i, len(t)) for i, t in zip(ids, h_texts) if len(t) > CAP]
    if over:
        print(f"❌ 惯例超过 {CAP} 字（会被 core.py 静默截断）：{over}")
        return 1

    print("== H 臂：手写惯例（容量测试，非学习）==")
    (out / "memH").mkdir(parents=True, exist_ok=True)
    memH = MemoryStore(out / "memH" / "memory.db")
    for i, t in zip(ids, h_texts):
        memH.commit_fact(t, source=ABSTRACT_SOURCE, confidence=0.5)
        print(f"  · {i:24s} len={len(t)}  {t}")

    print("== P 臂：逐条等长异域中性句（R7 载体）==")
    (out / "memP").mkdir(parents=True, exist_ok=True)
    memP = MemoryStore(out / "memP" / "memory.db")
    p_texts = build_placebo(h_texts)
    bad = check_placebo(h_texts, p_texts)
    if bad:
        print("❌ 安慰剂与处理不等价 ⇒ 拒绝出种子（R7 的前提不成立）：")
        for b in bad:
            print(f"   · {b}")
        return 1
    for i, t in zip(ids, p_texts):
        memP.commit_fact(t, source=ABSTRACT_SOURCE, confidence=0.5)
        print(f"  · {i:24s} len={len(t)}  {t}")

    print("== E 臂：空表 ==")
    (out / "memE").mkdir(parents=True, exist_ok=True)
    memE = MemoryStore(out / "memE" / "memory.db")

    # ── R1：每道题都必须取回**它自己那条** ────────────────────────────────
    print("== R1 送达预检（own-fact，不是只数条数）==")
    ok = True
    for t in heldout:
        want = facts[t["id"]]
        gotH = [f.text for f in memH.recall(t["prompt"], k=6)]
        gotP = [f.text for f in memP.recall(t["prompt"], k=6)]
        gotE = [f.text for f in memE.recall(t["prompt"], k=6)]
        own = want in gotH
        # 装置修正⑨：**两臂注入条数必须相等**（等长只有在条数相等时才等价于
        # "注入字符数相等"）。H 若因 FTS 精确命中而少送，必须当场暴露。
        # 装置修正⑨d：R1 的正确判据是**两臂等价**，不是"H 送得越多越好"。
        #   要求：①本题那条惯例必须送到（own）；②两臂条数相等；③两臂注入
        #   字符数相等（骨架安慰剂按构造成立，这里再实测一遍）；④E 必须为空。
        h_ok = len(gotH) == len(gotP)
        char_ok = sum(len(x) for x in gotH) == sum(len(x) for x in gotP)
        good = own and h_ok and char_ok and len(gotE) == 0
        ok &= good
        print(f"  {'✅' if good else '❌'} {t['id']:24s} H={len(gotH)}条"
              f"（本条{'✅' if own else '❌'}）P={len(gotP)}条 E={len(gotE)}条"
              f"  注入字符 H={sum(len(x) for x in gotH)}"
              f" P={sum(len(x) for x in gotP)}")
        if not own:
            print(f"      ⚠️ 未取回本条惯例；实际取回：{[x[:24] for x in gotH]}")
    print("R1 预检：" + ("通过" if ok else "❌ 失败（处理未送达，跑臂没有意义）"))

    memH.close(); memP.close(); memE.close()
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

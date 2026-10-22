#!/usr/bin/env python3
"""文档里的计数**单源核对**（AZ47 的落地件）。

## 为什么这个文件存在

仓库里有三处"数字"，它们描述的是**同一次运行**的结果：

    契约条数   tests/contract_test.py 跑出的 PASS=n
    锚层条数   tests/bench_test.py   跑出的 PASS=n
    撤回态条数 bench/withdraw_check.py 的 len(CASES)

这些数字此前被**手抄**进 README / demo/README（第五十五轮实测漂移：文档写 530/105，
实测 535/110；第五十八轮又发现 demo 写 532、README 一处写 520）。手抄不是"偶尔忘改"，
是**结构性**的：数字有 8 个副本，改一处不可能记得住其余七处。

第五十四轮曾把 AZ44 里写死的 `"109 条撤回态"` 改成 `len(withdraw_check.CASES)` 现算 ——
那次只是把它从"手抄"变成"现算"，但**没有管文档里其余的副本**；而且它自己留了个新洞：
`and "535" in _az44_head` 仍然是手抄。

## 为什么它是 runner 的一步，不是一条契约断言

因为被核对的对象**包含"契约总共跑出多少条"**。契约条数 = check() 的调用次数，
把"文档必须写 N"写进断言，断言自己就在 N 里面 ⇒ 要么写 N（于是加一条断言就要改
断言里的数），要么写 N+1（于是它描述的是一个还没发生的计数）。
AZ44 上一版正是这个自指结构的实证：真值涨到 110 之后，**锚自己把漂移锁死了**。

⇒ 唯一能同时看见"文档声明"与"本次实测"的位置在**被测集合之外**：runner。
本文件拿**本次运行**的两个日志去对文档，`run_tests.sh` 步骤 3b 调用它。

## 扫描范围（⛔ 只扫"活文档"）

    README.md        demo/README.md        .github/workflows/*.yml
    quickstart.sh    site/index.html

★ 第六十轮把 `.github/workflows/*.yml` 纳进来了，理由是同一句话：
CI 文件里写着"让『契约 N/0』这句话别人 clone 后能自证"，
而**它自己就是这句声明的第 N 个副本** —— 第五十八轮首次运行时它写着 535/110，
当时实测已是 536/112。让它被自己核对，是这条规则唯一自洽的延伸。

★ 第六十一轮再扩两处，都是"用户真正会看到的字"：
 · `quickstart.sh` —— 它是**唯一会主动打印给用户看**的地方
   （`say "2/4 契约测试（535 条断言…）"`）。文档可以没人读，上手脚本一定会被执行。
   实测：它写着 535，而当时已是 536 —— 扩范围当场抓到（就是本轮的第一条红）。
 · `site/index.html` —— 落地页上的读数条。它比 README 更容易被复制到别处，
   也更容易在改代码时被忘掉。

⛔ **不扫** DESIGN.md 与 bench/evidence/**/PROVENANCE.md：
它们是**历史台账**，每个数字记录的是**当时**的实测值（"第五十五轮：契约 535"）。
按今天的真值去改历史 = 伪造记录 —— 与本文件要防的病正好相反。
（新增台账时怎么写都行：它们不在扫描范围里，也不会被"修正"。）

用法：

    python3 bench/check_counts.py \
        --contract-log /tmp/v1_contract.out --anchor-log /tmp/v1_bench.out

退出码：0 = 全部一致；1 = 有不一致（并打印 file:line 与"声明 vs 实测"）；2 = 用法错误。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# ⚠️ 汇总行必须**锚在整句上**：契约崩掉时，中途某条断言自己会打印 `（PASS=7 FAIL=0）`
#    （第五十四轮实测踩过：裸 `PASS=(\d+)` 抓到的是 H8 那句自述，不是汇总）。
_CONTRACT_SUM = re.compile(r"V1 契约测试汇总\s+PASS=(\d+)\s+FAIL=(\d+)")
_ANCHOR_SUM = re.compile(r"V1 外部锚层契约测试汇总\s+PASS=(\d+)\s+FAIL=(\d+)")

# ── 声明形态 → 它指的是哪个数 ────────────────────────────────────────────
# ⚠️ 顺序即优先级：更具体的形态必须排在更泛的形态前面
#    （"97 条外部锚层断言"要先被 anchor 规则吃掉，否则会被当成契约条数）。
#
# ⛔ 每条规则都必须在**第一版实测**里说自己为什么不会误伤（这一版是第二版，
#    第一版用 `(\d+)\s*条断言` 这种泛规则，在 README 上误伤 5 处）：
#
#    · 泛规则 `(\d+)\s*条断言` 会被这些句子命中，而它们**不是**总数声明：
#        "（6 条断言里稳定挂 2 条）"        —— 说的是某道题内部
#        "15 条入口覆盖 17 条断言"          —— 说的是 AS 组内部的入口覆盖
#        "（4 题 × 6 条断言）"              —— 说的是题集结构
#      ⇒ 改为要求"契约"就在附近（≤6 字），即只在**描述整套契约**的句子里认。
#    · `(\d+)\s*条契约` 会被 "`448 条契约` = 能力更强" 命中 ——
#      那是"⛔ 三个禁止的读法"里的**引用**，记的是历史值。
#      ⇒ 反引号里的数字不算声明（`(?<!`)`）：引用的口径与当前口径不是一回事。
#    · `(\d+)\s*条撤回态` 会被 "（95 条撤回态：AS / AE / AT …）" 命中 ——
#      带冒号的是"某几个组"的**子集清单**。
#      ⇒ 要求冒号前没有紧邻的 `：`（`(?!\s*：)`）。
#
# ⚠️ 这些"不认"的判据都是**结构**上的（引用 / 子集 / 题内），不是"这行我懒得改"。
#    如果哪天真出现总数声明被漏掉，正确做法是补一条**有理由**的形态，
#    而不是把泛规则放回来（那等于让工具去改历史台账）。
_CLAIMS = [
    # 锚层
    (re.compile(r"(\d+)\s*条外部锚层"), "anchor"),
    (re.compile(r"锚层\s*\**\s*(\d+)\s*条"), "anchor"),
    (re.compile(r"bench_test\.py[`*\s]*(\d+)\s*(?:条|/\s*0)"), "anchor"),
    # 撤回态
    (re.compile(r"(\d+)\s*条撤回态(?!\s*：)"), "withdraw"),
    (re.compile(r"撤回态\s*\**\s*(\d+)\s*条"), "withdraw"),
    # 契约（放最后：`契约 535 条` / `535 条契约` / `535 契约` / `535/0` 都要认）
    # ⚠️ 数字前必须不是数字/反引号：`448 条契约` 里能匹配到的 "48 条契约"
    #    会把引用句读成声明（第二版实测踩过，报出"声明 48"这种鬼数）。
    (re.compile(r"契约\s*\**\s*(\d+)\s*(?:条|/\s*0)"), "contract"),
    # ⚠️ 数字前必须不是数字/反引号，也**不能是 `/`**：
    #    quickstart.sh 的步骤标记是 `2/4 契约测试（…）`，`4 契约` 会被读成"声明 4 条"
    #    （第六十一轮扩范围时实测踩到；那是步骤号，不是计数）。
    (re.compile(r"(?<![\d`/])(\d+)\s*条契约"), "contract"),
    (re.compile(r"(?<![\d/])(\d+)\s*契约"), "contract"),
    (re.compile(r"contract_test\.py[`*\s]*(\d+)\s*(?:条|/\s*0)"), "contract"),
    (re.compile(r"契约[^\n]{0,6}?(\d+)\s*条断言"), "contract"),
]

_KIND_CN = {"contract": "契约条数", "anchor": "锚层条数", "withdraw": "撤回态条数"}


def _measured(contract_log: Path, anchor_log: Path) -> dict[str, int]:
    """本次运行的实测真值。⛔ 只读日志，不重跑（重跑就等于换了一次运行）。"""
    if not contract_log.exists():
        raise SystemExit(f"❌ 找不到契约日志 {contract_log}（先跑 tests/contract_test.py）")
    if not anchor_log.exists():
        raise SystemExit(f"❌ 找不到锚层日志 {anchor_log}（先跑 tests/bench_test.py）")

    c_txt = contract_log.read_text(encoding="utf-8", errors="replace")
    a_txt = anchor_log.read_text(encoding="utf-8", errors="replace")
    cm, am = _CONTRACT_SUM.search(c_txt), _ANCHOR_SUM.search(a_txt)
    if not cm:
        raise SystemExit(f"❌ 契约日志里没有汇总行 {contract_log}（测试可能崩在中途）")
    if not am:
        raise SystemExit(f"❌ 锚层日志里没有汇总行 {anchor_log}（测试可能崩在中途）")
    if int(cm.group(2)) or int(am.group(2)):
        raise SystemExit("❌ 本次运行本身就有失败断言（FAIL≠0），先修红再谈计数一致")

    # ⚠️ 撤回态条数**现算**（唯一来源是锚层的 CASES 表）—— 这正是"单源"的意思：
    #    不抄、不缓存、不传参。
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    import withdraw_check  # noqa: PLC0415

    return {
        "contract": int(cm.group(1)),
        "anchor": int(am.group(1)),
        "withdraw": len(withdraw_check.CASES),
    }


def _scan(path: Path) -> list[tuple[int, str, int, str]]:
    """扫一份文档，返回 [(行号, 原行, 声明值, 类别)]。

    ⚠️ 同一行可能同时含多个计数（"契约 535 条 + 锚层 97 条"）⇒ 按**匹配区间**去重，
       不能按行去重，也不能只看第一个匹配。
    """
    out: list[tuple[int, str, int, str]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        taken: list[tuple[int, int]] = []
        for rx, kind in _CLAIMS:
            for m in rx.finditer(line):
                s, e = m.span(1)
                if any(s < e2 and s2 < e for s2, e2 in taken):
                    continue        # 已被更具体的规则吃掉
                taken.append((s, e))
                out.append((lineno, line, int(m.group(1)), kind))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="文档计数单源核对（AZ47）")
    ap.add_argument("--contract-log", default="/tmp/v1_contract.out")
    ap.add_argument("--anchor-log", default="/tmp/v1_bench.out")
    ap.add_argument("--doc", action="append", default=None,
                    help="要扫的文档（可重复）；默认扫活文档 README.md / demo/README.md / "
                         ".github/workflows/*.yml / quickstart.sh / site/index.html")
    args = ap.parse_args(argv)

    truth = _measured(Path(args.contract_log), Path(args.anchor_log))

    docs = ([Path(p) for p in args.doc] if args.doc
            else [_ROOT / "README.md", _ROOT / "demo" / "README.md"]
            + sorted((_ROOT / ".github" / "workflows").glob("*.yml"))
            + [_ROOT / "quickstart.sh", _ROOT / "site" / "index.html"])

    print("=" * 74)
    print("文档计数单源核对（AZ47）：文档声明必须等于**本次运行**的实测值")
    print("=" * 74)
    print(f"  实测真值：契约 {truth['contract']} 条 / 锚层 {truth['anchor']} 条 / "
          f"撤回态 {truth['withdraw']} 条")
    print(f"  扫描范围：{' , '.join(str(Path(os.path.relpath(d, _ROOT))) for d in docs)}")
    print("  ⛔ 不扫 DESIGN.md 与 bench/evidence/*/PROVENANCE.md：它们是历史台账，"
          "记的是当时的值。")
    print()

    bad = 0
    seen = 0
    for doc in docs:
        if not doc.exists():
            print(f"  ⚠️ 跳过不存在的文档 {doc}")
            continue
        rows = _scan(doc)
        print(f"  ── {os.path.relpath(doc, _ROOT)} —— 命中 {len(rows)} 处声明 ──")
        for lineno, line, claimed, kind in rows:
            seen += 1
            ok = claimed == truth[kind]
            if not ok:
                bad += 1
            mark = "✅" if ok else "❌"
            print(f"  {mark} :{lineno:<5d} {_KIND_CN[kind]} 声明 {claimed}"
                  f"{'' if ok else f'  ≠ 实测 {truth[kind]}'}")
            if not ok:
                print(f"           {line.strip()[:110]}")
        if not rows:
            print("     （无）")
        print()

    if bad:
        print(f"❌ {bad} 处声明与实测不一致（共核对 {seen} 处）。"
              f"改文档，不许改核对器 —— 数字的唯一来源是本次运行。")
        return 1
    print(f"✅ 全部一致（核对 {seen} 处声明，0 处漂移）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

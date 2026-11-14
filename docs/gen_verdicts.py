#!/usr/bin/env python3
"""从 DESIGN.md 抽出所有「轮次」标题，生成 `docs/VERDICTS.md` 判决索引。

为什么是标题而不是正文：DESIGN 里每个轮次标题本身就是作者浓缩过的判定
（「× 被否证」「× 不成立」「× 第一次通电」）。索引的目的是让人一页扫完
172 轮都判了什么，正文按需展开。

⛔ 不改 DESIGN、不猜结论，只搬运标题原文。
"""
from __future__ import annotations

import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "DESIGN.md"
OUT = ROOT / "docs" / "VERDICTS.md"

#: 认「轮次标题」的两种写法：`## §160 V91…` / `## 141. 第一百五十三轮（V73）…`
RE_SECTION = re.compile(r"^##\s+(§?[\d.]+)[.、]?\s+(.*)$")
RE_ROUND = re.compile(r"第[一二三四五六七八九十百千零]+轮|V\d+")

#: 判词色彩（只做视觉分档，不改文字）。NEG 先判，避免「未命中」被「命中」吃掉。
NEG = ("判负", "未命中", "不成立", "否证", "判死", "零净", "不可部署", "修不了",
       "不是剂量", "既不充分", "没有稳定符号", "被推翻", "假的", "缺口", "断在", "走不通")
POS = ("通电", "命中", "成立", "第一次", "接上", "复现", "有效", "改善", "提升", "做到了", "真的")


def mark(title: str) -> str:
    """给标题打一个粗粒度标记，方便扫。"""
    if any(k in title for k in NEG):
        return "✗"
    if any(k in title for k in POS):
        return "✓"
    return "·"


def main() -> None:
    lines = SRC.read_text(encoding="utf-8").splitlines()
    rows: list[tuple[int, str, str, str]] = []
    for i, ln in enumerate(lines, 1):
        if not ln.startswith("## "):
            continue
        m = RE_SECTION.match(ln)
        if not m:
            continue
        sec, title = m.group(1), m.group(2).strip()
        if not RE_ROUND.search(title):
            continue
        rows.append((i, sec, title, mark(title)))

    out = [
        "# V1 判决索引（172 轮）",
        "",
        "> 自动生成自 `DESIGN.md` 的轮次标题 —— **正文的浓缩就是标题本身**。",
        "> 标记：`✗` 判负/未命中　`✓` 通电/命中　`·` 普查或中性",
        "> 生成脚本：`docs/gen_verdicts.py`。⛔ 手改本文无效，重跑会覆盖。",
        "",
        "## 统计",
        "",
    ]
    n_neg = sum(1 for r in rows if r[3] == "✗")
    n_pos = sum(1 for r in rows if r[3] == "✓")
    n_mid = len(rows) - n_neg - n_pos
    out += [
        f"| 档 | 轮数 |",
        f"|---|---|",
        f"| `✗` 判负 / 未命中 / 被否证 | **{n_neg}** |",
        f"| `✓` 通电 / 命中 / 第一次做到 | **{n_pos}** |",
        f"| `·` 普查 / 中性 | {n_mid} |",
        f"| 合计 | {len(rows)} |",
        "",
        "⚠️ 这个分档只看标题用词，是**索引**不是判决。",
        "**标题中性 ≠ 结论中性**：99 个 `·` 里绝大多数结尾是负的（「普查后发现桥不在」这类），",
        "只是标题没写判负的动词。真正的判决看 `ASSESSMENT.md` §三：`✓` 的轮次里，",
        "凡是用到「知识」的，知识全部是人写的。",
        "",
        "## 全表",
        "",
        "| # | 行 | 轮次 | 判定 |",
        "|---|---|---|---|",
    ]
    for i, (ln_no, sec, title, mk) in enumerate(rows, 1):
        t = title.replace("|", "\\|")
        out.append(f"| {i} | {ln_no} | {mk} {sec} {t} |")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"写出 {OUT.relative_to(ROOT)}：{len(rows)} 轮（✗ {n_neg} / ✓ {n_pos} / · {n_mid}）")


if __name__ == "__main__":
    main()

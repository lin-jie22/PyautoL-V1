#!/usr/bin/env python3
"""落地页结构自检（第六十一轮）：把"我看过一眼，没问题"换成可重跑的读数。

⛔ 这不是 AZ49 的替代品：AZ49 锚的是**承诺**（零外部请求 / 边界块在），
   本文件查的是**结构**（标签闭合、单文件自足、明暗主题、可访问性最小项）。
   两者都必要：承诺对了但 HTML 是坏的，页面照样打不开。

用法：

    python3 bench/evidence/knowledge_s64/check_page.py

退出码：0 = 全部通过；1 = 有硬伤。
"""

from __future__ import annotations

import pathlib
import re
import sys
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[3]
PAGE = ROOT / "site" / "index.html"

#: 这些标签没有闭合标签（HTML5 的空元素）。
VOID = {"meta", "link", "br", "hr", "img", "input", "source",
        "area", "base", "col", "embed", "param", "track", "wbr"}


class Balance(HTMLParser):
    """栈式闭合检查：既查"没闭合"，也查"闭错了"（`<b><i></b>` 这种）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, tuple[int, int]]] = []
        self.err: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if not self.stack:
            self.err.append(f"多余的 </{tag}> @ {self.getpos()}")
            return
        if self.stack[-1][0] != tag:
            top, pos = self.stack[-1]
            self.err.append(f"闭合错配：</{tag}> @ {self.getpos()}，栈顶是 <{top}> @ {pos}")
            return
        self.stack.pop()


def main() -> int:
    if not PAGE.exists():
        print(f"❌ 找不到 {PAGE}")
        return 1
    src = PAGE.read_text(encoding="utf-8")

    p = Balance()
    p.feed(src)
    unclosed = [t for t, _ in p.stack]

    checks = [
        ("文件存在且非空", bool(src), f"{len(src)} 字符 / {src.count(chr(10)) + 1} 行"),
        ("标签闭合无错", not p.err, p.err[:2] or "无"),
        ("无未闭合标签", not unclosed, unclosed or "无"),
        ("有 <title>", bool(re.search(r"<title>.+?</title>", src, re.S)), "—"),
        ("有 lang=zh-CN", 'lang="zh-CN"' in src, "—"),
        ("有 viewport（移动端可读）", 'name="viewport"' in src, "—"),
        ("跟随系统明暗主题", "prefers-color-scheme" in src, "—"),
        ("有边界块（不是 AGI）", "先读这一条：V1 不是 AGI" in src, "—"),
        ("单文件自足（零外部请求）",
         not any(re.search(rx, src, re.I) for rx in (
             r"<script[^>]*\bsrc\s*=", r"<link\b[^>]*\bhref\s*=", r"<img\b",
             r"<iframe\b", r"\bsrcset\s*=", r"@import\b")),
         "—"),
    ]

    print("=" * 74)
    print("落地页结构自检（site/index.html）")
    print("=" * 74)
    bad = 0
    for name, ok, detail in checks:
        if not ok:
            bad += 1
        print(f"  {'✅' if ok else '❌'} {name:26s} {detail}")
    print()
    if bad:
        print(f"❌ {bad} 项不通过")
        return 1
    print("✅ 全部通过（结构层面；页面上的读数由 AZ47 核对，承诺由 AZ49 断言）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

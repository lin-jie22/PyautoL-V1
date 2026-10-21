#!/usr/bin/env python3
"""★7x 臂补丁：只改 `mw/core.py` 一处（+ 一个常量 + 两个小函数）。

臂间差异**只有一个字符串常量** `_MEM_FOCUS`：
    "off"    → 主分支原样（R1 臂也用它，R1 的差异全在 env `V1_RECALL_K=1`）
    "bigram" → FTS 取 6 条后按 CJK 二元组重叠重排，只留第 1 条（R2）
    "none"   → 整块 [语义记忆] 不入 prompt（M0）

⛔ 不用 `re`（core.py 没 import re，加 import 会把 diff 扩大到模块头）。
⛔ 不用 `while`（内核「只准有一个 while」是 AST 钉死的不变量 J6）。
"""
import pathlib
import sys

ANCHOR_DEF = "        recalled = self.memory.recall(goal, k=self.s.limits.recall_k)\n"

HELPER = '''

# ---------------------------------------------------------------------------
# ★7x 臂开关 —— **只存在于测量 worktree**，主分支恒 "off"。
#   实测动机：题面里那句"按记忆里记着的（ZK1）（ZK2）（ZK3）（ZK4）惯例处理"
#   把四个键全点了 ⇒ 四个键 token 对四道题的贡献完全相同 ⇒ bm25 的基础分
#   退化成"谁的正文最短谁第一"（ZK1 恒第一）。给对一条和给错一条，
#   模型在上下文里无从分辨。
_MEM_FOCUS = "off"


def _cjk_bigrams(text: str) -> set[str]:
    """CJK 连串的二元组集合。非 CJK 字符（含标点/ASCII）是切分点。

    ⚠️ 这是**词法近似**，不是语义。g7e1 上 ZK3 只以 6:5 赢 ZK1 就是它的脆弱性证据
    （"大小写不敏感"里的「小写」与 ZK1 的「转小写」共享二元组）。
    """
    out: set[str] = set()
    run = ""
    for ch in text or "":
        if "\\u4e00" <= ch <= "\\u9fff":
            run += ch
            continue
        out.update(run[i:i + 2] for i in range(len(run) - 1))
        run = ""
    out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _focus_recalled(recalled, goal):
    """按 CJK 二元组重叠重排，只留第 1 条。并列时**保持 FTS 原序**（不许自己发明次序）。

    零重叠 ⇒ 返回 `[]`：与 `recall` 的零命中语义一致（宁可说"没有相关的"，
    也不给一条与题面毫无字面交集的惯例句）。
    """
    if not recalled:
        return []
    q = _cjk_bigrams(goal)
    if not q:
        return list(recalled)[:1]
    best, best_n = [], -1
    for f in recalled:
        n = len(q & _cjk_bigrams(getattr(f, "text", "") or ""))
        if n > best_n:
            best, best_n = [f], n
    if best_n <= 0:
        return []
    return best[:1]
'''


def patch(path: pathlib.Path, focus: str) -> None:
    src = path.read_text(encoding="utf-8")
    if src.count(ANCHOR_DEF) != 1:
        sys.exit(f"!! 锚点出现 {src.count(ANCHOR_DEF)} 次（必须 1）：{path}")
    if "_MEM_FOCUS" in src:
        sys.exit(f"!! 已经被打过补丁：{path}")

    # ① 常量 + 两个函数：贴在模块级 import 段之后（找第一处 "@dataclass" 之前）
    marker = "\n@dataclass"
    if src.count(marker) < 1:
        sys.exit("!! 找不到 @dataclass 锚点，无法安置常量")
    i = src.index(marker)
    src = src[:i] + HELPERS.replace('_MEM_FOCUS = "off"', f'_MEM_FOCUS = "{focus}"') + src[i:]

    # ② 调用点
    new = (
        "        recalled = self.memory.recall(goal, k=self.s.limits.recall_k)\n"
        '        if _MEM_FOCUS == "none":\n'
        "            recalled = []\n"
        '        elif _MEM_FOCUS == "bigram":\n'
        "            recalled = _focus_recalled(recalled, goal)\n"
    )
    src = src.replace(ANCHOR_DEF, new)
    path.write_text(src, encoding="utf-8")
    print(f"  patched {path}  focus={focus!r}")


HELPERS = HELPER

if __name__ == "__main__":
    patch(pathlib.Path("/tmp/v1_7x_wt_r2/mw/core.py"), "bigram")
    patch(pathlib.Path("/tmp/v1_7x_wt_m0/mw/core.py"), "none")
    print("R1 不补丁（差异全在 env V1_RECALL_K=1）")

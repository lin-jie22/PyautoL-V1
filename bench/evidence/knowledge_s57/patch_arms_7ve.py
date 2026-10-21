#!/usr/bin/env python3
"""★7v-e 装置：最小对 —— N1/N2 两句仅第 7 字不同（`条`/`项`），各 31 字。

⛔ 只注入**一个字面量拼接行**，位置与 A 完全一致（state 块之后、收尾句之前）。
   N1 与 N2 的差别必须是**且只是**那一个字；任何其他差异都会让"词形 vs 框架"
   的分辨重新变成两个变量。
"""
import sys

BASE = (
    '        if state:\n'
    '            prompt += f"当前状态：\\n{state[:cfg.STATE_CHAR_CAP]}\\n"\n'
)

N1 = "上方状态请逐条对照执行，每一条都按原文原样落地，不要自行改写。"
N2 = "上方状态请逐项对照执行，每一条都按原文原样落地，不要自行改写。"

ARMS = {
    "/tmp/v1_7ve_wt_n1/mw/planner.py": N1,
    "/tmp/v1_7ve_wt_n2/mw/planner.py": N2,
}

# ── 跑前硬断言：长度相等 + 只有一个字不同 + 差异位是第 7 字 ──────────
assert len(N1) == 31 and len(N2) == 31, ("长度", len(N1), len(N2))
diff = [i for i, (a, b) in enumerate(zip(N1, N2)) if a != b]
assert len(diff) == 1, ("差异位数量", diff)
assert diff[0] == 6, ("差异位应为第 7 字(索引6)", diff)
assert (N1[6], N2[6]) == ("条", "项"), ("差异字", N1[6], N2[6])
print(f"串长 N1={len(N1)} N2={len(N2)} 唯一差异位=索引{diff[0]}（{N1[6]} vs {N2[6]}）")

for path, s in ARMS.items():
    src = open(path, encoding="utf-8").read()
    assert len(s) == 31, (path, "串长", len(s))
    ins = '            prompt += "%s" + "\\n"\n' % s
    if ins in src:                     # 幂等：重复执行不重复注入
        print("already patched", path)
        continue
    n = src.count(BASE)
    assert n == 1, (path, "锚点计数", n)
    open(path, "w", encoding="utf-8").write(src.replace(BASE, BASE + ins))
    print("patched", path)

# ── 形态核对：只认代码行形态，不认裸串（注释里出现同一句不算）─────────
print("\n--- 形态核对（answer() 体内）---")
forms = {
    "A(主分支/正对照)": ("/Users/linshaojie/V1/mw/planner.py", None, "const"),
    "B(基线)": ("/tmp/v1_7ve_wt_b/mw/planner.py", None, "none"),
    "N1(逐条)": ("/tmp/v1_7ve_wt_n1/mw/planner.py", N1, "literal"),
    "N2(逐项)": ("/tmp/v1_7ve_wt_n2/mw/planner.py", N2, "literal"),
}
A_STR = "上方状态按原文顺序依次阅读，逐条都保持其原有排列顺序与文字内容不变。"
ok_all = True
for tag, (path, want, mode) in forms.items():
    src = open(path, encoding="utf-8").read()
    i = src.find("def answer(self, goal")
    j = src.find("return self.llm.complete", i)
    body = src[i:j]                    # 只取 answer() 体
    np = body.count("prompt +=")
    has_n1 = ('prompt += "%s"' % N1) in body
    has_n2 = ('prompt += "%s"' % N2) in body
    if mode == "const":
        ok = ("prompt += _ANSWER_FIDELITY" in body
              and ('_ANSWER_FIDELITY = "%s"' % A_STR) in src
              and not has_n1 and not has_n2 and np == 3)
        detail = f"常量拼接=True 定义=True 拼接行={np}"
    elif mode == "literal":
        mine, other = (has_n1, has_n2) if want == N1 else (has_n2, has_n1)
        ok = (mine and not other and "_ANSWER_FIDELITY" not in src and np == 3)
        detail = f"自己那句={mine} 对方那句={other} 拼接行={np}"
    else:
        ok = (not has_n1 and not has_n2 and "_ANSWER_FIDELITY" not in src and np == 2)
        detail = f"N1={has_n1} N2={has_n2} 拼接行={np}（基线应为 2）"
    ok_all &= ok
    print(f"{tag:18s} {'OK ' if ok else 'BAD'} {detail}")
print("装置形态:", "全绿" if ok_all else "有 BAD")
sys.exit(0 if ok_all else 1)

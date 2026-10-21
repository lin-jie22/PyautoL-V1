#!/usr/bin/env python3
"""★7v-d 装置：给 S1/S2/S3 三个 worktree 各注入一句（34 字），位置与 A 完全一致。

⛔ 只注入**一个字面量拼接行**，不引入常量、不动注释 —— 三臂与 A 的唯一差别
   必须是那 34 个字本身。
"""
import sys

BASE = (
    '        if state:\n'
    '            prompt += f"当前状态：\\n{state[:cfg.STATE_CHAR_CAP]}\\n"\n'
)

ARMS = {
    "/tmp/v1_7vd_wt_s1/mw/planner.py":
        "上方状态里的每一条内容都彼此独立存在，一条一条分别生效，且互不覆盖。",
    "/tmp/v1_7vd_wt_s2/mw/planner.py":
        "上方状态里的内容逐条排列，逐条只是排版上的安排，不构成任何约束要求。",
    "/tmp/v1_7vd_wt_s3/mw/planner.py":
        "逐条" * 16 + "的。",
}

A_STR = "上方状态按原文顺序依次阅读，逐条都保持其原有排列顺序与文字内容不变。"

for path, s in ARMS.items():
    src = open(path, encoding="utf-8").read()
    assert len(s) == 34, (path, "串长", len(s))
    ins = '            prompt += "%s" + "\\n"\n' % s
    if ins in src:                     # 幂等：重复执行不重复注入
        print("already patched", path, "len", len(s))
        continue
    n = src.count(BASE)
    assert n == 1, (path, "锚点计数", n)
    open(path, "w", encoding="utf-8").write(src.replace(BASE, BASE + ins))
    print("patched", path, "len", len(s))

# ── 装置形态核对：四处理臂各只有一句、B 一句都没有 ────────────────
# ⛔ A 的串在**常量**里（`prompt += _ANSWER_FIDELITY`），S 臂是行内字面量
#    ⇒ 判定必须分开写：A 查"常量拼接 + 常量定义在文件内"，S 查"字面量在体内"。
print("\n--- 形态核对 ---")
forms = {
    "A(主分支)": ("/Users/linshaojie/V1/mw/planner.py", A_STR, "const"),
    "B(基线)": ("/tmp/v1_7vd_wt_b/mw/planner.py", None, "none"),
    "S1(同义/无逐条)": ("/tmp/v1_7vd_wt_s1/mw/planner.py",
                    ARMS["/tmp/v1_7vd_wt_s1/mw/planner.py"], "literal"),
    "S2(逐条/否定)": ("/tmp/v1_7vd_wt_s2/mw/planner.py",
                   ARMS["/tmp/v1_7vd_wt_s2/mw/planner.py"], "literal"),
    "S3(逐条×16)": ("/tmp/v1_7vd_wt_s3/mw/planner.py",
                  ARMS["/tmp/v1_7vd_wt_s3/mw/planner.py"], "literal"),
}
ALL = list(ARMS.values())          # 三个 S 串（不含 A，A 走常量）
ok_all = True
for tag, (path, want, mode) in forms.items():
    src = open(path, encoding="utf-8").read()
    i = src.find("def answer(self, goal")
    j = src.find("return self.llm.complete", i)
    body = src[i:j]                # 只取 answer() 体，不越界到下一个方法
    hits = [x for x in ALL if x in body]
    np = body.count("prompt +=")   # B=2（状态块 + 收尾句）/ 处理臂=3
    if mode == "const":
        ok = ("prompt += _ANSWER_FIDELITY" in body
              and ('_ANSWER_FIDELITY = "%s"' % want) in src
              and not hits and np == 3)
        detail = f"常量拼接=True 定义=True 拼接行={np}"
    elif mode == "literal":
        ok = (len(hits) == 1 and hits[0] == want
              and "_ANSWER_FIDELITY" not in src and np == 3)
        detail = f"体内命中={len(hits)} 长度={len(hits[0]) if hits else 0} 拼接行={np}"
    else:
        ok = (len(hits) == 0 and "_ANSWER_FIDELITY" not in src and np == 2)
        detail = f"体内命中={len(hits)} 拼接行={np}（基线应为 2）"
    ok_all &= ok
    print(f"{tag:18s} {'OK ' if ok else 'BAD'} {detail}")
print("装置形态:", "全绿" if ok_all else "有 BAD")
sys.exit(0 if ok_all else 1)

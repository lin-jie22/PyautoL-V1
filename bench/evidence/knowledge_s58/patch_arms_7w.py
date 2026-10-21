#!/usr/bin/env python3
"""★7w 装置：补救通道闭路反馈 —— **四臂同一份代码，只差一个常量**。

模式：none（B0 盲发）/ fail（W1 带 Verdict 失败行）/ answer（W2 带上一次答案）/ both（W3）

⛔ 为什么四臂共用一份实现、只改常量：
   7v-c/d/e 的教训是"臂与臂之间除了处理变量之外，任何一处漂移都会污染 Δ"。
   用同一份代码 + 一个常量，把"装置差异"压缩到**一行字符串**，可以 diff 直接核对。

⛔ 为什么钩子在 core._verify：失败原文（`Verdict.report`）与被打回的答案（`result.output`）
   只有 core 手里有；planner 只能在"被判失败之后"的下一轮补救里用它们。
   钩子必须在**判失败那一刻**记，否则记到的是别的题的。
"""
import sys

ARMS = {
    "/tmp/v1_7w_wt_b0": "none",
    "/tmp/v1_7w_wt_w1": "fail",
    "/tmp/v1_7w_wt_w2": "answer",
    "/tmp/v1_7w_wt_w3": "both",
}

CONST = '''#: ★7w 臂标记：补救反馈形态（"none" 盲发 / "fail" 带失败行 / "answer" 带上一次答案 / "both"）
#: ⛔ 四臂代码完全相同，**只**这个常量不同 —— diff 可核。
_REMEDY_FEEDBACK = "%s"


'''

FIELDS = '''        # ★7w：补救反馈的两件原料（由 core._verify 在判失败那一刻写入）
        self.remedy_n = 0
        self._last_fail = ""
        self._last_answer = ""
'''

METHODS = '''    # ------------------------------------------------------------------ ★7w
    def note_verdict(self, report: str, answer: str) -> None:
        """记下"上一次交付被判失败"的两件东西 —— 只给补救反馈用。

        ⛔ 截断是必须的：`Verdict.report` 可能带整段断言文本，原样拼进 prompt
           会把 state 挤出去（`cfg.STATE_CHAR_CAP` 之外的东西没有搬运层）。
        """
        self._last_fail = (report or "").strip()[:300]
        self._last_answer = (answer or "").strip()[:600]

    def _feedback_line(self) -> str:
        """第 2 次及以后的补救才带反馈（第 1 次没有"上一次"）。"""
        if self.remedy_n < 2:
            return ""
        out = []
        if _REMEDY_FEEDBACK in ("fail", "both") and self._last_fail:
            out.append(f"上一次答案未通过：{self._last_fail}\\n")
        if _REMEDY_FEEDBACK in ("answer", "both") and self._last_answer:
            out.append(f"上一次的答案（已被否决）：\\n{self._last_answer}\\n")
        return "".join(out)

'''

ANSWER_BASE = ('        prompt = f"目标：{goal}\\n"\n'
               '        if state:\n'
               '            prompt += f"当前状态：\\n{state[:cfg.STATE_CHAR_CAP]}\\n"\n')
ANSWER_NEW = ('        self.remedy_n += 1\n'
              '        prompt = f"目标：{goal}\\n"\n'
              '        if state:\n'
              '            prompt += f"当前状态：\\n{state[:cfg.STATE_CHAR_CAP]}\\n"\n'
              '        _fb = self._feedback_line()\n'
              '        if _fb:\n'
              '            prompt += _fb\n')

CORE_OLD = '        return ToolResult(False, error=f"自检未通过：{v.report}")\n'
CORE_NEW = ('        self.planner.note_verdict(v.report, result.output)\n'
            '        return ToolResult(False, error=f"自检未通过：{v.report}")\n')

ok_all = True
for d, mode in ARMS.items():
    p = f"{d}/mw/planner.py"
    src = open(p, encoding="utf-8").read()
    if "_REMEDY_FEEDBACK" in src:
        print(f"{mode:7s} already patched")
    else:
        assert src.count("class Planner:") == 1
        src = src.replace("class Planner:", CONST % mode + "class Planner:", 1)
        assert src.count("self.last_bad_tools: list[str] = []") == 1
        src = src.replace("        self.last_bad_tools: list[str] = []\n",
                          "        self.last_bad_tools: list[str] = []\n" + FIELDS, 1)
        assert src.count("    def replan(") == 1
        src = src.replace("    def replan(", METHODS + "    def replan(", 1)
        n = src.count(ANSWER_BASE)
        assert n == 1, ("answer() 锚点", d, n)
        src = src.replace(ANSWER_BASE, ANSWER_NEW, 1)
        open(p, "w", encoding="utf-8").write(src)

    c = f"{d}/mw/core.py"
    csrc = open(c, encoding="utf-8").read()
    if "note_verdict" not in csrc:
        assert csrc.count(CORE_OLD) == 1, ("core 锚点", d)
        open(c, "w", encoding="utf-8").write(csrc.replace(CORE_OLD, CORE_NEW, 1))
    print(f"{mode:7s} patched {d}")

# ── 形态核对：常量值 / 钩子在场 / answer() 里只有一处拼接反馈 ──────────
print("\n--- 形态核对 ---")
for d, mode in ARMS.items():
    src = open(f"{d}/mw/planner.py", encoding="utf-8").read()
    csrc = open(f"{d}/mw/core.py", encoding="utf-8").read()
    got = [l for l in src.splitlines() if l.startswith("_REMEDY_FEEDBACK = ")]
    i = src.find("def answer(self, goal")
    j = src.find("return self.llm.complete", i)
    body = src[i:j]
    ok = (len(got) == 1 and got[0] == f'_REMEDY_FEEDBACK = "{mode}"'
          and "self.remedy_n += 1" in body and "_fb = self._feedback_line()" in body
          and "note_verdict" in csrc and "_ANSWER_FIDELITY" not in src)
    ok_all &= ok
    print(f"  {mode:7s} {'OK ' if ok else 'BAD'} 常量={got} 计数自增={'self.remedy_n += 1' in body} "
          f"反馈拼接={'_fb = self._feedback_line()' in body} core钩子={'note_verdict' in csrc}")

print("装置形态:", "全绿" if ok_all else "有 BAD")
sys.exit(0 if ok_all else 1)

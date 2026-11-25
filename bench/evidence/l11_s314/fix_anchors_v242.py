#!/usr/bin/env python3
"""V242（§314）：修补 22 条**腐烂的撤回锚点**。

## 为什么要写脚本而不是手改

`CASES` 是 375 条元组字面量，手改容易漏、也容易改到隔壁条目。这个脚本：
1. 按**名字**定位元组块（`("NAME", "file",` 开头，到 `),` 结尾）；
2. 整块替换成新的 `(名字, 文件, old, new)`；
3. **强制校验**：替换后每条 `old` 在目标文件里必须**恰好出现 1 次**（这正是撤回态的定义）；
4. 只重排这 22 条会破坏顺序 ⇒ 原地替换，注释与其余 353 条一字不动。

## 改的是什么（不是改判据，是改锚点）

这 22 条的共同病：源码演进了，`old_string` 已经不在了 ⇒ 撤回请求「出现 0 次」
⇒ 基线照绿 ⇒ **规则静默失效**（V240 §312.6 的 18+4 条）。
本轮只把锚点重新对准**当前**代码里承担同一职责的那几行，判据一个字不改。
"""
from __future__ import annotations

import pathlib
import re
import sys

V1 = pathlib.Path(__file__).resolve().parents[3]
W = V1 / "bench" / "withdraw_check.py"

#: 名字 → (文件, 新 old, 新 new)。`None` 表示沿用当前条目里的文件字段。
OVR: dict[str, tuple[str, str, str]] = {
    # ── ENV_KEYS：把 V1_EXTERNAL_ANSWER_TOOL 从登记表里摘掉（AE2 要它进签名） ──
    "AE2": ("mw/config.py",
            '    "V1_REWARD_ECHO",\n    "V1_EXTERNAL_ANSWER_TOOL",\n    "V1_ALLOW_NOTE",',
            '    "V1_REWARD_ECHO",\n    "V1_ALLOW_NOTE",'),
    # ── ZERO_OK_KEYS：摘掉 V1_VERIFY_RETRIES（AS12 要它允许 0） ──
    #     ⚠️ ENV_KEYS 里有逐字相同的三行 ⇒ 必须带上后面那句 ZERO_OK 独有的注释才唯一。
    "AS12": ("mw/config.py",
             '    "V1_VERIFY_RETRIES",\n    "V1_VERIFY_STATIC",\n'
             '    # ★ 第一百四十七轮（§127）：`0` = 不派示例判据',
             '    "V1_VERIFY_STATIC",\n'
             '    # ★ 第一百四十七轮（§127）：`0` = 不派示例判据'),
    # ── AS3：让 derive() 重新能收 answer（自评分入口） ──
    "AS3": ("mw/verify.py",
            "def derive(goal: str, static: int = 0, exec_: int = 0) -> tuple[Check, ...]:",
            'def derive(goal: str, static: int = 0, exec_: int = 0, answer: str = "")'
            " -> tuple[Check, ...]:"),
    # ── AS7：闸恒通过（`if v.ok` ⇒ `if True`），打回机制失效 ──
    "AS7": ("mw/core.py",
            '        if v.ok:\n            self.verify_stats["passed"] += 1',
            '        if True:\n            self.verify_stats["passed"] += 1'),
    # ── AS11：verify_retries=0 也照样建判据 ⇒ 关不掉 ──
    "AS11": ("mw/core.py",
             "            vfy.derive(goal, int(self.s.limits.verify_static),\n"
             "                       int(self.s.limits.verify_exec))\n"
             "            if int(self.s.limits.verify_retries) > 0 else ())",
             "            vfy.derive(goal, int(self.s.limits.verify_static),\n"
             "                       int(self.s.limits.verify_exec))\n"
             "            if True else ())"),
    # ── AX4：Limits → WorldModel 的 allow_learn 透传线被剪断 ──
    "AX4": ("mw/core.py",
            "                             allow_learn=self.s.limits.allow_learn,",
            "                             allow_learn=True,"),
    # ── AZ90：verify_static 不再进 derive（档位恒 0） ──
    "AZ90": ("mw/core.py",
             "            vfy.derive(goal, int(self.s.limits.verify_static),",
             "            vfy.derive(goal, 0,"),
    # ── AZ129c：基线缺身份证也放行 ──
    "AZ129c": ("mw/bench.py",
               "    if not ok:\n        return False, why\n    if not store_run_id:",
               "    if not ok:\n        return False, why\n    if False and not store_run_id:"),
    # ── AZ130e：MISMATCH 也被计进状态分布（坏行冒充好行） ──
    "AZ130e": ("mw/bench.py",
               '        out["rows"] += 1\n        out[audit_measurement(rec)[0]] += 1',
               '        out["rows"] += 1\n        _st = audit_measurement(rec)[0]\n'
               '        if _st != "MISMATCH":\n            out[_st] += 1'),
    # ── AZ135b：停滞原因恒报 STUCK_BEST（`STUCK_MEANS` 再也出现不了） ──
    #     ⚠️ V242 首版把 `if stuck_means else` 整段删掉 ⇒ `render_escalations` 走进
    #       STUCK_BEST 分支去格式化 `best=None` ⇒ **TypeError、无汇总行**（撤回后崩溃）。
    #       撤回要证明的是「这个字段不是恒真」，所以改撤**它数的那个数**：
    #       `n_fail + 1` ⇒ 断言里的 `n_fail == 3` 变假，而 reason 仍是 STUCK_MEANS ⇒ 不崩。
    "AZ135b": ("mw/goals.py",
               '"kind": kind, "n_fail": len(tail),',
               '"kind": kind, "n_fail": len(tail) + 1,'),
    # ── AZ137a：缺 impl 的行也进池（`not impl` 的闸被拆掉） ──
    #     ⚠️ 同一段在 scoreinfo 里出现两次（另一处是 `counts["n_no_impl"]`）⇒ 带下一行才唯一。
    "AZ137a": ("mw/scoreinfo.py",
               "        impl = r.get(impl_key)\n        if not impl or score_key not in r:\n"
               '            n_no += 1',
               "        impl = r.get(impl_key)\n        if score_key not in r:  # 撤回态\n"
               '            n_no += 1'),
    # ── AZ137c：movability 恒 None（可动性读数消失） ──
    "AZ137c": ("mw/goals.py",
               '        m = _SI.movability(obs, cell=c) if obs else None\n'
               '        e["movability"] = (m if m and m.get("label") != _SI.KIND_UNKNOWN else None)',
               '        e["movability"] = None  # 撤回态'),
    # ── AZ137e：claim 一出现就跳过（换路不再发生） ──
    "AZ137e": ("mw/goals.py",
               '        elif st["best_score"] < target:\n            if c in esc_set:',
               '        elif st["best_score"] < target:\n'
               '            if c in esc_set or _SI.movability(obs, cell=c).get("claim"):  # 撤回态'),
    # ── AZ145d：输入池退化 ⇒ 「超长/不超长」两头只剩一头 ──
    #     ⚠️ V242 首版撤「单参数包 1-tuple」⇒ 下游 `for x, in INPUTS` 解包抛错 ⇒
    #       **无汇总行 + 崩溃**。撤回态必须让**判据**变假，不许让整个测试挂掉。
    #       改成撤 TAIL 的「两头」：`LEN_POOL[2]`(9) → `LEN_POOL[1]`(3) ⇒
    #       断言 ① `tail[1] == ("abcdef", 9)` 变假，而输入形状不变 ⇒ 不崩。
    "AZ145d": ("mw/critgen.py",
               "            return [(p[0], LEN_POOL[0]), (p[0], LEN_POOL[2]),\n"
               "                    (p[2], LEN_POOL[1]), (p[1], LEN_POOL[0])]",
               "            return [(p[0], LEN_POOL[0]), (p[0], LEN_POOL[1]),\n"
               "                    (p[2], LEN_POOL[1]), (p[1], LEN_POOL[0])]"),
    # ── AZ149c：catalogue() 忽略 n_args（永远全量 6 行） ──
    "AZ149c": ("mw/critgen.py",
               "    src = allow if allow is not None else (OPS if n_args is None else ops_for(n_args))",
               "    src = allow if allow is not None else OPS  # 撤回态：忽略 n_args"),
    # ── AZ21：per_task 的 elapsed_s 恒 0（读数消失，且 `>0` 判据变假） ──
    "AZ21": ("mw/bench.py",
             '                    "elapsed_s": round(time.time() - _t_task, 2)}',
             '                    "elapsed_s": 0}'),
    # ── AZ22：零命中不许静默给全表 ⇒ 撤回 = **把第三级兜底装回去** ──
    #     ⚠️ V242 首版写 `if rows: → if False:`：那撤的是「有命中时返回命中」，
    #       打红的是 AZ147（第二级通电顺序），不是 AZ22 ⇒ **目标没红 = 没撤对**。
    #       正确的撤回是把 AZ22 守的那个旧 bug 装回去：零命中回落 `ORDER BY hits DESC`。
    "AZ22": ("mw/memory.py",
             '        if rows:\n            return [self._fact(r, r["score"]) for r in rows]\n'
             "        # ★ 第二级（V84 / §153）：第一级两条路都空 ⇒ 才走字级 2 字链。",
             '        if rows:\n            return [self._fact(r, r["score"]) for r in rows]\n'
             '        _all = self.conn.execute(\n'
             '            "SELECT *, 0.0 AS score FROM facts ORDER BY hits DESC LIMIT ?",\n'
             "            (k,),\n"
             "        ).fetchall()\n"
             "        if _all:                      # 撤回态：零命中静默给全表\n"
             '            return [self._fact(r, r["score"]) for r in _all]\n'
             "        # ★ 第二级（V84 / §153）：第一级两条路都空 ⇒ 才走字级 2 字链。"),
    # ── AZ48：显式 `--suite` 也走包目录兜底（悄悄换文件） ──
    "AZ48": ("run.py",
             "    suite_path = (Path(args.suite).expanduser() if args.suite\n"
             "                  else _data_path(DEF_SUITE))",
             "    suite_path = (_data_path(Path(args.suite).expanduser()) if args.suite\n"
             "                  else _data_path(DEF_SUITE))"),
    # ── AZ62：下一次提问不再带「被丢掉动作的原文」 ──
    #     ⚠️ V242 首版撤的是**正常 propose** 的 `rejected=`：那条路径打红的是
    #       AZ63/AZ95（换形态文本的渲染），AZ62 **没红** ⇒ 场景实际走的是 `replan`。
    #       改成撤 replan 那一处（带它独有的注释才唯一）。
    "AZ62": ("mw/core.py",
             "                        blocked=sorted(attempted_sigs),\n"
             "                        rejected=reseek_lines,\n"
             '                        # ⚠️ 重规划**更**需要易错点：它正是"刚栽了"的那一次。',
             "                        blocked=sorted(attempted_sigs),\n"
             '                        # ⚠️ 重规划**更**需要易错点：它正是"刚栽了"的那一次。'),
    # ── AE8：第二个 `_build` 分支不再透传 --no-external ──
    "AE8": ("run.py",
            "                    lessons=_lessons,\n"
            '                    wire_external=not getattr(args, "no_external", False))\n'
            "    return core",
            "                    lessons=_lessons)\n    return core"),
    # ── TH1：默认档（未设）也发 thinking 键 ⇒ 请求体多一个键 ──
    #     ⚠️ V242 首版把 `if` 整段换成无条件赋值 ⇒ 源码锚 AZ162e（要求那一行逐字存在）
    #       连带转红 —— 那是「锚在同一段文字上」，不是真连带。
    #       改成只把**条件**恒真：源行不动，AZ162e 仍绿，而默认档多出 `{"type": ""}`。
    "TH1": ("mw/llm.py",
            '            if self.cfg.thinking:\n'
            '                body_obj["thinking"] = {"type": self.cfg.thinking}',
            '            if True:\n'
            '                body_obj["thinking"] = {"type": self.cfg.thinking}'),
    # ── TH2：`thinking=disabled` 也不发该键 ⇒ 开关等于标签 ──
    #     ⚠️ 与 TH1 同一段代码、不同改法：TH1 让**默认**多发，TH2 让**显式**不发。
    "TH2": ("mw/llm.py",
            '            if self.cfg.thinking:\n'
            '                body_obj["thinking"] = {"type": self.cfg.thinking}',
            '            if False:\n'
            '                body_obj["thinking"] = {"type": self.cfg.thinking}'),
}


def case_spans(src: str) -> dict[str, tuple[int, int]]:
    """用 **AST** 拿每个条目 `(名字 → (起始行, 结束行))`，0 基、含首尾。

    ⚠️ 不要数 `'''`、也不要靠「行尾是 `),` 且引号平衡」：
    `CASES` 有三种写法（三引号 / 双引号+转义 / 多行拼接字符串），
    后两种的任何启发式都会**提前命中**（AS12 的第 8 行就以 `)',` 结尾）。
    V242 在这里踩了两次，最后用 AST —— 语法树是唯一不会误判的判据。
    """
    import ast
    tree = ast.parse(src)
    for node in tree.body:
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "CASES"):
            elts = node.value.elts
            break
    else:
        raise SystemExit("⛔ 找不到 CASES 的定义（可撤回装置变了）")
    out: dict[str, tuple[int, int]] = {}
    for e in elts:
        name = e.elts[0].value
        out[name] = (e.lineno - 1, e.end_lineno - 1)
    return out


def main() -> int:
    src = W.read_text(encoding="utf-8")
    spans = case_spans(src)
    lines = src.splitlines(keepends=True)
    done: list[str] = []
    #: ⚠️ 必须**按行号从大到小**替换：先改下面的行，上面的行号才不会漂。
    todo = []
    for name, (fname, old, new) in OVR.items():
        if name not in spans:
            raise SystemExit(f"⛔ {name}: CASES 里没有这条")
        todo.append((spans[name][0], name, fname, old, new))
    for _, name, fname, old, new in sorted(todo, reverse=True):
        i, j = spans[name]
        block = (
            f'    ("{name}", "{fname}",\n'
            f"     '''{old}''',\n"
            f"     '''{new}'''),\n"
        )
        lines[i:j + 1] = [block]
        done.append(name)
    W.write_text("".join(lines), encoding="utf-8")
    print(f"已替换 {len(done)} 条：{' '.join(sorted(done))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

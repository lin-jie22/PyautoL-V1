#!/usr/bin/env python3
"""★7y 可行性探针（**零模型**）：给 `mw/verify.py` 加判据，能不能有"牙"？

## 先回答一个更小的问题：**现在的闸是不是根本没牙**

第五十四轮（7w）实测 80/80 task-run `校验=1跑/1过/0打回` ⇒ `note_verdict` 从没走过。
当时判的是"装置未通电"。但还有一种可能：**题面派生的判据太浅，什么都能过**
——`verify.py` 自己的文档里写着 `def f(): pass` 能过全部四项。

本脚本不碰内核、不调模型，只做两件事：

    ① 把 s57 / s58 / s59 三轮归档里所有**最终交付答案**重放一遍（52 个 cap，
       4 题 ⇒ 最多 208 份答案），同一份代码只跑一次（按去重后的语料计）；
    ② 对每份答案同时记三个读数：
         · 外部打分器给的**真分**（ground truth，0.2/0.4/0.6/1.0）
         · **现行** `verify.derive()` 的判决
         · **候选判据**的判决

候选判据分两族，都只从**题面**派生（不读 tests / oracle / 分数）：

| 族 | 派生来源 | 判定方式 |
|---|---|---|
| F1 元数 | 题面写的签名 `truncate_tail(s, n)` | AST 静态看 `def` 有几个参数 |
| F2 题面陈述的性质 | 题面里那些**不是惯例**的话：<br>"最多 n 个字符"<br>"不超长时原样返回"<br>"无法识别 ⇒ None"<br>"返回列表保持原顺序"<br>"格式化成金额字符串" | 用打分器那个沙箱真跑（同一份 `code_exec.run_one`） |

⛔ **这一步是可行性探针，不是规则**：F2 的"题面句 → 断言"映射此处**手写**，每条都标了 `src`
（来自题面的哪一句）。能不能变成通用规则是下一步的问题；此处只回答一件事：

    **候选判据能不能把 0.2 分的答案和 1.0 分的答案分开？**

判据的代价（对一个已经有处处埋好的chip; 两条都算：

    · 误伤 = 在真分 1.0 的答案里，被判"未过"的比例 ⇒ 必须 0（不许惩罚正确答案）
    · 有牙 = 在真分 <1.0 的答案里，被判"未过"的比例 ⇒ 必须 >0（否则闸恒通过，7w 同型）
"""
import ast
import glob
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = "/Users/linshaojie/V1"
SPEC = importlib.util.spec_from_file_location(
    "code_exec", os.path.join(ROOT, "bench/scorers/code_exec.py"))
CE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CE)

VFY_SPEC = importlib.util.spec_from_file_location(
    "vverify", os.path.join(ROOT, "mw/verify.py"))
VFY = importlib.util.module_from_spec(VFY_SPEC)
# dataclass 需要能从 sys.modules 找到自己的 module（幽灵模块会 AttributeError）
sys.modules["vverify"] = VFY
VFY_SPEC.loader.exec_module(VFY)

SUITE = {json.loads(l)["id"]: json.loads(l)
         for l in open(os.path.join(ROOT, "bench/heldout/g7j_n4_heldout.jsonl"),
                       encoding="utf-8")}
FUNCS = {"g7d2_dedupe_keep": "dedupe_keep", "g7d3_fmt_amount": "fmt_amount",
         "g7e1_parse_flag": "parse_flag", "g7e2_truncate_tail": "truncate_tail"}

# ── F2：题面陈述的性质。每条都带 src（该性质取自题面的哪一句原话） ──────────
# ⛔ 手写映射，见文件头说明。src 必须能在题面里逐字找到（脚本会校验）。
PROPS = {
    "g7d2_dedupe_keep": [
        ("列表去重⇒重复项被去掉", "对字符串列表去重",
         "assert dedupe_keep(['a', 'a', 'b']) == ['a', 'b']"),
        ("保持原顺序⇒无重复输入原样返回", "返回列表保持原顺序",
         "assert dedupe_keep(['a', 'b']) == ['a', 'b']"),
    ],
    "g7d3_fmt_amount": [
        ("结果是字符串", "把数字格式化成金额字符串",
         "assert isinstance(fmt_amount(1), str)"),
        ("int 或 float 都要能处理", "输入可能是 int 或 float",
         "assert isinstance(fmt_amount(-1.5), str)"),
    ],
    "g7e1_parse_flag": [
        ("无法识别⇒None", "转成 True / False / None（无法识别）",
         "assert parse_flag('zzzq') is None"),
        ("大小写不敏感+首尾空白忽略", "入参大小写不敏感、首尾空白忽略",
         "assert parse_flag(' Y ') == parse_flag('y')"),
    ],
    "g7e2_truncate_tail": [
        ("不超长时原样返回", "不超长时原样返回",
         "assert truncate_tail('abc', 10) == 'abc'"),
        ("截短到最多 n 个字符", "把过长的字符串截短到最多 n 个字符",
         "assert len(truncate_tail('abcdefghij', 3)) <= 3"),
    ],
}
# F2 的 src 必须逐字在题面里 —— 否则就是我在发明判据，不是读题
_SIG_RE = re.compile(r"函数\s+([A-Za-z_]\w*)\s*\(([^)]*)\)")


def derived_arity_checks(tid):
    """F1：从题面里的签名派生元数。返回 (期望元数, 函数名) 或 None。"""
    prompt = SUITE[tid]["prompt"]
    m = _SIG_RE.search(prompt)
    if not m:
        return None
    name = m.group(1)
    params = [p.strip() for p in m.group(2).split(",") if p.strip()]
    return (name, len(params))


def f1_arity(code, tid):
    """F1 静态判定：`def <名>` 的参数个数与题面签名一致。"""
    exp = derived_arity_checks(tid)
    if exp is None:
        return True, ""
    name, n = exp
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"AST 不可解析：{e.msg}"
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            got = len(node.args.args) + len(
                getattr(node.args, "posonlyargs", []))
            return (True, "") if got == n else (
                False, f"题面签名 {n} 个参数，代码写了 {got} 个")
    return False, f"没有找到 def {name}"


def final_answers(path):
    """把 cap 里 request/response 配对，返回 {task_id: 最后一次 answer 调用正文}。

    复用 s59/attribute_7x.py 的路数：只认 answer() 那条通道（system 带"只输出最终答案本身"）。
    """
    out, pend = {}, None
    for line in open(path, encoding="utf-8"):
        ev = json.loads(line)
        if "#resp" in ev["path"]:
            if pend is None:
                continue
            try:
                body = json.loads(ev["body"])
                txt = body["choices"][0]["message"].get("content", "") or ""
            except (KeyError, ValueError, TypeError):
                pend = None
                continue
            out[pend] = txt
            pend = None
            continue
        if not ev["path"].endswith("/chat/completions"):
            continue
        try:
            msgs = json.loads(ev["body"]).get("messages", [])
        except ValueError:
            continue
        if not msgs or "只输出最终答案本身" not in msgs[0].get("content", ""):
            pend = None
            continue
        u = msgs[-1].get("content", "")
        pend = next((t for t, f in FUNCS.items() if f in u), None)
    return out


# ── 1) 收集语料：**全部归档窗口**（s44–s59，99 个 cap），不挑有利的窗 ──
caps = sorted(glob.glob(f"{ROOT}/bench/evidence/knowledge_*/cap*.jsonl"))
caps = [p for p in caps if "knowledge_s60" not in p]

corpus = defaultdict(lambda: {"n": 0, "caps": []})   # (tid, code) -> 计数
for p in caps:
    tag = os.path.basename(p)[:-6]        # 例：capA_r1
    ans = final_answers(p)
    for tid, txt in ans.items():
        key = (tid, CE.extract_code(txt))
        corpus[key]["n"] += 1
        corpus[key]["caps"].append(tag)

print(f"cap 文件 {len(caps)} 个；去重后语料 {len(corpus)} 份答案"
      f"（合计 {sum(v['n'] for v in corpus.values())} 次交付）")
print()

# ── 2) 逐题\equiv 三条判据的同时评价 ─────────────────────────────────────
stats = {tid: {"满分被判未过": Counter(), "满分总数": 0,
               "低分被判未过": Counter(), "低分总数": 0,
               "现行通过": 0, "现行未过": 0} for tid in SUITE}
samples = defaultdict(list)

for (tid, code), meta in sorted(corpus.items()):
    task = SUITE[tid]
    passed = sum(1 for t in task["tests"] if CE.run_one(code, t)[0])
    score = passed / len(task["tests"])

    v = VFY.run(code, VFY.derive(task["prompt"]))
    st = stats[tid]
    st["现行通过" if v.ok else "现行未过"] += meta["n"]
    # ★逐条：现行闸里到底哪一条在承重？（同一份答案、同一组判据，单跑）
    for c in VFY.derive(task["prompt"]):
        single = VFY.run(code, (c,))
        st[c.kind + ("退纬" if False else "")] = st.get(c.kind, 0) + 0
        key_direct = "现行·单条未过:" + c.kind
        st[key_direct] = st.get(key_direct, 0) + (
            0 if single.ok else meta["n"])

    f1_ok, f1_msg = f1_arity(code, tid)
    f2_fails = []
    for label, src, test in PROPS[tid]:
        if src not in task["prompt"]:
            raise SystemExit(f"⛔ 判据来源不在题面里，拒绝运行：{tid} {src!r}")
        ok, why = CE.run_one(code, test)
        if not ok:
            f2_fails.append(label)

    bucket = "满分" if score == 1.0 else "低分"
    st[bucket + "总数"] += meta["n"]
    if not f1_ok:
        st[bucket + "被判未过"]["F1元数"] += meta["n"]
    for lb in f2_fails:
        st[bucket + "被判未过"]["F2:" + lb] += meta["n"]
    if bucket == "低分" and not v.ok:
        samples[tid].append((score, f1_msg, tuple(f2_fails), meta["caps"][0]))

# ── 3) 汇总 ────────────────────────────────────────────────────────────
print(f"{'题':22s} {'现行闸':>10s}  {'满分n':>5s} {'误伤(满分被判未过)':>22s}"
      f" {'低分n':>5s} {'有牙(低分被判未过)':>22s}")
print("=" * 130)
for tid in SUITE:
    st = stats[tid]
    cur = f"{st['现行通过']}过/{st['现行未过']}未"
    good_n, bad_n = st["满分总数"], st["低分总数"]
    hurt = Counter({k: v for k, v in st["满分被判未过"].items()})
    bite = Counter({k: v for k, v in st["低分被判未过"].items()})
    hurt_s = ", ".join(f"{k}+{v}" for k, v in sorted(hurt.items())) or "零"
    bite_s = ", ".join(f"{k}+{v}" for k, v in sorted(bite.items())) or "零"
    print(f"{tid:22s} {cur:>10s}  {good_n:5d} {hurt_s:>22s} {bad_n:5d} {bite_s:>22s}")

print()
print("=== 附：答案多样性（十六个窗口在这四道题上到底产出过几种不同的最终答案）===")
uniq = Counter(tid for (tid, _c) in corpus)
windows = defaultdict(set)
for (tid, code), meta in corpus.items():
    for tag in meta["caps"]:
        windows[tid].add(tag.split("_")[0][3:])
for tid in SUITE:
    print(f"  {tid:22s} 互异答案 {uniq[tid]:2d} 份 / {sum(corpus[(t2,c)]['n'] for (t2,c) in corpus if t2==tid):3d} 次交付")
print(f"  {'合计':22s} 互异答案 {len(corpus):2d} 份 / "
      f"{sum(v['n'] for v in corpus.values()):3d} 次交付（{len(caps)} 个 cap / "
      f"{len(sorted(set(os.path.basename(p)[3:-6].split('_')[0] for p in caps)))} 类臂）")

print()
print("=== 现行闸**逐条**承重读数（每份互异答案 × 该条判据单独跑；数字 = 被判未过的交付次数）===")
allg = Counter()
for tid in SUITE:
    st = stats[tid]
    row = {k: v for k, v in st.items() if k.startswith("现行·单条未过:")}
    for k, v in row.items():
        allg[k] += v
    print(f"  {tid:22s} 总数{st['现行通过'] + st['现行未过']:4d}  "
          + ", ".join(f"{k.split(':')[1]} 未过{v}" for k, v in sorted(row.items())))
print("  " + "-" * 60)
print(f"  {'全语料':22s} 总数{sum(stats[t]['现行通过'] + stats[t]['现行未过'] for t in SUITE):4d}  "
      + ", ".join(f"{k.split(':')[1]} 未过{v}" for k, v in sorted(allg.items())))
print()
print("=== 明细：现行闸 + 候选判据在各题各分数档的命中（按去重分数列出） ===")
for tid in SUITE:
    print(f"--- {tid} ---")
    agg = defaultdict(lambda: [0, Counter()])
    for (t2, code), meta in corpus.items():
        if t2 != tid:
            continue
        task = SUITE[tid]
        passed = sum(1 for t in task["tests"] if CE.run_one(code, t)[0])
        sc = passed / len(task["tests"])
        _, f1m = f1_arity(code, tid)
        f2 = [lb for lb, _s, test in PROPS[tid] if not CE.run_one(code, test)[0]]
        key = (sc, bool(f1m), tuple(f2))
        agg[key][0] += meta["n"]
        agg[key][1]["n"] += meta["n"]
    for (sc, f1bad, f2), (n, _c) in sorted(agg.items(), key=lambda kv: -kv[0][0]):
        print(f"  真分 {sc:.1f}  份数{n:3d}  F1未过={f1bad}  F2未过={list(f2) or '无'}")

timeout = 0

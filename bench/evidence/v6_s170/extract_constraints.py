"""V81 前置量测：**题面约束机械化**（F 方案）到底抽得出多少、咬不咬得住。

三条前置（见 `PREREG.md` §4）：

  S1 可抽约束条数     —— 规则表派出的 spec 条数 / 覆盖几题
  S2 咬住             —— 31 份**已知错**实现里被判 FAIL 的份数
  S3 误伤             —— 9 份**已知对**实现里被判 FAIL 的份数（必须 0）
  S4 部署性           —— 真正咬住过错的 spec 里，探针不依赖 `tests` 实参的条数

装置全部离线：题面 + 已存盘的 44 份样例代码（V80 产物）+ 题集 `oracle`。
⛔ 期望值（`tests` 里的 `==` 右边）**一律丢弃**；断言性质只来自 `prompt`。
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

EV = Path("bench/evidence/v6_s170")
V80 = Path("bench/evidence/v6_s169/probe_diffself.json")
CASES = [
    ("struct", "bench/heldout/struct_convert_heldout.jsonl"),
    ("g7j_n4", "bench/heldout/g7j_n4_heldout.jsonl"),
]

FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.S)
DEF = re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", re.M)

#: 中文名 → 字符（R4 的转义表用）
CHAR_NAMES = {"空格": " ", "分号": ";", "逗号": ",", "等号": "=", "百分号": "%"}

#: 在**子进程**里跑的判定器：装上代码，逐条探针跑「性质」检查。
#: 只判断性质，⛔ 不比对任何期望值。
RUNNER = r'''
import json, re, sys
sys.setrecursionlimit(4000)
payload = json.load(open(sys.argv[1], encoding="utf-8"))
ns = {"__name__": "__v1_probe__"}
try:
    exec(compile(payload["code"], "<answer>", "exec"), ns)
except BaseException as e:
    print(json.dumps({"fatal": type(e).__name__ + ": " + str(e)[:160]}))
    raise SystemExit(0)
fn = payload["fn"]
if not callable(ns.get(fn)):
    print(json.dumps({"fatal": "missing_fn"}))
    raise SystemExit(0)

def canon(v):
    if isinstance(v, dict):
        return ["d", sorted([[canon(k), canon(x)] for k, x in v.items()], key=repr)]
    if isinstance(v, (list, tuple)):
        return ["l", [canon(x) for x in v]]
    if v is None or isinstance(v, (bool, int, str)):
        return ["v", repr(v)]
    if isinstance(v, float):
        return ["f", repr(v)]
    return ["o", type(v).__name__, repr(v)[:120]]

def chk(kind, val, p):
    if kind == "sep":
        sep = p["sep"]
        if not isinstance(val, str):
            return "NA", "非字符串"
        if len(sep) < 2:
            return "NA", "单字符分隔符无判别力"
        if sep[0] not in val:
            return "NA", "输出里没有该分隔符"
        bad = re.search(re.escape(sep[0]) + "(?!" + re.escape(sep[1:]) + ")", val)
        if bad:
            return "FAIL", "分隔符不是 " + repr(sep) + "：" + repr(val)[:120]
        return "PASS", ""
    if kind == "path_join":
        if not isinstance(val, dict):
            return "NA", "非 dict"
        key = p["key"]
        if key in val:
            return "PASS", ""
        return "FAIL", "没有 " + repr(key) + " 这个键，实际键=" + repr(list(val)[:4])[:160]
    if kind == "nl_least":
        if not isinstance(val, str):
            return "NA", "非字符串"
        if val == "":
            return "NA", "空串（题面另说返回空串）"
        if val.endswith("\n"):
            return "PASS", ""
        return "FAIL", "末尾没有换行符：" + repr(val[-12:])
    if kind == "nl_exact":
        if not isinstance(val, str):
            return "NA", "非字符串"
        if val == "":
            return "NA", "空串"
        if val.endswith("\n") and not val.endswith("\n\n"):
            return "PASS", ""
        return "FAIL", "末尾换行数不是 1：" + repr(val[-14:])
    if kind == "escape":
        ch, code = p["ch"], p["code"]
        if not isinstance(val, str):
            return "NA", "非字符串"
        if code not in val:
            return "FAIL", "缺少 " + code + "：" + repr(val)[:120]
        if ch != "%" and ch in val:
            return "FAIL", "原文 " + repr(ch) + " 残留：" + repr(val)[:120]
        return "PASS", ""
    if kind == "index_fill":
        base, i, value = p["base"], p["i"], p["value"]
        if not isinstance(val, dict):
            return "NA", "非 dict"
        lst = val.get(base)
        if not isinstance(lst, list):
            return "FAIL", repr(base) + " 不是 list：" + repr(lst)[:100]
        if len(lst) != i + 1:
            return "FAIL", "长度 " + str(len(lst)) + " != " + str(i + 1) + "：" + repr(lst)[:100]
        if lst[:i] != [None] * i:
            return "FAIL", "未填位置不是 None：" + repr(lst)[:100]
        if lst[i] != value:
            return "FAIL", "末位不是 " + repr(value) + "：" + repr(lst)[:100]
        return "PASS", ""
    if kind == "order_subseq":
        src, out = p["src"], val
        if not isinstance(out, list):
            return "NA", "非 list"
        j = 0
        for x in src:
            if j < len(out) and out[j] == x:
                j += 1
        if j == len(out) and len(out) <= len(src):
            return "PASS", ""
        return "FAIL", "不是入参的原序子序列：" + repr(out)[:120]
    if kind == "identity":
        if val == p["arg"]:
            return "PASS", ""
        return "FAIL", "未原样返回：" + repr(val)[:120] + " != " + repr(p["arg"])[:100]
    return "NA", "unknown_kind"

out = []
for pr in payload["probes"]:
    try:
        a = eval(pr["calls"][0], dict(ns))
    except BaseException as e:
        out.append({"verdict": "EXC", "why": "调用抛 " + type(e).__name__})
        continue
    if pr["kind"] == "same":
        try:
            b = eval(pr["calls"][1], dict(ns))
        except BaseException as e:
            out.append({"verdict": "EXC", "why": "第二次调用抛 " + type(e).__name__})
            continue
        if canon(a) == canon(b):
            out.append({"verdict": "PASS", "why": ""})
        else:
            out.append({"verdict": "FAIL",
                        "why": "两句不变量不成立：" + repr(a)[:80] + " vs " + repr(b)[:80]})
        continue
    v, w = chk(pr["kind"], a, pr.get("params") or {})
    out.append({"verdict": v, "why": w})
print(json.dumps({"fatal": "", "results": out}, ensure_ascii=False))
'''


def fn_of(code: str) -> str:
    m = DEF.search(code or "")
    return m.group(1) if m else ""


def extract_code(raw: str) -> str:
    m = FENCE.search(raw or "")
    return (m.group(1) if m else (raw or "")).strip()


def load_tasks(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def test_calls(tests: list[str]) -> list[ast.Call]:
    """从 `assert f(...) == ...` 里抽出**调用节点**；期望值整段丢弃。"""
    out = []
    for line in tests:
        try:
            tree = ast.parse(line)
        except SyntaxError:
            continue
        node = tree.body[0]
        if not isinstance(node, ast.Assert) or not isinstance(node.test, ast.Compare):
            continue
        left = node.test.left
        if isinstance(left, ast.Call):
            out.append(left)
    return out


def arg_exprs(call: ast.Call) -> list[str]:
    return [ast.unparse(a) for a in call.args]


def literal_args(call: ast.Call):
    """实参全是字面量时返回列表，否则 None（用于挑探针）。"""
    try:
        return [ast.literal_eval(a) for a in call.args]
    except Exception:
        return None


def strs_in(obj) -> list[str]:
    """递归收集实参里的所有字符串。

    ⚠️ 自踩（本轮）：第一版只查顶层值 ⇒ `{'a': 'x y'}` 里的 `'x y'` 看不见
    ⇒ `R4` 转义表一条都没派出来（`k02` 只抽到 `R1a`）。预注册写的是
    「实参字符串含该字符」，含嵌套。
    """
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out = []
        for k, v in obj.items():
            out += strs_in(k) + strs_in(v)
        return out
    if isinstance(obj, (list, tuple, set)):
        out = []
        for x in obj:
            out += strs_in(x)
        return out
    return []


# ────────────────────────────────────────────────────────────── 规则表

def specs_from_prompt(tid: str, fn: str, prompt: str, tests: list[str]) -> list[dict]:
    """⛔ 只看 prompt 文本（+ tests 的**实参**作为探针输入）。"""
    specs: list[dict] = []
    calls = test_calls(tests)

    # R1a / R1b：`用 'X' 连接`
    for m in re.finditer(r"用\s*['\"]([^'\"]{1,8})['\"]\s*连接", prompt):
        sep = m.group(1)
        clause = prompt[max(0, m.start() - 24): m.end() + 6]
        if "路径" in clause or "键" in clause:
            specs.append({
                "rule": "R1b", "kind": "path_join",
                "calls": [f'{fn}({{"a": {{"b": 1}}}}, {{"a": {{"b": 2}}}})'],
                "params": {"sep": sep, "key": f"a{sep}b"},
                "built": True,
                "note": f"嵌套路径用 {sep!r} 连接",
            })
        elif sep.strip():
            specs.append({
                "rule": "R1a", "kind": "sep",
                "calls": [ast.unparse(c) for c in calls],
                "params": {"sep": sep},
                "built": False,
                "note": f"多项用 {sep!r} 连接",
            })

    # R2a / R2b：末尾换行
    if "末尾也要有换行符" in prompt or ("末尾" in prompt and "换行符" in prompt):
        expr = [ast.unparse(c) for c in calls]
        specs.append({"rule": "R2a", "kind": "nl_least", "calls": expr, "params": {},
                      "built": False, "note": "非空输出以 \\n 结尾（字面读法）"})
        specs.append({"rule": "R2b", "kind": "nl_exact", "calls": expr, "params": {},
                      "built": False, "note": "非空输出恰以一个 \\n 结尾（解释性加严）"})

    # R3：最大下标 +1、未填 None
    if "最大下标" in prompt and "+1" in prompt and "None" in prompt:
        specs.append({
            "rule": "R3", "kind": "index_fill",
            "calls": [f'{fn}({{"a[2]": 0}})'],
            "params": {"base": "a", "i": 2, "value": 0},
            "built": True,
            "note": "长度 = 最大下标+1，未填 None",
        })

    # R4：转义表 `X→%NN`
    for m in re.finditer(r"([^\s，,；;：:]+)\s*→\s*(%[0-9A-Fa-f]{2})", prompt):
        name, code = m.group(1), m.group(2)
        ch = CHAR_NAMES.get(name, name if len(name) == 1 else "")
        if not ch:
            continue
        picked = []
        for c in calls:
            lits = literal_args(c)
            if lits is None:
                continue
            if any(ch in s for s in strs_in(lits)):
                picked.append(ast.unparse(c))
        if picked:
            specs.append({"rule": "R4", "kind": "escape", "calls": picked,
                          "params": {"ch": ch, "code": code}, "built": False,
                          "note": f"{name}→{code}"})

    # R5：大小写不敏感 / 首尾空白忽略
    strs = []
    for c in calls:
        lits = literal_args(c)
        if lits and isinstance(lits[0], str):
            strs.append(lits[0])
    if "大小写不敏感" in prompt and strs:
        s = strs[0]
        specs.append({
            "rule": "R5a", "kind": "same",
            "calls": [f'{fn}({s!r})', f'{fn}({s.upper()!r})'],
            "params": {}, "built": True, "note": "大小写不敏感",
        })
    if "首尾空白忽略" in prompt and strs:
        s = strs[0]
        padded = "  " + s + "  "
        specs.append({
            "rule": "R5b", "kind": "same",
            "calls": [f'{fn}({s!r})', f'{fn}({padded!r})'],
            "params": {}, "built": True, "note": "首尾空白忽略",
        })

    # R6：不超长时原样返回
    if "不超长时原样返回" in prompt:
        picked = []
        for c in calls:
            lits = literal_args(c)
            if lits and len(lits) >= 2 and isinstance(lits[0], str) and isinstance(lits[1], int):
                if len(lits[0]) <= lits[1]:
                    picked.append((ast.unparse(c), lits[0]))
        if picked:
            specs.append({"rule": "R6", "kind": "identity", "calls": picked,
                          "params": {}, "built": False,
                          "note": "不超长时原样返回"})

    # R7：保持原顺序
    if "保持原顺序" in prompt:
        for c in calls:
            lits = literal_args(c)
            if lits and isinstance(lits[0], list):
                specs.append({"rule": "R7", "kind": "order_subseq",
                              "calls": [ast.unparse(c)],
                              "params": {"src": lits[0]}, "built": False,
                              "note": "返回值是入参的原序子序列"})
                break

    return specs


# ────────────────────────────────────────────────────────────── 执行

def expand(specs: list[dict]) -> list[dict]:
    """⚠️ 自踩（本轮）：第一版每条 spec 只把 `calls` 列表整体塞进 runner，而 runner
    只 eval 了 `calls[0]` ⇒ `R1a`/`R2a`/`R2b` 全部落在第一条用例上（`''`/单元素）
    变成「不适用」。预注册 §3 写的是「`tests` 全部」⇒ 这里按调用展开成独立探针。
    """
    out = []
    for s in specs:
        if s["kind"] == "same":
            out.append({"rule": s["rule"], "kind": "same",
                        "calls": s["calls"], "params": s["params"]})
            continue
        for c in s["calls"]:
            if isinstance(c, tuple):
                expr, arg = c
                out.append({"rule": s["rule"], "kind": s["kind"],
                            "calls": [expr], "params": {"arg": arg}})
            else:
                out.append({"rule": s["rule"], "kind": s["kind"],
                            "calls": [c], "params": s["params"]})
    return out


def run_code(code: str, fn: str, probes: list[dict], timeout: float = 25.0) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"code": code, "fn": fn, "probes": probes}, f, ensure_ascii=False)
        payload_path = f.name
    try:
        r = subprocess.run([sys.executable, "-c", RUNNER, payload_path],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return {"fatal": f"runner exit {r.returncode}: {(r.stderr or '')[-160:]}"}
        return json.loads((r.stdout or "{}").strip().splitlines()[-1] or "{}")
    except subprocess.TimeoutExpired:
        return {"fatal": "TIMEOUT"}
    finally:
        Path(payload_path).unlink(missing_ok=True)


def main() -> int:
    v80 = json.loads(V80.read_text(encoding="utf-8"))
    stored = {t["id"]: t for t in v80["tasks"]}

    tasks, impls = [], []
    for tag, path in CASES:
        for t in load_tasks(path):
            fn = fn_of(t["oracle"])
            specs = specs_from_prompt(t["id"], fn, t["prompt"], t["tests"])
            tasks.append({"suite": tag, "id": t["id"], "fn": fn,
                          "n_specs": len(specs), "rules": [s["rule"] for s in specs],
                          "built": sum(1 for s in specs if s["built"]), "specs": specs})
            impls.append({"id": t["id"], "suite": tag, "kind": "oracle",
                          "label": "pass", "code": extract_code(t["oracle"])})
            rec = stored.get(t["id"]) or {}
            for i, code in enumerate(rec.get("codes") or []):
                impls.append({"id": t["id"], "suite": tag, "kind": f"stored#{i + 1}",
                              "label": rec["labels"][i], "code": code})

    # 只跑有 spec 的题（无 spec 的实现不进 S2/S3 分母）
    by_task = {t["id"]: t for t in tasks}
    results = []
    for im in impls:
        t = by_task[im["id"]]
        if not t["specs"]:
            continue
        probe_list = expand(t["specs"])
        res = run_code(im["code"], t["fn"],
                       [{"kind": p["kind"], "calls": p["calls"], "params": p["params"]}
                        for p in probe_list])
        if res.get("fatal"):
            results.append({**{k: im[k] for k in ("id", "suite", "kind", "label")},
                            "fatal": res["fatal"], "per_spec": []})
            continue
        per = []
        for s, r in zip(probe_list, res.get("results") or []):
            per.append({"rule": s["rule"], "verdict": r["verdict"], "why": r["why"]})
        results.append({**{k: im[k] for k in ("id", "suite", "kind", "label")},
                        "fatal": "", "per_spec": per})

    # ── 汇总
    n_specs = sum(t["n_specs"] for t in tasks)
    covered = [t["id"] for t in tasks if t["n_specs"]]
    wrong = [r for r in results if r["label"] != "pass" and not r["fatal"]]
    right = [r for r in results if r["label"] == "pass" and not r["fatal"]]
    faults = [r for r in results if r["fatal"]]

    def bite(r):
        return [p for p in r["per_spec"] if p["verdict"] == "FAIL"]

    bite_wrong = [r for r in wrong if bite(r)]
    hurt_right = [r for r in right if bite(r)]

    rule_stats: dict[str, dict] = {}
    for r in results:
        for p in r["per_spec"]:
            d = rule_stats.setdefault(p["rule"], {"bit": 0, "hurt": 0, "na": 0, "pass": 0})
            if p["verdict"] == "FAIL":
                d["hurt" if r["label"] == "pass" else "bit"] += 1
            elif p["verdict"] == "PASS":
                d["pass"] += 1
            else:
                d["na"] += 1

    print("=" * 78)
    print("S1 可抽约束")
    for t in tasks:
        print(f"  {t['id']:22s} {t['n_specs']} 条 {t['rules']}  自造探针={t['built']}")
    print(f"  合计 {n_specs} 条，覆盖 {len(covered)}/{len(tasks)} 题")

    print("=" * 78)
    print(f"S2 咬住（分母=已知错实现 {len(wrong)} 份）")
    for r in results:
        if r["label"] == "pass" or r["fatal"]:
            continue
        b = bite(r)
        mark = "咬住" if b else "  - "
        why = b[0]["why"][:76] if b else ""
        print(f"  {mark} {r['id']:22s} {r['kind']:9s} {why}")
    print(f"  → 咬住 {len(bite_wrong)} / {len(wrong)}")

    print("=" * 78)
    print(f"S3 误伤（分母=已知对实现 {len(right)} 份）")
    for r in right:
        b = bite(r)
        print(f"  {'误伤' if b else ' ok '} {r['id']:22s} {r['kind']:9s} "
              f"{(b[0]['why'][:76] if b else '')}")
    print(f"  → 误伤 {len(hurt_right)} / {len(right)}")

    print("=" * 78)
    print("逐条规则")
    for k in sorted(rule_stats):
        d = rule_stats[k]
        print(f"  {k:5s} 咬住={d['bit']:2d} 误伤={d['hurt']:2d} "
              f"通过={d['pass']:3d} 不适用={d['na']:3d}")

    s4 = sorted({p["rule"] for r in bite_wrong for p in r["per_spec"]
                 if p["verdict"] == "FAIL"
                 and any(s["rule"] == p["rule"] and s["built"] for s in by_task[r["id"]]["specs"])})
    print("=" * 78)
    print(f"S4 部署性：既咬住过错、探针又自造（不依赖 tests 实参）的规则 = {s4}")

    if faults:
        print("=" * 78)
        print(f"装置故障 {len(faults)} 份（不计入分母）")
        for r in faults:
            print(f"  {r['id']:22s} {r['kind']:9s} {r['fatal'][:80]}")

    EV.joinpath("extract_constraints.json").write_text(json.dumps(
        {"tasks": tasks, "results": results,
         "summary": {"n_specs": n_specs, "covered": covered,
                     "bite": len(bite_wrong), "wrong_n": len(wrong),
                     "hurt": len(hurt_right), "right_n": len(right),
                     "faults": len(faults), "s4": s4,
                     "rule_stats": rule_stats}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

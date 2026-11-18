"""V192：部署面 vs 装置面 —— 33 个函数的宿主在哪。

⛔ 不跑模型、不改实现。只做两件事：算导入闭包、数引用。
读数是「个数」，不是分数。
"""
from __future__ import annotations

import ast
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
TARGETS = ("mw.critgen", "mw.metacrit", "mw.probe")
ENTRY_DEPLOY = "run"                    # ⚠️ 模块名，⛔ 不是文件名（初版传 "run.py" ⇒ path_of 拼成 run.py.py ⇒ 闭包恒空）
ENTRY_DEVICE = "tests.contract_test"


# ── 模块名 ⇄ 路径 ─────────────────────────────────────────────────────────
def path_of(mod: str) -> pathlib.Path | None:
    p = ROOT / (mod.replace(".", "/") + ".py")
    if p.is_file():
        return p
    p2 = ROOT / mod.replace(".", "/") / "__init__.py"
    return p2 if p2.is_file() else None


def mod_of(path: pathlib.Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def exists(mod: str) -> bool:
    return path_of(mod) is not None


# ── 导入解析：一个文件里所有 Import / ImportFrom（含函数体内）──────────────
def imports_in(src: str, self_mod: str) -> list[tuple[str, str | None]]:
    """返回 [(被导入模块, 绑定名或 None)]。`None` = 模块本身（`import X`）。"""
    out: list[tuple[str, str | None]] = []
    tree = ast.parse(src)
    pkg = self_mod.rsplit(".", 1)[0] if "." in self_mod else ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name, a.asname or a.name))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = pkg.split(".") if pkg else []
                if node.level > 1:
                    up = up[: len(up) - (node.level - 1)] if node.level - 1 <= len(up) else []
                base = ".".join([p for p in up if p] + ([base] if base else []))
            for a in node.names:
                full = f"{base}.{a.name}" if base else a.name
                out.append((full if exists(full) else base, a.asname or a.name))
    return out


def closure(entry: str) -> dict[str, set[str]]:
    """{模块: {它导入了谁}} —— 从 entry 出发的闭包。"""
    seen: dict[str, set[str]] = {}
    todo = [entry]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        p = path_of(mod)
        if p is None:
            continue
        imp = imports_in(p.read_text(encoding="utf-8"), mod)
        deps: set[str] = set()
        for target, _bind in imp:
            # 只跟能解析成仓库文件的导入（stdlib / 第三方自然被过滤掉）
            if exists(target):
                deps.add(target)
                todo.append(target)
        seen[mod] = deps
    return seen


# ── 引用计数 ──────────────────────────────────────────────────────────────
def bindings(src: str, self_mod: str) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """(模块别名 → 模块名, 对象名 → (模块名, 属性名))"""
    alias: dict[str, str] = {}
    objs: dict[str, tuple[str, str]] = {}
    tree = ast.parse(src)
    pkg = self_mod.rsplit(".", 1)[0] if "." in self_mod else ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                alias[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = pkg.split(".") if pkg else []
                if node.level > 1 and node.level - 1 <= len(up):
                    up = up[: len(up) - (node.level - 1)]
                base = ".".join([p for p in up if p] + ([base] if base else []))
            for a in node.names:
                full = f"{base}.{a.name}" if base else a.name
                if exists(full):
                    alias[a.asname or a.name] = full
                else:
                    objs[a.asname or a.name] = (base, a.name)
    return alias, objs


def refs(entry: str, mods_in: dict[str, set[str]]) -> dict[str, int]:
    """闭包内对**任意** `mw.*` 模块的引用次数（别名 + 属性访问）。

    ⚠️ 初版只对 `TARGETS` 三个模块计数 ⇒ 其余 124 个函数永远「零引用」，
       而那是**定义推出来的**，不是读数（预注册 §2 自己写下的陷阱，仍然踩了）。
       现在按 `mw.*` 全量计数。
    """
    per_mod: dict[str, int] = {}
    per_fn: dict[str, int] = {}
    for mod in mods_in:
        p = path_of(mod)
        if p is None:
            continue
        src = p.read_text(encoding="utf-8")
        alias, objs = bindings(src, mod)
        tree = ast.parse(src)

        def bump(tgt: str, attr: str) -> None:
            if not (tgt == "mw" or tgt.startswith("mw.")):
                return
            full = f"mw.{attr}" if tgt == "mw" else f"{tgt}.{attr}"
            per_mod[tgt] = per_mod.get(tgt, 0) + 1
            per_fn[full] = per_fn.get(full, 0) + 1

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                tgt = alias.get(node.value.id)
                if tgt:
                    bump(tgt, node.attr)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in objs:
                    m, attr = objs[node.id]
                    bump(m, attr)
    return per_mod, per_fn


def public_fns(mod: str) -> list[str]:
    p = path_of(mod)
    if p is None:
        return []
    tree = ast.parse(p.read_text(encoding="utf-8"))
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not n.name.startswith("_")]


# ── P6：verify.py 有没有「采判据」的入口 ─────────────────────────────────
def verify_surface() -> list[dict]:
    p = ROOT / "mw/verify.py"
    tree = ast.parse(p.read_text(encoding="utf-8"))
    out = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_"):
            args = [a.arg for a in n.args.args] + [a.arg for a in n.args.kwonlyargs]
            out.append({"fn": n.name, "args": args})
    return out


# ── 主 ────────────────────────────────────────────────────────────────────
def main() -> int:
    R: dict = {"prereg": "PREREG_v192.md"}

    dep = closure(ENTRY_DEPLOY)
    dev = closure(ENTRY_DEVICE)
    R["deploy_modules"] = sorted(dep)
    R["deploy_n"] = len(dep)
    R["device_modules_n"] = len(dev)
    R["targets_in_deploy"] = [m for m in TARGETS if m in dep]
    R["targets_in_device"] = [m for m in TARGETS if m in dev]

    dep_mod, dep_fn = refs(ENTRY_DEPLOY, dep)
    dev_mod, dev_fn = refs(ENTRY_DEVICE, dev)
    R["P1_targets_in_deploy"] = len(R["targets_in_deploy"])
    R["P3_refs_from_deploy"] = sum(dep_mod.get(m, 0) for m in TARGETS)
    R["P3_per_mod"] = {m: dep_mod.get(m, 0) for m in TARGETS}
    R["P4_refs_from_device"] = sum(dev_mod.get(m, 0) for m in TARGETS)
    R["P4_per_mod"] = {m: dev_mod.get(m, 0) for m in TARGETS}
    R["P4_per_fn"] = dict(sorted((k, v) for k, v in dev_fn.items()
                                 if any(k.startswith(m + ".") for m in TARGETS)))

    # 动态证据（⚠️ 不能用 -I：隔离模式不带 cwd ⇒ import run 直接失败，而 stdout 空会冒充「0 个模块」）
    code = ("import sys, json\nimport run\n"
            "print(json.dumps(sorted(m for m in sys.modules if m.startswith('mw.'))))\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print("动态臂崩溃：", (r.stderr or "").strip()[-400:])
        return 2
    dyn = json.loads((r.stdout or "[]").strip() or "[]")
    R["P2_dynamic_modules"] = dyn
    R["P2_targets_loaded"] = [m for m in TARGETS if m in dyn]
    R["P2_rc"] = r.returncode

    # 全 mw 公开函数的零引用计数
    allfns: dict[str, list[str]] = {}
    for p in sorted((ROOT / "mw").glob("*.py")):
        m = mod_of(p)
        allfns[m] = public_fns(m)
    total = sum(len(v) for v in allfns.values())
    zero: list[str] = []
    for m, fns in allfns.items():
        if m not in dep:
            zero.extend(f"{m}.{f}" for f in fns)
            continue
        for f in fns:
            if dep_fn.get(f"{m}.{f}", 0) == 0:
                # 同模块内 Name 直呼也算引用
                if _local_used(m, f):
                    continue
                zero.append(f"{m}.{f}")
    R["mw_public_total"] = total
    R["mw_zero_ref_from_deploy"] = len(zero)
    R["mw_zero_ref_list"] = zero
    R["P5_ok"] = len(zero) >= 5

    R["verify_surface"] = verify_surface()
    hits = [f for f in R["verify_surface"]
            if any(a in ("kind", "types", "ops", "operators", "types_") for a in f["args"])]
    R["P6_host_hits"] = hits

    R["P1_ok"] = R["P1_targets_in_deploy"] == 0
    R["P2_ok"] = len(R["P2_targets_loaded"]) == 0
    R["P3_ok"] = R["P3_refs_from_deploy"] == 0
    R["P4_ok"] = R["P4_refs_from_device"] > 0
    R["P6_ok"] = len(hits) == 0

    # 自校验：闭包空 = 检测器瞎，读数作废
    if R["deploy_n"] == 0 or R["device_modules_n"] == 0:
        print("⛔ 闭包为空 ⇒ 检测器瞎，本轮读数作废")
        return 3

    # R2 正例对照：往 run.py 头部塞一行 import，P1 必须从 0/3 变 1/3
    R["R2"] = r2_control()

    print("=" * 74)
    print(f"[P1] 部署闭包 {R['deploy_n']} 个 mw 模块；三目标在其中："
          f"{R['targets_in_deploy']} ⇒ {'✅' if R['P1_ok'] else '❌'}")
    print(f"[P2] 动态 import run 后加载 {len(dyn)} 个 mw 模块；三目标："
          f"{R['P2_targets_loaded']} ⇒ {'✅' if R['P2_ok'] else '❌'}")
    print(f"[P3] 部署闭包对三目标的引用：{R['P3_per_mod']} 共 {R['P3_refs_from_deploy']}"
          f" ⇒ {'✅' if R['P3_ok'] else '❌'}")
    print(f"[P4] 装置闭包（contract_test）对三目标的引用：{R['P4_per_mod']} "
          f"共 {R['P4_refs_from_device']} ⇒ {'✅' if R['P4_ok'] else '❌ 检测器瞎'}")
    print(f"[P5] mw 公开函数 {total} 个，部署闭包内零引用 {len(zero)} 个"
          f" ⇒ {'✅' if R['P5_ok'] else '❌'}")
    print(f"[P6] verify.py 公开函数 {len(R['verify_surface'])} 个，"
          f"带 kind/types/ops 参数的：{hits} ⇒ {'✅' if R['P6_ok'] else '❌ 宿主存在'}")
    print(f"[R2] 往 run.py 塞一行 import 后：闭包 {R['R2'].get('closure_n')} 个模块、"
          f"含 {R['R2'].get('closure_has')}；动态含 {R['R2'].get('dyn_has')}"
          f" ⇒ {'✅ 检测器有牙' if R['R2']['ok'] else '❌ 检测器瞎'}")
    print("=" * 74)
    print("三目标各自的公开函数数：",
          {m: len(public_fns(m)) for m in TARGETS})
    print("零引用（含三目标全家）前 40：")
    for x in zero[:40]:
        print("   ", x)

    (ROOT / "bench/evidence/l3_s280/readings_v192.json").write_text(
        json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nreadings 已写。")
    return 0


def r2_control() -> dict:
    """正例对照：给 `run.py` 临时加一行 import，看闭包与动态是否都跟着变。

    ⛔ 只在内存里改、`finally` 里逐字节还原；跑完用 `git diff --exit-code run.py` 自证。
    """
    p = ROOT / "run.py"
    bak = p.read_text(encoding="utf-8")
    out: dict = {}
    # ⚠️ 不能塞在文件第一行：`run.py` 第 17 行是 `from __future__ import annotations`，
    #    前面插任何语句都会 SyntaxError ⇒ 动态臂 rc≠0 ⇒ 看起来像「检测器瞎」（本轮自踩第 1 条）。
    #    插在 `__future__` 那一行之后才是合法位置。
    lines = bak.splitlines(keepends=True)
    idx = next((k for k, ln in enumerate(lines)
                if ln.startswith("from __future__")), -1)
    if idx < 0:
        raise RuntimeError("run.py 没有 __future__ 行 —— 装置前提变了，先看一遍再改")
    patched = "".join(lines[: idx + 1]) + "from mw import critgen as _cg_r2  # noqa: F401\n" \
              + "".join(lines[idx + 1:])
    try:
        p.write_text(patched, encoding="utf-8")
        d = closure(ENTRY_DEPLOY)
        out["closure_has"] = [m for m in TARGETS if m in d]
        out["closure_n"] = len(d)
        code = ("import sys, json\nimport run\n"
                "print(json.dumps(sorted(m for m in sys.modules if m.startswith('mw.'))))\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=300)
        out["dyn_rc"] = r.returncode
        if r.returncode == 0:
            dyn = json.loads((r.stdout or "[]").strip() or "[]")
            out["dyn_has"] = [m for m in TARGETS if m in dyn]
        else:
            out["dyn_has"] = None
            out["dyn_err"] = (r.stderr or "").strip()[-300:]
    finally:
        p.write_text(bak, encoding="utf-8")
    out["ok"] = bool(out.get("closure_has")) and bool(out.get("dyn_has"))
    return out


def _local_used(mod: str, fn: str) -> bool:

    """同模块内的 `Name(fn)` 使用（含自己调自己以外的调用）。"""
    p = path_of(mod)
    if p is None:
        return False
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == fn:
            return True
    return False


if __name__ == "__main__":
    sys.exit(main())

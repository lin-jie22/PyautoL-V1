#!/usr/bin/env python3
"""口径可比性审计（零模型、零网络）：`llm_cond` 到底能不能被拆成「被测」与「通道」。

## 它要回答的问题

§53.5 把一笔账记成「`caliber_sig` 把模型名塞进去了 ⇒ 跨模型比较必然 sig 不同」。
这个说法**不准确**，而且照它去改内核会撞上一条已存在的机制（`Caliber.diff_except`）。
先把它精确化：

    `llm_cond` 是一个**不可再分的整体**，它同时装着
        被测对象：`model`（+ 有效时 `thinking`）
        通道    ：`endpoint` / `timeout_s` / `offline`
    ⇒ 声明豁免 `llm_cond` 时，**无法区分「换了模型」和「换了通道」**。
    而 §53 刚刚用读数证明：只换通道（0.3714 → 0.9286）就能让分数翻倍。

所以真欠账不是「签名里有模型名」，而是「**一个字段里混了两类变量**」。

## 本审计不猜，只数

扫所有 `runs.jsonl`，把去重后的 `llm_cond` 两两配对，按键级差异分三类：

    SAME          完全相同
    SUBJECT_ONLY  只差被测对象（model / thinking）  → 这才是「换被测对象」的证据
    CHANNEL_ONLY  只差通道（endpoint / timeout_s）  → §53 已证：读数会变，不可当同一把尺子
    MIXED         两类都差                          → 豁免 `llm_cond` 会把通道变动暗放行

## 输出

    · 去重后的 `llm_cond` 全集
    · 每一对的分类
    · 三类各有多少对（**0 对 SUBJECT_ONLY 就是最要紧的读数**）

用法：
    python3 bench/probe_caliber_split.py [--dir .] [--json]
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from itertools import combinations
from pathlib import Path

#: 被测对象：换它 = 换「量谁」。
#: `thinking` 归这里而不是通道 —— 它改的是**模型的行为**（§50：关思考交付 5/5），
#: 不是通道的传输方式。`temperature` 不在此列，它是 `Caliber` 的独立字段。
SUBJECT_KEYS = ("model", "thinking")
#: 通道：换它 = 换「怎么送」。§53 实测：只换这个就能让分数从 0.3714 到 0.9286。
CHANNEL_KEYS = ("endpoint", "timeout_s", "offline")

OTHER_TOKEN = "open.bigmodel.cn"   # 仅用于打印时脱敏，不影响判定

#: 冻结台账：S6a 的**唯一**比对基准（相对仓库根）。
#: ⛔ 第一百零一轮 §71 自踩：原来判据锚在「全仓库扫到多少行」，而 `collect()`
#:    是递归扫 ⇒ 往 `bench/evidence/` 里多放两份 `runs.jsonl` 就把门禁撞红
#:    （149 行 / 8 种）。判据必须锚在**文件**上，不能锚在「仓库里碰巧有几个文件」。
FROZEN_LEDGER = "bench_out/runs.jsonl"


def classify(a: dict, b: dict) -> str:
    """按键级差异分类两个 `llm_cond`。⛔ 不认整体哈希，只认逐键。"""
    keys = sorted(set(a) | set(b))
    sub = [k for k in keys if k in SUBJECT_KEYS and a.get(k) != b.get(k)]
    chn = [k for k in keys if k in CHANNEL_KEYS and a.get(k) != b.get(k)]
    unk = [k for k in keys if k not in SUBJECT_KEYS and k not in CHANNEL_KEYS
           and a.get(k) != b.get(k)]
    if not sub and not chn and not unk:
        return "SAME"
    if sub and not chn and not unk:
        return "SUBJECT_ONLY"
    if chn and not sub and not unk:
        return "CHANNEL_ONLY"
    return "MIXED"


def _is_frozen(src: str) -> bool:
    """这一行来自**冻结台账**吗？

    ⛔ 必须逐字比较，不能用 `"bench_out" in src` —— 子串匹配会把 `bench_out_x/`
       与 `bench_out/runs.jsonl.bak` 一起捞进来，判据就会随目录名漂。
       S6b 就是钉这一条的边界反证。
    """
    return src == FROZEN_LEDGER


def collect(root: Path) -> list[dict]:
    """扫所有 runs.jsonl，取每一行的 (来源, sig, llm_cond, offline, suite_sha)。"""
    out = []
    for f in sorted(root.rglob("runs.jsonl")):
        if any(part.startswith(".") for part in f.parts):
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            cal = r.get("caliber") or {}
            lc = cal.get("llm_cond")
            if not isinstance(lc, dict):
                continue
            out.append({
                "src": str(f.relative_to(root)),
                "sig": r.get("caliber_sig") or "",
                "solver": r.get("solver") or "",
                "suite": cal.get("suite_sha"),
                "llm_cond": lc,
            })
    return out


def _self_check() -> int:
    """九条反证。

    ⛔ 最要紧的是 S1 与 S2 **成对**：只有 S1（仪器抓得住真·换被测对象）成立，
    主表里那个 `SUBJECT_ONLY = 0` 才是"历史上没发生过"，而不是"仪器永远判不出来"。
    一个恒返回 MIXED 的分类器会让 0 对看起来像重大发现 —— 这正是纪律⑩
    「检测器必须自带反证」要挡的东西。

    S6a/S6b 是 §71 补的：冻结读数的判据必须锚在**一个文件**上（`bench_out/runs.jsonl`），
    锚在「全仓库扫到多少行」就等于锚在「仓库里碰巧有几个 evidence 文件」——
    往 `bench/evidence/` 里加两份台账会把它撞红（实测 149 行 / 8 种，门禁 EXIT=1）。
    """
    bad = 0

    def chk(name: str, ok: bool, note: str = "") -> None:
        nonlocal bad
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  [{note}]" if note else ""))
        if not ok:
            bad += 1

    print("── 口径拆分审计自检（九条反证）──")

    base = {"endpoint": "127.0.0.1:11434", "model": "qwen2.5:7b",
            "offline": False, "timeout_s": 120.0}

    # S1 仪器必须认得出「真·换被测对象」。反证方向：若它判不出 SUBJECT_ONLY，
    #    主表里的 0 对就毫无信息量。
    chk("S1 只改 model ⇒ SUBJECT_ONLY（仪器认得出换被测对象）",
        classify(base, {**base, "model": "gpt-4o-mini"}) == "SUBJECT_ONLY")
    chk("S1b 只加 thinking ⇒ SUBJECT_ONLY（thinking 归被测侧）",
        classify(base, {**base, "thinking": "disabled"}) == "SUBJECT_ONLY")

    # S2 仪器必须认得出「只换通道」，且**不能**与 SUBJECT_ONLY 混淆。
    chk("S2 只改 endpoint ⇒ CHANNEL_ONLY",
        classify(base, {**base, "endpoint": "api.openai.com"}) == "CHANNEL_ONLY")
    chk("S2b 只改 timeout_s ⇒ CHANNEL_ONLY",
        classify(base, {**base, "timeout_s": 300.0}) == "CHANNEL_ONLY")

    # S3 同类同写 ⇒ SAME（否则「假差异」会被误判成真差异，反向也不行）
    chk("S3 完全相同 ⇒ SAME", classify(base, dict(base)) == "SAME")

    # S4 两类都差 ⇒ MIXED（证明它不会把混装当成任一单类放过）
    chk("S4 model 与 endpoint 同时改 ⇒ MIXED",
        classify(base, {**base, "model": "gpt-4o-mini",
                        "endpoint": "api.openai.com"}) == "MIXED")

    # S5 同一台服务的两种写法被判 CHANNEL_ONLY。
    #    ⚠️ 这里**不是**在说签名错了 —— 实测（2026-10-26）：
    #      `127.0.0.1:11434` 与 `localhost:11434` 的 `/api/tags` **逐字节相同**
    #      （sha256 = 63793910…f0c4）⇒ 同一台 ollama；
    #      但解析到不同 IP 栈（`127.0.0.1` → IPv4；`localhost` → `::1`），
    #      实测延迟 `0.013s` vs `0.159s`，**差 12 倍**。
    #    ⇒ `endpoint` 是**传输路径**，不是服务身份；而路径差异是**可测的真实差异**，
    #      放进签名符合「每个键都要对应一次会改变读数的真实请求差异」。
    #    ⇒ 我原先"这是假差异"的假设被这条实测推翻，见 §55.4。
    chk("S5 同一台 ollama 的 v4/v6 两种写法被判 CHANNEL_ONLY（同一服务、不同路径、延迟差 12 倍）",
        classify(base, {**base, "endpoint": "localhost:11434"}) == "CHANNEL_ONLY")

    # S6 冻结台账的读数必须稳定。
    #   ⛔ 第一百零一轮 §71 自踩：这条原来写的是 `len(rows) == 147 and len(ks) == 7`，
    #      而 `collect()` 是 `rglob("runs.jsonl")` **递归扫全仓库** ⇒ 那两个数是
    #      「145 行冻结台账 + 恰好两份 evidence 台账」的**巧合**。
    #      我在 evidence 里多放两份 runs.jsonl，门禁当场 EXIT=1（149 行 / 8 种）。
    #      ⇒ 量具被"加证据"撞坏，说明它的判据锚在**无关文件的数量**上，不是在台账本身。
    #   修法：判据只认**冻结台账**这一份文件；额外台账单独计数、只作信息打印。
    #   反证 S6b：`_is_frozen` 必须只认这一个路径（子串匹配会把 bench_out_x/ 也捞进来）。
    rows_all = collect(Path(__file__).resolve().parent.parent)
    rows = [r for r in rows_all if _is_frozen(r["src"])]
    if not rows:
        chk("S6a 冻结台账在盘上（缺文件时不许把'没量到'读成 0 对）", False,
            f"全库扫到 {len(rows_all)} 行，冻结台账 0 行")
    else:
        uniq: dict[str, dict] = {}
        for r in rows:
            k = json.dumps(r["llm_cond"], sort_keys=True, ensure_ascii=False)
            uniq.setdefault(k, r["llm_cond"])
        ks = list(uniq)
        tally = {"SAME": 0, "SUBJECT_ONLY": 0, "CHANNEL_ONLY": 0, "MIXED": 0}
        for ka, kb in combinations(ks, 2):
            tally[classify(uniq[ka], uniq[kb])] += 1
        chk("S6a 冻结台账复现冻结读数（145 行 / 5 种 / SUBJECT_ONLY = 0）",
            len(rows) == 145 and len(ks) == 5 and tally["SUBJECT_ONLY"] == 0,
            f"{len(rows)} 行 / {len(ks)} 种 / {tally}")
        # 额外台账只报数，不参与判据（它们是**别的轮次**的证据，不是这把尺子的基准）
        print(f"      ℹ️ 全库另有 {len(rows_all) - len(rows)} 行 evidence 台账 "
              f"（不参与 S6a；§71 之前它们会被算进去 ⇒ 判据随证据数量漂）")

    # S6b ⭐ 反证：`_is_frozen` 的边界必须**逐字**，不能是子串匹配
    ok_b = (_is_frozen("bench_out/runs.jsonl")
            and not _is_frozen("bench/evidence/artifact_run_s101/t1_old_ruler/runs.jsonl")
            and not _is_frozen("bench_out_x/runs.jsonl")
            and not _is_frozen("bench_out/runs.jsonl.bak"))
    chk("S6b 冻结台账的识别是逐字路径（子串匹配会把 bench_out_x 也捞进来）", bool(ok_b),
        "四条边界样本" if ok_b else "有样本判错")

    print(f"\n结论：{'全部成立' if bad == 0 else f'{bad} 条不成立'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-check", action="store_true", help="六组反证，零模型零网络")
    a = ap.parse_args()

    if a.self_check:
        return _self_check()

    rows = collect(Path(a.dir))
    if not rows:
        print("没有找到任何带 llm_cond 的 runs.jsonl", file=sys.stderr)
        return 2

    # 去重：llm_cond 的规范化 JSON 作为身份
    uniq: dict[str, dict] = {}
    for r in rows:
        k = json.dumps(r["llm_cond"], sort_keys=True, ensure_ascii=False)
        u = uniq.setdefault(k, {"cond": r["llm_cond"], "n": 0, "sigs": set(), "srcs": set()})
        u["n"] += 1
        if r["sig"]:
            u["sigs"].add(r["sig"])
        u["srcs"].add(r["src"])

    print(f"扫到 {len(rows)} 行运行记录，去重后 {len(uniq)} 种 llm_cond\n")
    print("── 去重后的 llm_cond 全集 ──")
    keys = sorted(uniq)
    for i, k in enumerate(keys, 1):
        u = uniq[k]
        c = u["cond"]
        desc = "  ".join(f"{x}={c.get(x)!r}" for x in sorted(c))
        print(f"[{i}] {u['n']:3d} 行 | sig {sorted(u['sigs'])[:2]} | {desc}")
        print(f"      来源: {', '.join(sorted(u['srcs']))}")

    # 两两配对
    tally = {"SAME": 0, "SUBJECT_ONLY": 0, "CHANNEL_ONLY": 0, "MIXED": 0}
    pairs = []
    for (ka, kb) in combinations(keys, 2):
        cls = classify(uniq[ka]["cond"], uniq[kb]["cond"])
        tally[cls] += 1
        pairs.append((cls, ka, kb))

    print(f"\n── 两两配对（{len(pairs)} 对）──")
    for cls in ("SUBJECT_ONLY", "CHANNEL_ONLY", "MIXED", "SAME"):
        sel = [p for p in pairs if p[0] == cls]
        print(f"\n{cls}：{len(sel)} 对")
        for _, ka, kb in sel:
            ca, cb = uniq[ka]["cond"], uniq[kb]["cond"]
            diff = [k for k in sorted(set(ca) | set(cb)) if ca.get(k) != cb.get(k)]
            print(f"  · 差 {diff}")
            for tag, c in (("A", ca), ("B", cb)):
                print(f"      {tag}: " + "  ".join(f"{x}={c.get(x)!r}" for x in sorted(c)))

    print("\n── 结论读数 ──")
    print(f"  SUBJECT_ONLY（只差被测对象）= **{tally['SUBJECT_ONLY']} 对**"
          f"  ← 非 0 才说明'换被测对象'这个比较在历史台账里真实发生过")
    print(f"  CHANNEL_ONLY（只差通道）    = {tally['CHANNEL_ONLY']} 对"
          f"  ← §53 已证这类差异会让分数翻倍 ⇒ 豁免 llm_cond 会把它暗放行")
    print(f"  MIXED（两类都差）           = {tally['MIXED']} 对")

    if a.json:
        print("\n" + json.dumps({"n_rows": len(rows), "n_uniq": len(uniq), "tally": tally},
                                ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

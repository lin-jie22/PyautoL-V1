"""V36 分析器：去掉 `b#N` 之后，state 稳不稳、那 16 分还在不在。

判据全部来自预注册（`bench/evidence/v6_s126/PREREG.md`，冻结于 13:41:00）：

  D1  累积臂跨轮 `[工作记忆]` 块的 same 比例，通过 = **100%**
  D2  `e1wi` 在两臂的分数，必须 **2×2 逐格**（⛔ 不许只报 Σ）
  D3  `Caliber.diff_except(other, {"memory_seed"})` 必须**空**
  D4  dump 的 `guidance` 在两轮之间必须非空且不同；全空 ⇒ 如实登记「未定位」

⛔ 本脚本只打印，不写任何文件（纪律 34）。
⛔ 断段声明：`mw/` 已改 ⇒ 本轮的 Σ **不许**与 V28~V35 相减。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench/evidence/v6_s125"))

from probe_v35_blocks import ORDER, split_blocks      # noqa: E402
from mw.bench import caliber_from_dict                # noqa: E402

W = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v36")
HANDLE = re.compile(r"b#\d+")
PAIRS = [("cum_r1", "cum_r2"), ("rst_r1", "rst_r2"),
         ("cum_r1", "rst_r1"), ("cum_r2", "rst_r2")]


def load(tag: str) -> dict[tuple[str, int, int], str]:
    """键必须含**行序**：同一 `(task, it)` 会写多行（纪律 35）。"""
    p = W / f"states_{tag}.jsonl"
    out: dict[tuple[str, int, int], str] = {}
    if not p.exists():
        return out
    seen: dict[tuple[str, int], int] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        k2 = (str(d["task"]), int(d["it"]))
        idx = seen.get(k2, 0)
        seen[k2] = idx + 1
        out[(k2[0], k2[1], idx)] = str(d["state"])
    return out


def meta(tag: str) -> dict[tuple[str, int, int], dict]:
    p = W / f"states_{tag}.jsonl"
    out: dict[tuple[str, int, int], dict] = {}
    if not p.exists():
        return out
    seen: dict[tuple[str, int], int] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        k2 = (str(d["task"]), int(d["it"]))
        idx = seen.get(k2, 0)
        seen[k2] = idx + 1
        out[(k2[0], k2[1], idx)] = {"n": d.get("n"), "guidance": d.get("guidance")}
    return out


def blocks_compare(a: dict, b: dict) -> dict[str, dict[str, int]]:
    """逐块三分类，**并统计绝对长度**。

    ⛔ 为什么必须有 `nonempty` 和 `chars`（本轮差点自己骗自己）：
       两个空块 `"" == ""` 会被记成 `same`。若所有块都空，`same率 = 100%`
       读起来像「装置极度稳定」，实际是「这个块根本没出现过」——
       与纪律 19（判据的聚合方式会骗人）和「红=无」的第三种形态同源。
    """
    keys = sorted(set(a) & set(b))
    cnt: dict[str, dict[str, int]] = {}
    for k in keys:
        A, B = split_blocks(a[k]), split_blocks(b[k])
        for nm in ORDER:
            ta, tb = A.get(nm, ""), B.get(nm, "")
            c = cnt.setdefault(nm, {"same": 0, "handle": 0, "content": 0,
                                    "born": 0, "n": 0, "nonempty": 0, "chars": 0})
            c["n"] += 1
            if ta or tb:
                c["nonempty"] += 1
            c["chars"] += len(ta)
            if ta == tb:
                c["same"] += 1
            elif HANDLE.sub("b#N", ta) == HANDLE.sub("b#N", tb):
                c["handle"] += 1          # ⛔ 残留句柄 —— D1 不通过的直接证据
            else:
                c["content"] += 1
                if not ta and tb:
                    c["born"] += 1
    return cnt


def scores(tag: str) -> dict[str, float] | None:
    p = W / f"bench_{tag}/runs.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    if not rows:
        return None
    return {str(t["id"]): float(t.get("score") or 0.0)
            for t in rows[-1]["report"]["per_task"]}


def caliber(tag: str):
    p = W / f"bench_{tag}/runs.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    if not rows:
        return None
    return caliber_from_dict(rows[-1]["caliber"])


def main() -> int:
    print("=" * 84)
    print("V36 判据（预注册 bench/evidence/v6_s126/PREREG.md，冻结于 13:41:00）")
    print("=" * 84)

    data = {t: load(t) for t in ("cum_r1", "cum_r2", "rst_r1", "rst_r2")}
    for t, d in data.items():
        print(f"  {t:8s} state 行 = {len(d):3d}")

    # ── D1 与三个对照对 ───────────────────────────────────────────────
    print("\n" + "-" * 84)
    print("D1 / 块级稳定（每格 = 该块在多少个 (task,it) 上属于这一类）")
    print("-" * 84)
    verdict: dict[str, bool] = {}
    for a, b in PAIRS:
        if not data[a] or not data[b]:
            print(f"\n  {a} vs {b}：数据缺失，跳过")
            continue
        cnt = blocks_compare(data[a], data[b])
        keys = sorted(set(data[a]) & set(data[b]))
        print(f"\n  {a} vs {b}   （{len(keys)} 个 (task,it) 对上）")
        print(f"    {'块':<10s} {'same':>5s} {'handle':>7s} {'content':>8s} "
              f"{'born':>5s} {'same率':>7s} {'非空':>5s} {'平均字':>6s}")
        for nm in ORDER:
            c = cnt.get(nm)
            if not c or not c["n"]:
                continue
            r = c["same"] / c["n"]
            flag = "" if c["handle"] == 0 else "   ⛔残留句柄"
            if c["nonempty"] == 0:
                flag += "   ⚠️两边都空，same率无信息"
            print(f"    {nm:<10s} {c['same']:>5d} {c['handle']:>7d} "
                  f"{c['content']:>8d} {c['born']:>5d} {r:>6.0%} "
                  f"{c['nonempty']:>5d} {c['chars'] // max(1, c['n']):>6d}{flag}")
        wm = cnt.get("[工作记忆]", {"same": 0, "n": 0, "handle": 0})
        if a.startswith("cum") and b.startswith("cum"):
            verdict["D1"] = wm["n"] > 0 and wm["same"] == wm["n"]
        if a.startswith("rst") and b.startswith("rst"):
            verdict["POSCTL"] = wm["n"] > 0 and wm["same"] == wm["n"]

    # ── D2 分数：必须逐格 ─────────────────────────────────────────────
    print("\n" + "-" * 84)
    print("D2 / 分数（⛔ 逐格，不许只报 Σ）")
    print("-" * 84)
    sc = {t: scores(t) for t in ("cum_r1", "cum_r2", "rst_r1", "rst_r2")}
    tasks = ["e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"]
    print(f"    {'题':<8s} {'cum_r1':>8s} {'cum_r2':>8s} {'rst_r1':>8s} "
          f"{'rst_r2':>8s}   Σcum  Σrst")
    for tk in tasks:
        vals = []
        for t in ("cum_r1", "cum_r2", "rst_r1", "rst_r2"):
            v = sc[t].get(tk) if sc[t] else None
            vals.append("—" if v is None else f"{v * 16:.0f}")
        print(f"    {tk:<8s} {vals[0]:>8s} {vals[1]:>8s} {vals[2]:>8s} {vals[3]:>8s}")
    for tag in ("cum_r1", "cum_r2", "rst_r1", "rst_r2"):
        if sc[tag]:
            s = sum(sc[tag].values())
            print(f"    Σ {tag:8s} = {s * 16:.0f}/96   逐题="
                  f"{[f'{sc[tag][k] * 16:.0f}' for k in tasks]}")

    # ── D3 口径 ──────────────────────────────────────────────────────
    print("\n" + "-" * 84)
    print("D3 / 口径：diff_except({\"memory_seed\"}) 必须为空")
    print("-" * 84)
    cal = {t: caliber(t) for t in ("cum_r1", "cum_r2", "rst_r1", "rst_r2")}
    for a, b in PAIRS:
        if not (cal.get(a) and cal.get(b)):
            continue
        unexpected, declared = cal[a].diff_except(cal[b], {"memory_seed"})
        print(f"  {a} vs {b}: 意外差异={unexpected or '无'}  已声明={declared or '无'}")
        if a.startswith("cum") and b.startswith("cum"):
            verdict["D3"] = not unexpected

    # ── D4 guidance 有消费者吗 ───────────────────────────────────────
    print("\n" + "-" * 84)
    print("D4 / `guidance` 是否真的进入了 prompt（V35 那 488 字节的嫌疑人）")
    print("-" * 84)
    for tag in ("cum_r1", "cum_r2", "rst_r1", "rst_r2"):
        m = meta(tag)
        if not m:
            continue
        nonempty = [v["guidance"] for v in m.values() if (v["guidance"] or "").strip()]
        uniq = {str(x) for x in nonempty}
        ns = [v["n"] for v in m.values()]
        print(f"  {tag:8s} 行={len(m):3d} 非空 guidance={len(nonempty):3d} "
              f"不同取值={len(uniq):3d}  n 取值={sorted({x for x in ns if x is not None})}")
        for g in list(uniq)[:2]:
            print(f"      「{g[:70]}」")
    if verdict.get("POSCTL") is False:
        verdict["D4"] = None
        print("\n  ⚠️ 正控（重置臂两轮）没有逐字节相同 ⇒ guidance 的跨轮比较**无意义**，"
              "本轮不给 D4 判决。")

    # ── 结论 ─────────────────────────────────────────────────────────
    print("\n" + "=" * 84)
    for k in ("POSCTL", "D1", "D3"):
        if k in verdict and verdict[k] is not None:
            print(f"  {k:8s} = {'通过' if verdict[k] else '不通过'}")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

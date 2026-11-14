#!/usr/bin/env python3
"""V100 读数：闭环轨迹 + 五条判据（按 PREREG 逐字）。"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

TASKS = ["g7d2_dedupe_keep", "g7d3_fmt_amount", "g7e1_parse_flag", "g7e2_truncate_tail"]
SHORT = {t: t.split("_")[0] for t in TASKS}
KNOWN = {"075b4099": 0.4, "8089eee6": 1.0, "3e033a96": 0.6,
         "8f2e966c": 0.0, "5755e033": 0.6, "419ec792": 0.4}


def answers(d: pathlib.Path) -> dict[str, str]:
    """⭐ 兼容两种取法：<run>/bench/answers.jsonl（原跑目录）、
    <arch>/answers.jsonl（归档副本，`/tmp` 清掉后仍可复核）。"""
    cands = [d / "bench" / "answers.jsonl", d / "answers.jsonl",
             d.parent / "answers" / d.name / "answers.jsonl"]
    p = next((c for c in cands if c.exists()), None)
    if p is None:
        return {}
    out = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("kind") != "answer":
            continue
        out[r["task_id"]] = r.get("answer") or ""
    return out


def reconcile(traj: list) -> None:
    """§6：PREREG §4 的三条预期逐条对账（含「数对上了但理由错了」）。"""
    print("\n## §6 与 PREREG §4 的对账")
    for r in traj:
        if "candidates" not in r:
            continue
        cands = r["candidates"]
        hit = [c for c in cands if c["score"]["g7d3_fmt_amount"] >= 0.999]
        down = [c for c in cands if c["score"]["g7e2_truncate_tail"] <= 0.2]
        print(f"  r{r['round']}：mean 保持 0.85 ⇒ {'是' if not r['accepted'] else '否'}"
              f"　踩到 g7d3 满分盆 {len(hit)}/{len(cands)}"
              f"　g7e2 被打到 0.2 的 {len(down)}/{len(cands)}")
    print("  ⇒ r1 押『保持 0.85』数对了，但**理由错了**：不是没踩到盆，是踩到了又被连带损伤拉下来"
          "（纪律 10：数值对得上 ≠ 解释对）")


def cross() -> None:
    """§5：V99（人手枚举 9 臂）与 V100（脚本枚举 12 候选）里**同一个编辑**的答案比对。

    V99 的枚举顺序是 O0..O8，V100 是按 (事实, 位置) 排序 —— 两条路径互不相干。
    若四个 task 的答案**全文** sha256 全等 ⇒ 答案只由种子字节决定，
    **跨轮/跨进程/跨日期**成立（V97 只验到「同一轮内两跑」）。
    """
    here = pathlib.Path(__file__).resolve().parent
    old = here.parent / "v6_s188" / "arms"
    if not old.exists():
        print("\n## §5 跨轮逐字节复现　⚠️ 找不到 v6_s188/arms ⇒ 跳过")
        return
    print("\n## §5 跨轮逐字节复现（V99 人手 vs V100 脚本，同一编辑）")
    pairs = [("O1", "ZK2#39 。→.", "r3_3"), ("O6", "ZK2#18 ，→,", "r3_0"),
             ("O8", "ZK2#34 、→,", "r3_2")]
    for o, desc, new in pairs:
        a = answers(old / o)
        b = answers(here / "answers" / new)
        if not a or not b:
            print(f"  {desc}: 缺答案 ⇒ 跳过")
            continue
        same = [t for t in TASKS
                if hashlib.sha256((a.get(t) or "").encode()).hexdigest()
                == hashlib.sha256((b.get(t) or "").encode()).hexdigest()]
        print(f"  V99 {o} ≡ V100 {new}　{desc}　四题逐字节相同 {len(same)}/{len(TASKS)}"
              + ("　✓ 答案只由种子字节决定" if len(same) == len(TASKS) else "　⛔ 有分歧"))


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v100")
    traj = json.loads((root / "trajectory.json").read_text(encoding="utf-8"))

    print("## §1 轨迹")
    prev = None
    for r in traj:
        if "candidates" not in r:
            sc = r["score"]
            print(f"  r{r['round']} 基线  mean={r['mean']:.4f}  "
                  + "  ".join(f"{SHORT[t]}={sc[t]:.1f}" for t in TASKS))
            prev = r["mean"]
            continue
        print(f"  r{r['round']} 候选：")
        for c in r["candidates"]:
            tag, i, o, n = c["edit"]
            print(f"     {c['tag']:6s} {tag}#{i:2d} {o}→{n}  mean={c['mean']:.4f}  "
                  + "  ".join(f"{SHORT[t]}={c['score'][t]:.1f}" for t in TASKS))
        arrow = "↑ **写回**" if r["accepted"] else "＝ 保持"
        print(f"     ⇒ 选 {r['picked']}（{max(c['mean'] for c in r['candidates']):.4f}）{arrow}"
              f" ⇒ 当前 {r['mean']:.4f}")
        prev = r["mean"]

    print("\n## §2 判据（按 PREREG 原文）")
    base = traj[0]
    h0 = hashlib.sha256((answers(root / "r0").get("g7d3_fmt_amount") or "").encode()).hexdigest()[:8]
    ok0 = abs(base["mean"] - 0.85) < 1e-9 and h0 == "075b4099"
    print(f"  R0 基线 mean=0.85 且指纹 075b4099：{'✓ 装置没漂' if ok0 else '⛔ ⇒ UNKNOWN'}（{h0}）")

    means = [r["mean"] for r in traj]
    mono = all(b >= a - 1e-9 for a, b in zip(means, means[1:]))
    print(f"  R1 轨迹单调不减 {['%.2f' % m for m in means]}：{'✓' if mono else '⛔ 写回有 bug'}")

    # R2：同盆必同分（全部库）
    by = {}
    for r in traj:
        tags = ["r0"] if "candidates" not in r else [c["tag"] for c in r["candidates"]]
        for tg in tags:
            A = answers(root / tg)
            if not A:
                continue
            h = hashlib.sha256(A["g7d3_fmt_amount"].encode()).hexdigest()[:8]
            sc = None
            if "candidates" in r:
                sc = next(c["score"]["g7d3_fmt_amount"] for c in r["candidates"] if c["tag"] == tg)
            else:
                sc = r["score"]["g7d3_fmt_amount"]
            by.setdefault(h, set()).add(sc)
    bad = {h: s for h, s in by.items() if len(s) > 1}
    print(f"  R2 同盆必同分（{len(by)} 个盆 / "
          f"{sum(1 for r in traj if 'candidates' in r for _ in r['candidates']) + 1} 个库）："
          f"{'✓ 成立' if not bad else f'⛔ 破 {bad}'}")
    for h, ss in sorted(by.items(), key=lambda kv: -list(kv[1])[0]):
        print(f"     {h}  分={sorted(ss)}  {'已知' if h in KNOWN else '**新**'}")

    ups = [r for r in traj if r.get("accepted") and "candidates" in r]
    print(f"  R3 三轮内发生『变好』：{len(ups)} 次"
          f"{'✓' if ups else '⛔ 未中'}"
          + (f"（r{ups[0]['round']} ⇒ {ups[0]['mean']:.2f}）" if ups else ""))
    final = traj[-1]["mean"]
    print("  R4 最终 mean={:.2f}".format(final)
          + ("　⚠️ 报告里必须写明：这是同一套题上的**过拟合**，不是泛化" if final >= 0.999 else ""))

    # §2b 扩展读数（**非预注册**）：四题各自的盆，用来看 R2 之外还剩多少结构
    print("\n## §2b 扩展读数（非预注册）：四题各自的盆")
    per = {t: {} for t in TASKS}
    for r in traj:
        tags = ["r0"] if "candidates" not in r else [c["tag"] for c in r["candidates"]]
        for tg in tags:
            A = answers(root / tg)
            if not A:
                continue
            sc = r["score"] if "candidates" not in r else \
                next(c["score"] for c in r["candidates"] if c["tag"] == tg)
            for t in TASKS:
                h = hashlib.sha256((A.get(t) or "").encode()).hexdigest()[:8]
                per[t].setdefault(h, set()).add(sc[t])
    for t in TASKS:
        desc = "  ".join(f"{h}:{sorted(v)}" for h, v in sorted(per[t].items(), key=lambda kv: -max(kv[1])))
        print(f"  {SHORT[t]:5s} {len(per[t])} 盆　{desc}")
    top = {t: sum(1 for v in per[t].values() if max(v) >= 0.999) for t in TASKS}
    print(f"  满分盆的**个数**：" + "  ".join(f"{SHORT[t]}={top[t]}" for t in TASKS)
          + "　⇒ >1 表示『满分也不是一个实现』")

    print("\n## §3 被『总分』拒掉的那一格")
    for r in traj:
        if "candidates" not in r:
            continue
        for c in r["candidates"]:
            if c["score"]["g7d3_fmt_amount"] == 1.0 and c["mean"] < r["mean"]:
                print(f"  {c['tag']} {c['edit'][0]}#{c['edit'][1]} {c['edit'][2]}→{c['edit'][3]}："
                      f"g7d3 **1.0**（目标格满分）但 g7e2={c['score']['g7e2_truncate_tail']:.1f}"
                      f" ⇒ mean={c['mean']:.2f} 被拒")
    print("\n## §4 最终状态")
    print("  edits =", traj[-1]["edits"])
    print(f"  最终四格 = " + "  ".join(f"{SHORT[t]}={traj[-1]['score'][t]:.1f}" for t in TASKS))

    cross()
    reconcile(traj)


if __name__ == "__main__":
    main()

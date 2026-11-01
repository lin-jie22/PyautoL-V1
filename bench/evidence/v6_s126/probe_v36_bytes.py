"""V36 附加探针：`state` 逐字节相同 ⇒ `prompt` 是否也逐字节相同？

为什么必须补这一条
------------------
V34 已经立了纪律 43：**`state` 不是 `prompt` 的身份**。D1 只证到 `_state()` 的产物稳定，
而真正发给模型的是 `Planner` 拼的 messages —— `guidance` / `n` / `blocked` / `rejected`
都不在 `state` 里。

本轮 M4/M5 已经把这四样里的两样（`n`、`guidance`）落进了 dump，但**它们不等于请求字节**。
唯一能证明「prompt 相同」的东西是代理台账里的 `req_sha`（请求体的 sha）。
⇒ 本探针按时间窗切出四轮，逐条比 `req_sha` / `req_bytes`。

三种结果的含义（预注册之外的附加判据，**不参与 D1/D2/D3 判决**）：
  · `req_sha` 全同           ⇒ 本轮"累积臂与重置臂的 prompt 逐字节相同"**成立**，
                              那 0 差异不再是"哪一层抵消"的问题，是**根本同一个输入**；
  · `req_sha` 有异、state 同 ⇒ V35 那 488 字节**确实在 state 之外**，本轮给它记上主人；
  · `req_sha` 有异且差异大   ⇒ 需要在 RESULTS 里逐条点名，不许概括成"噪声"。

⛔ 只打印，不写文件（纪律 34）。
⛔ 台账的 `n` 是**行计数器**不是候选数（V35 纪律 46）—— 本探针不用它。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

W = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v36")
TAGS = ("cum_r1", "cum_r2", "rst_r1", "rst_r2")


def window(tag: str) -> tuple[int, int] | None:
    p = W / f"time_{tag}.txt"
    if not p.exists():
        return None
    nums = [x for x in p.read_text().split() if x.lstrip("-").isdigit()]
    if len(nums) < 2:
        return None
    return int(nums[0]), int(nums[1])


def rows() -> list[dict]:
    p = W / "proxy.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    all_rows = rows()
    print("=" * 88)
    print("V36 附加：state 相同 ⇒ prompt 相同？（唯一身份 = proxy 的 req_sha）")
    print("=" * 88)
    print(f"proxy.jsonl 共 {len(all_rows)} 行")

    cut: dict[str, list[dict]] = {}
    for t in TAGS:
        w = window(t)
        if not w:
            print(f"  ⚠️ {t}: 时间窗缺失")
            cut[t] = []
            continue
        cut[t] = [r for r in all_rows if w[0] <= r["t_wall"] <= w[1]]
        print(f"  {t:8s} 窗=({w[0]},{w[1]}) 条数={len(cut[t]):3d}")

    for a, b in (("cum_r1", "cum_r2"), ("rst_r1", "rst_r2"),
                 ("cum_r1", "rst_r1"), ("cum_r2", "rst_r2")):
        A, B = cut.get(a) or [], cut.get(b) or []
        if not A or not B:
            continue
        n = min(len(A), len(B))
        same_sha = sum(1 for i in range(n) if A[i]["req_sha"] == B[i]["req_sha"])
        deltas = [B[i]["req_bytes"] - A[i]["req_bytes"] for i in range(n)
                  if A[i]["req_sha"] != B[i]["req_sha"]]
        print(f"\n  {a} vs {b}：逐条对齐 {n} 对")
        print(f"    req_sha 相同 {same_sha}/{n}  "
              f"（不同 {n - same_sha} 条；字节差 {sorted(set(deltas))[:8] or '无'}）")
        if len(A) != len(B):
            print(f"    ⚠️ 条数不等（{len(A)} vs {len(B)}）⇒ 后面的对可能错位，"
                  f"diff 只作参考")
        # 逐条点名**全部**不同的（⛔ 不许概括成"噪声"；V35 就栽在只报个数上）
        shown = 0
        for i in range(n):
            if A[i]["req_sha"] != B[i]["req_sha"]:
                print(f"      #{i:2d}: {A[i]['req_sha'][:10]}/{A[i]['req_bytes']:5d}B "
                      f"→ {B[i]['req_sha'][:10]}/{B[i]['req_bytes']:5d}B "
                      f"Δ={B[i]['req_bytes'] - A[i]['req_bytes']:+5d}  "
                      f"t={A[i]['t_wall']:.0f}")
                shown += 1
        if shown:
            print(f"    ⇒ 共 {shown} 条不同，累计字节差 "
                  f"{sum(B[i]['req_bytes'] - A[i]['req_bytes'] for i in range(n) if A[i]['req_sha'] != B[i]['req_sha']):+d}")

    print("\n" + "-" * 88)
    print("判读口径（附加，不改 D1/D2/D3）：")
    print("  · 若「同臂跨轮」req_sha 全同 ⇒ 那一臂的两轮是**同一个 prompt 的两发重放**，")
    print("    按 V34，答案必然相同；那 0 差异的归因是「输入相同」，不是「记忆没用」。")
    print("  · 若「跨臂同轮」req_sha 全同 ⇒ 累积与重置两臂的输入逐字节相同，")
    print("    这才是本轮主结论的**最强形态**：累积的记忆一个字节都没进 prompt。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

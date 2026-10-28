#!/usr/bin/env python3
"""打回日志探针（第 106 轮 §76）：**机制自己记下的那一刻**，有多少次交付「差一个名字」。

## 为什么不用 `answers.jsonl` 当"首答"

预注册 §6 原本写的是「`nc` 组**首答（反馈前）**里含未定义名的题数」。
开工后核对台账结构发现这个取证**根本不存在**：

    §74 的 `a1_v4/answers.jsonl` = 24 行 = 24 次尝试（8 题 × 3 rep）
    ⇒ 台账**只存每次尝试的最终交付**，一次尝试内部的多次模型调用（含打回后的重写）
      只留下**最后一份**。

所以"反馈前的第一次交付"在台账里没有独立记录。若硬拿它算，`=1` 臂的首答
已经吃过自己的打回 ⇒ 机会被系统性**低估**，而低估的幅度恰好是机制的效应
⇒ 那正是 §75 之后最该避免的「把机制效应算进机会」。

## 改用这一份：打回日志

`run.log` 里机制被触发的**那一刻**是独立记录的，格式固定：

    🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：1/5 项未过：未定义的名字 'os'（在 ... 内被引用，…）
    🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：1/5 项未过：自检未通过（不提供细节）      ← =2 臂

它比台账**更接近"第一次交付"**：被打回的就是那一次交付。于是定义

    机会(题) = 该题**至少有一次**交付被判「未定义名」
    机会(尺) = 机会为真的题数 / 题数

⚠️ 这条定义在 `=2` 臂上**看不见名字**（盲报的载荷就是不给细节），
   所以：`=1` 臂给「名字归因」，两臂都给「触发题数」。两臂触发题数相同
   ⇒ 触发集等价（§73 的等长安慰剂臂正是靠这一点成立的）。

用法：
    python3 bench/evidence/v3_s106/probe_gate_hits.py --log /tmp/v1_s106_run_a1_v5.log
    python3 bench/evidence/v3_s106/probe_gate_hits.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_BLOCK = "▶ 任务开始"
_REJ = "自检未过"
_NAME = re.compile(r"未定义的名字 '([^']+)'")
_FN = re.compile(r"def\s+(\w+)\s*\(")
NC = ("nc01", "nc02", "nc03")
CT = ("ct01", "ct02", "ct03")
_FN2ID = {
    "scrub_tags": "nc01", "save_counts": "nc02", "span_days": "nc03",
    "write_ranked": "ct01", "write_cfg": "ct02", "write_split": "ct03",
}


def parse(log_path: str) -> dict:
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in Path(log_path).read_text(errors="replace").splitlines():
        if _BLOCK in line:
            if cur:
                blocks.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)

    per: dict[str, dict] = {}
    for b in blocks:
        head = b[0]
        m = _FN.search(head)
        tid = _FN2ID.get(m.group(1), m.group(1)) if m else "?"
        rej = 0
        names: list[str] = []
        for line in b:
            if _REJ in line:
                rej += 1
                names.extend(_NAME.findall(line))
        d = per.setdefault(tid, {"attempts": 0, "hit_attempts": 0,
                                 "rejections": 0, "names": {}})
        d["attempts"] += 1
        d["rejections"] += rej
        if rej:
            d["hit_attempts"] += 1
        for nm in names:
            d["names"][nm] = d["names"].get(nm, 0) + 1
    return per


def report(per: dict) -> dict:
    nc_hit = [t for t in NC if per.get(t, {}).get("hit_attempts", 0)]
    ct_hit = [t for t in CT if per.get(t, {}).get("hit_attempts", 0)]
    return {
        "nc_hit": nc_hit, "nc_n": len(nc_hit),
        "ct_hit": ct_hit, "ct_n": len(ct_hit),
        "attempts": sum(d["attempts"] for d in per.values()),
        "rejections": sum(d["rejections"] for d in per.values()),
        "per": per,
    }


def selftest() -> int:
    """反证：构一份真格式的假日志，探针必须分别判「有名字」和「没名字」。"""
    fake = """▶ 任务开始: 写一个函数 `scrub_tags`，函数签名**必须**是 `def scrub_tags(rows, path)`
12:00:01   [1] 通道=深 候选=1
12:00:02       🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：1/5 项未过：未定义的名字 're'（在 scrub_tags 内被引用，模块里既没有 import 也没有赋值）
12:00:03   ✅ 收尾: ...
▶ 任务开始: 写一个函数 `save_counts`，函数签名**必须**是 `def save_counts(rows, path)`
12:01:00   [1] 通道=深 候选=1
12:01:02   ✅ 收尾: ...
▶ 任务开始: 写一个函数 `write_cfg`，函数签名**必须**是 `def write_cfg(a, b, path)`
12:02:00   [1] 通道=深 候选=1
12:02:02       🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：1/5 项未过：自检未通过（不提供细节）
12:02:03   ✅ 收尾: ...
"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "run.log"
        p.write_text(fake, encoding="utf-8")
        rep = report(parse(str(p)))
    cases = [
        ("S1 有名字的打回必须归到 nc01 且名字是 re",
         rep["nc_hit"] == ["nc01"] and rep["per"]["nc01"]["names"] == {"re": 1},
         rep["per"].get("nc01")),
        ("S2 没打回的 save_counts 不许被计入",
         "nc02" not in rep["nc_hit"], rep["nc_hit"]),
        ("S3 盲报的打回算「触发」但**不给名字**",
         rep["ct_hit"] == ["ct02"] and rep["per"]["ct02"]["names"] == {},
         rep["per"].get("ct02")),
        ("S4 尝试数按块计数 = 3", rep["attempts"] == 3, rep["attempts"]),
    ]
    bad = 0
    for why, ok, got in cases:
        print(f"  {'✅' if ok else '❌'} {why}  实测={got}")
        bad += 0 if ok else 1
    print(f"{'✅ 自证 4/4' if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.log:
        print("缺 --log（或 --selftest）", file=sys.stderr)
        return 2
    rep = report(parse(a.log))
    print(f"  {'题':6s} {'组':4s} {'尝试':>4s} {'触发尝试':>8s} {'打回次数':>8s} 名字")
    for tid in NC + CT:
        d = rep["per"].get(tid)
        if not d:
            print(f"  {tid:6s} {'nc' if tid in NC else 'ct':4s}    —        —        —  （无记录）")
            continue
        print(f"  {tid:6s} {'nc' if tid in NC else 'ct':4s} {d['attempts']:4d} "
              f"{d['hit_attempts']:8d} {d['rejections']:8d} {d['names'] or '—'}")
    other = [t for t in rep["per"] if t not in NC + CT]
    for tid in sorted(other):
        d = rep["per"][tid]
        print(f"  {tid:6s} {'其他':4s} {d['attempts']:4d} "
              f"{d['hit_attempts']:8d} {d['rejections']:8d} {d['names'] or '—'}"
              f"   ← 不属本轮的组，只为交叉核对")
    print(f"nc 组触发过的题：{rep['nc_n']}/3 {rep['nc_hit']}")
    print(f"ct 组触发过的题：{rep['ct_n']}/3 {rep['ct_hit']}")
    print(f"总尝试 {rep['attempts']} 次 / 打回 {rep['rejections']} 次")
    print(f"D0_N={rep['nc_n']}")
    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""V204 判定：把人工 β 判读固化成数据，算两个口径的条数。

口径一（文本条数）：产出里有几句够得上「可迁移」。
口径二（**不同知识条数**）：同一件事的两种写法算 1 条。⭐ 主口径。

⛔ 判读标准（预注册 §2）：点名了具体的输入形态/约束，使模型能做出一处具体的代码决定。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent

#: 人工判读结果：text -> (是否可迁移, 知识簇名或 None)
#: ⭐ 判读时先打乱臂序（list_v204.py 已按 md5 排序），此处只记录结论。
JUDGE: dict[str, tuple[bool, str | None]] = {
    # ---- A/C 共有的两条：讲同一件事（保序）⇒ 同一个知识簇 ----
    "遇到重复字符串时，先确定保留原文的出现顺序。": (True, "去重要保序"),
    "遇见到重复项处理规则不明确时，先明确保留哪次出现的原文。": (True, "去重要保序"),
    # ---- B/D 共有的两条：两件不同的事 ----
    "空 dict 返回空串而非 None": (True, "空容器返回什么"),
    "括号内逗号不算分隔符": (True, "分割要跳过括号内的分隔符"),
    # ---- 其余：无信息（流程句 / 语法常识 / 空话）----
}

SELF = re.compile(
    r"交付前检查|自检|预演|停滞|模型调用失败|编译|通信|外部 agent|重构|提交|"
    r"函数定义完整|函数实现|依赖函数|必需函数|基础函数|功能实现|"
    r"补充缺失信息|资源|网络连接|输入数据是否|当前网络|语言标签|题目要求的语言"
)


def main() -> int:
    d = json.loads((OUT / "readings_v204.json").read_text(encoding="utf-8"))
    rows = {k: (d[k].get("texts") or []) for k in ("A", "B", "C", "D")}

    print(f"{'臂':<3}{'总条数':>7}{'强(文本)':>9}{'强(知识簇)':>11}"
          f"{'浓度':>8}{'自指':>7}{'自指率':>8}  说明")
    stat = {}
    for k in ("A", "B", "C", "D"):
        ts = rows[k]
        strong_txt = [t for t in ts if JUDGE.get(t, (False, None))[0]]
        clusters = {JUDGE[t][1] for t in strong_txt if JUDGE[t][1]}
        selfn = sum(1 for t in ts if SELF.search(t))
        stat[k] = {"n": len(ts), "strong_text": len(strong_txt),
                   "strong_cluster": len(clusters),
                   "density": round(len(strong_txt) / max(1, len(ts)), 4),
                   "self": selfn, "self_rate": round(selfn / max(1, len(ts)), 4),
                   "clusters": sorted(clusters)}
        print(f"{k:<3}{len(ts):>7}{len(strong_txt):>9}{len(clusters):>11}"
              f"{len(strong_txt)/max(1,len(ts)):>8.1%}{selfn:>7}"
              f"{selfn/max(1,len(ts)):>8.1%}  {d[k]['tag']}")

    # 未被判读覆盖的条目数（防止「没判 = 默认不可迁移」被藏起来）
    unjudged = [t for k in ("A", "B", "C", "D") for t in rows[k] if t not in JUDGE]
    print(f"\n未显式判读的条目：{len(unjudged)}（全部按「不可迁移」处理，逐条可见于 pool_v204.json）")

    # 交叉：哪些臂产出了哪簇
    print("\n知识簇 × 臂：")
    allc = sorted({c for s in stat.values() for c in s["clusters"]})
    for c in allc:
        have = [k for k in ("A", "B", "C", "D") if c in stat[k]["clusters"]]
        print(f"  {c:24s} {'/'.join(have)}")

    # NONE 率
    print("\nNONE 率：", {k: (round(d[k]["reasons"].get("NONE", 0)
                                 / max(1, d[k]["n_eps"]), 4)
                            if d[k].get("reasons") else None)
                          for k in ("A", "B", "C", "D")})

    print("\n每条原料产出率（kept/n_eps）：",
          {k: round(d[k]["n_kept"] / max(1, d[k]["n_eps"]), 4) for k in ("A", "B", "C", "D")})

    (OUT / "judged_v204.json").write_text(
        json.dumps({"stat": stat, "n_unjudged": len(unjudged),
                    "judge": {k: list(v) for k, v in JUDGE.items()}},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT/'judged_v204.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""§17-7j 的**题面派生器**：给四道 heldout 的 prompt 加 ASCII 钥匙。

为什么需要它（这是 7i 量出来的事实，不是设计出来的）：
    `_TOKEN = [A-Za-z0-9_\\u4e00-\\u9fff]+` 把**整段连续中文当一个 token**，
    而 FTS5 找的是**完全相同的串**。7e/7f 的四道题面里：
      · `g7d2`/`g7d3` 与惯例的中文 token **零交集** ⇒ 命中 0 条；
      · `g7e1` 靠 ASCII 的 `None`、`g7e2` 靠 `n` + 恰好逐字相同的
        `不超长时原样返回` ⇒ 命中 1 条。
    ⇒ 7d 想测的"题面不完备"，在中文上**从来没被真正测过**：要么零命中，
       要么命中的是 ASCII 而不是判别条件。

做法（装置修正⑯）：在题面的"按记忆里记着的项目 XX 惯例处理"处，
    换成 `（ZK1）`…`（ZK4）` —— **ASCII**，被全角括号隔开 ⇒ 独立 token。

⛔ 为什么必须是 ASCII：`build_placebo` 只打散汉字、**原位保留 ASCII**。
   若钥匙用汉字，P 臂（骨架安慰剂）的钥匙会被打散 ⇒ 两臂检索行为不同
   ⇒ 注入条数/字符数不等，R7 的"等长"在注入层就破了（7d 装置修正⑨）。

⛔ 钥匙**不含判别信息**：判别条件（去空白/转小写/保留第一次…）仍在惯例正文里，
   钥匙只负责"让该送达的那条能被检索到"，与 7d 手写惯例的初衷一致。

用法：
    python3 bench/build_g7j_heldout.py --hits n0     # 每题只提自己那把钥匙
    python3 bench/build_g7j_heldout.py --hits n4     # 每题提全部四把
    python3 bench/build_g7j_heldout.py --hits n4 --check   # 只打印不落盘
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_BENCH = Path(__file__).resolve().parent
_ROOT = _BENCH.parent
for _p in (str(_ROOT), str(_BENCH)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mw.memory import fts_query, _TOKEN          # noqa: E402

#: 题的**唯一事实源**：id → 钥匙，直接取自种子构建器（⛔ 不许在这里再抄一份
#: —— 两处声明一旦漂移，"题面提的钥匙"与"惯例里写的钥匙"就不再是同一个东西，
#: 而现象只是"命中少了几条"，看起来像 FTS 坏了）。
try:                                              # 被 `import bench.*` 时
    from bench.build_g7d_seed import KEYS_7J as KEYS
except ImportError:                               # 直接以脚本身份跑时
    from build_g7d_seed import KEYS_7J as KEYS

#: 要被替换的那一段。四题原文各不同（"项目去重惯例"/"项目金额惯例"/
#: "项目开关解析惯例"/"项目截断惯例"），所以用"项目…惯例处理"包住中段。
#: ⛔ 必须**恰好命中一次**：`subn` 的计数不为 1 就拒绝出文件，
#:    否则"加钥匙"这件事本身没有可复核的形状。
_SPOT = re.compile(r"按记忆里记着的项目[^（）]*?惯例处理")

#: `fts_query` 的窗口上限（`mw/memory.py` 里 `toks[:24]`）。
#: ⚠️ 钥匙必须全部落在这个窗口里，否则"题面提了钥匙"与"FTS 收到钥匙"不是一回事。
QUERY_WINDOW = 24


def keys_for(tid: str, hits: str) -> list[str]:
    """某一档位下，这道题的题面该提哪几把钥匙。"""
    if hits == "n0":
        return [KEYS[tid]]
    if hits == "n4":
        return [KEYS[k] for k in KEYS]          # 固定顺序，不依赖 dict 迭代顺序
    raise ValueError(f"未知档位 {hits!r}（只认 n0 / n4）")


def rewrite(prompt: str, tid: str, hits: str) -> str:
    """把题面里那一段换成钥匙串。**纯函数** —— 契约测试直接调它。

    ⚠️ 保留"按记忆里记着的"这半句：它是 7e 引入的**指向记忆**的措辞，
       7f/7g 的读数都建立在它上面。只替换"项目 XX 惯例"里的"项目 XX"。
    """
    mark = "".join(f"（{k}）" for k in keys_for(tid, hits))
    new, n = _SPOT.subn("按记忆里记着的" + mark + "惯例处理", prompt)
    if n != 1:
        raise ValueError(f"{tid}: 替换锚点出现 {n} 次（必须恰好 1 次）")
    return new


def query_tokens(prompt: str) -> list[str]:
    """`fts_query` 实际会送进 MATCH 的 token（已截到 24）—— 给验收用。"""
    q = fts_query(prompt)
    return re.findall(r'"([^"]*)"', q)


def check_windows(prompt: str, tid: str, hits: str) -> list[str]:
    """钥匙是否**全部**落在 24-token 窗口内。返回违规清单（空 = 通过）。"""
    bad: list[str] = []
    toks = query_tokens(prompt)
    total = len(_TOKEN.findall(prompt))
    for k in keys_for(tid, hits):
        if k not in toks:
            bad.append(f"{tid}/{hits}: 钥匙 {k} 不在 fts_query 窗口内"
                       f"（题面共 {total} 个 token，窗口 {QUERY_WINDOW}）")
    return bad


def build(heldout: list[dict], hits: str) -> list[dict]:
    """派生一份新 heldout：除 `prompt` 外的字段**一字不动**。"""
    out = []
    for t in heldout:
        tid = t["id"]
        if tid not in KEYS:
            raise ValueError(f"{tid}: 没有分配钥匙（四题必须一一对应）")
        d = dict(t)
        d["prompt"] = rewrite(t["prompt"], tid, hits)
        out.append(d)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--src", default="bench/heldout/g7e_hint_heldout.jsonl")
    p.add_argument("--out", default="")
    p.add_argument("--hits", default="n0", choices=("n0", "n4"))
    p.add_argument("--check", action="store_true",
                   help="只打印 token 窗口与替换结果，不写文件")
    args = p.parse_args(argv)

    src = Path(args.src)
    if not src.is_absolute():
        src = _ROOT / src
    heldout = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines()
               if l.strip()]

    try:
        rows = build(heldout, args.hits)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print(f"== 档位 {args.hits}（源：{src.name}，{len(rows)} 题）==")
    bad: list[str] = []
    for t in rows:
        toks = query_tokens(t["prompt"])
        n_all = len(_TOKEN.findall(t["prompt"]))
        bad += check_windows(t["prompt"], t["id"], args.hits)
        print(f"  · {t['id']:24s} 题面 token={n_all:2d}"
              f" 窗口内={len(toks):2d} 钥匙={keys_for(t['id'], args.hits)}")
        print(f"      {t['prompt']}")
    if bad:
        print("❌ 钥匙不在窗口内：")
        for b in bad:
            print(f"   · {b}")
        return 1

    if args.check:
        print("== 只检查，不落盘 ==")
        return 0

    out = Path(args.out) if args.out else \
        _ROOT / f"bench/heldout/g7j_{args.hits}_heldout.jsonl"
    out.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in rows) + "\n",
                   encoding="utf-8")
    print(f"⇒ 已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""V33 P0 —— 等长安慰剂臂的块，必须是「同样长、同样形状、只是不关于本题」

## 要证的三件事（全部零 LLM、零网络）

1. **形状**：三条臂在 `e1ni` 眼里的 `[外部评审]` 块，各自是哪三条；
2. **长度**：`c1`（`prev-self`）与 `P`（`prev` + 丢 `e2wi`）的块**字符数逐字节相同**
   —— 这是「长度中性」的全部意义。只比字数不算，要比整段 `len()`；
3. **隔离**：`--critique-drop` 只作用于**快照副本**，磁盘上的账本一条不少（前后 sha 相同）。

## 为什么必须调 `Core._state` 而不是自己算

`pick = [self_c[-1]] + others[-(3-1):] if self_c else crit[-3:]` 是**被测对象本身**。
自己抄一遍再断言「抄对了」，证明的是抄写正确（V31 自踩 ⑩ 的同宗）。
所以这里只做两件事：调 `run._critique_snapshot`（与 `cmd_bench` 同一份实现）、调 `Core._state`（与跑批同一份实现）。

用法：`probe_v33_block.py`（stdout）；不带参数，不改写任何证据文件（纪律 34）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="v33probe_"))
os.environ["V1_STATE_DIR"] = str(_TMP / "state")
os.environ["V1_LOG_DIR"] = str(_TMP / "logs")
os.environ["V1_STATE_CHARS"] = "2400"      # 与跑批同口径
os.environ.pop("V1_CRITIQUE_SCOPE", None)

sys.path.insert(0, "/Users/linshaojie/V1")

import run  # noqa: E402  （`_critique_snapshot` 的唯一实现）
from mw import config as cfg  # noqa: E402
from mw.core import Core  # noqa: E402
from mw.critique import CritiqueLedger  # noqa: E402
from mw.llm import StubLLM  # noqa: E402

SEED = Path("/Users/linshaojie/V1/bench/evidence/v6_s121/seed_base/critique.jsonl")
TASKS = ["e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"]
BLK = "[外部评审]"
#: ⚠️ `why` 文本自带换行（`import pathlib as _pp\nclean_lines(...`）⇒
#:   按 `splitlines()` 取块只会拿到**第一物理行**（实测 63/97 字，看着像被截断）。
#:   必须用与跑批（`run_v32.sh` 的 `BLK`）**同一条**正则切片。
import re  # noqa: E402

BLK_RE = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)


def sha16(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def block_of(led: CritiqueLedger | None, task: str, ss: str, us: str) -> str:
    """把 `task` 的 `[外部评审]` 块原样取出来（走内核，不重算）。"""
    core = Core(cfg.Settings(limits=cfg.Limits(max_steps=6)), llm=StubLLM(),
                critique_ledger=led)
    core.log = lambda _m: None
    st = core._state("目标", [], 1, task_id=task, scorer_sha=ss, suite_sha=us)
    core.memory.close()
    m = BLK_RE.search(st)
    return ("[外部评审] " + m.group(1)) if m else ""


def ids_in(blk: str) -> list[str]:
    """块里的 task_id，**按出现顺序**（不是按 TASKS 列表顺序 —— 那会骗自己）。"""
    return re.findall(r"\[tasks_v8/([a-z0-9]+)\]", blk)


def main() -> int:
    print("=" * 100)
    print("V33 P0 —— 等长安慰剂臂：块形状 / 块长度 / 快照隔离")
    print("=" * 100)
    print(f"种子 {SEED}")
    sha_before = sha16(SEED)

    led = CritiqueLedger(SEED)
    n = led.load()
    print(f"载入 {n} 条  sha16={sha_before}")
    rows = [json.loads(x) for x in SEED.read_text(encoding="utf-8").splitlines() if x.strip()]
    ss, us = rows[0]["scorer_sha"], rows[0]["suite_sha"]

    arms = [("c0", "prev", set(), "旧口径：同题被挡，窗口 = 字典序尾三"),
            ("c1", "prev-self", set(), "处理臂：本题那条占一个名额（V32）"),
            ("P", "prev", {"e2wi"}, "安慰剂：丢满分条目 ⇒ 窗口换成三条真实失败")]
    info: dict[str, dict[str, object]] = {}
    for tag, scope, drop, note in arms:
        snap = run._critique_snapshot(led, scope, drop)
        assert snap is not None, f"{tag} 快照为空"
        print()
        print("─" * 100)
        print(f"臂 {tag}  scope={scope}  drop={sorted(drop) or '-'}   快照 {len(snap.by_key)} 条   （{note}）")
        print("─" * 100)
        info[tag] = {}
        for t in TASKS:
            blk = block_of(snap, t, ss, us)
            ids = ids_in(blk)
            info[tag][t] = {"ids": ids, "len": len(blk), "blk": blk}
            print(f"  {t:5s} 条数={len(ids)} 块长={len(blk):3d}  {ids}")

    print()
    print("─" * 100)
    print("一、`e1ni` 眼里的三条（本轮唯一判据所在的那道题）")
    print("─" * 100)
    for tag in ("c0", "c1", "P"):
        d = info[tag]["e1ni"]
        print(f"  {tag:2s}  ids={d['ids']}  块长={d['len']}")
        print(f"      首条 = {d['blk'].split(' ｜ ')[0]!r}")

    print()
    print("─" * 100)
    print("二、长度中性：`c1` 与 `P` 的块必须**字符数完全相同**")
    print("─" * 100)
    l_c1, l_p = int(info["c1"]["e1ni"]["len"]), int(info["P"]["e1ni"]["len"])
    same_len = l_c1 == l_p
    print(f"  c1={l_c1}  P={l_p}  差={l_p - l_c1}  ⇒ {'✅ 等长' if same_len else '❌ 不等长'}")
    print(f"  （对照：c0={info['c0']['e1ni']['len']}，与 c1 差 {l_c1 - int(info['c0']['e1ni']['len'])}）")

    print()
    print("─" * 100)
    print("三、成分：c1 必须含自己的失败；P 必须**不含**自己、且含 e2ni 的真实失败")
    print("─" * 100)
    c1_ids = info["c1"]["e1ni"]["ids"]
    p_ids = info["P"]["e1ni"]["ids"]
    ok_self = c1_ids[:1] == ["e1ni"]
    ok_other = "e1ni" not in p_ids and "e2ni" in p_ids
    print(f"  c1 首条是 e1ni 自己 = {ok_self}    P 不含 e1ni 且含 e2ni = {ok_other}")
    tail_same = c1_ids[1:] == p_ids[1:] and len(c1_ids) == 3
    print(f"  c1 与 P 的**后两条按序相同** = {tail_same}   c1后两条={c1_ids[1:]}  P后两条={p_ids[1:]}")

    print()
    print("─" * 100)
    print("四、隔离：快照不能写回磁盘")
    print("─" * 100)
    sha_after = sha16(SEED)
    print(f"  种子 sha16 前={sha_before} 后={sha_after} ⇒ "
          f"{'✅ 未被改写' if sha_before == sha_after else '❌ 被快照写回'}")

    ok = same_len and ok_self and ok_other and tail_same and sha_before == sha_after
    print()
    print("=" * 100)
    print(f"VERDICT {'P0-OK' if ok else 'P0-FAIL'}")
    print("=" * 100)
    print("⛔ 本探针只证「块长得对」。它**不**回答「接通后有没有用」—— 那是跑批的事。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

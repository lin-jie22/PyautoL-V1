#!/usr/bin/env python3
"""V225 离线自测：`CritiqueLedger` 的「满分不覆盖」两臂必须**可区分**。

为什么要有这个文件：实跑只证明「这一轮账本没变」，不能证明「是冻结让它没变」。
唯一能把「冻结」与「没写」分开的办法，是**同一个写入在 on/off 两臂下产出不同字节**。
零 LLM、零网络，可直接复跑。

跑法：
    unset PYTHONPATH && python3 bench/evidence/l3_s302/selftest_v225.py
期望：末行 `SELFTEST OK`
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from mw.critique import Critique, CritiqueLedger      # noqa: E402

OLD = "OLD-TYPEERROR-LINE"
NEW = "NEW-PASS-LINE"


def mk(score: float, why: str) -> Critique:
    return Critique(scorer_sha="s", suite_sha="u", task_id="t",
                    family="f", score=score, why=why, answer="x")


def why_of(led: CritiqueLedger) -> str:
    return led.by_key[("s", "u", "t")].why


def main() -> int:
    d = tempfile.mkdtemp(prefix="v225_selftest_")
    p = os.path.join(d, "c.jsonl")

    seed = CritiqueLedger(p)
    seed.add(mk(0.6667, OLD))
    seed.save()

    on = CritiqueLedger(p, freeze_pass=True)
    on.load()
    on.add(mk(1.0, NEW))          # 满分 ⇒ 应冻结
    print(f"ON  满分写入后 why={why_of(on)!r} frozen={on.frozen}")
    assert why_of(on) == OLD, "满分被覆盖了：冻结没生效"
    assert on.frozen == 1, f"frozen 计数={on.frozen}"

    on.add(mk(0.6667, "4/6 fail"))  # 非满分 ⇒ 应覆盖
    print(f"ON  非满分写入后 why={why_of(on)!r} frozen={on.frozen} sup={on.superseded}")
    assert why_of(on) == "4/6 fail", "非满分没被覆盖：回归被吞掉了"

    off = CritiqueLedger(p)        # 默认 off
    off.load()
    off.add(mk(1.0, NEW))
    print(f"OFF 满分写入后 why={why_of(off)!r} frozen={off.frozen}")
    assert why_of(off) == NEW, "默认行为被改了（旧行为必须一字不变）"
    assert off.frozen == 0

    print("SELFTEST OK（两臂可区分：ON 保留旧行 / OFF 换成新行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

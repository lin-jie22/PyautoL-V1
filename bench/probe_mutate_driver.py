#!/usr/bin/env python3
"""驱动器自检：**零模型调用**，把 `bench/mutate_driver.py` 的每一格都过一遍。

## 为什么它必须存在

驱动器是"把变异真的推下去"的那一段。它自己错了，读数是**看起来正常**的错：
三个臂都跑完了、都有分数、都进了池子 —— 只是池子里混着别的口径 / 别的系统，
或者那个"变异"根本没进内核。**这种错不会让任何东西崩，只会让结论变假。**

所以这里每一项都配反证（喂坏样本必须命中），否则就是死代码。

## 它不做什么

⛔ 不跑模型、不写台账、不改 `runs.jsonl`。它只验证**纯函数**与**冻结证据的重算**。
真跑一圈的读数在 `bench/evidence/evolve_s76/`（见那里的 PROVENANCE）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mw.config import Limits, Settings                             # noqa: E402
from mw import mutate as MU                                        # noqa: E402
from bench import mutate_driver as MD                              # noqa: E402
from bench import noise_floor as NF                                # noqa: E402
from bench import mutation_check as MC                             # noqa: E402

_fail: list[str] = []


def ck(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _fail.append(label)


def main() -> int:
    S = Settings()
    base = S.limits.as_dict()

    # ── P1/P2/P3：变异必须先过内核的界 ────────────────────────────────────
    ok_c = MU.apply_mutation(S, "max_stall", 3)
    ok_p = MU.apply_mutation(S, "working_memory_items", 32)
    ck("P1 界内变异被接受（处理臂 + 安慰剂臂）", ok_c.ok and ok_p.ok,
       f"{ok_c.as_line()} ｜ {ok_p.as_line()}")
    ck("P2 越界被**拒绝**而不是钳制", not MU.apply_mutation(S, "max_stall", 99).ok,
       MU.apply_mutation(S, "max_stall", 99).why[:40])
    ck("P3 冻结旋钮被拒绝（`max_steps` 是预算总量，不许内核自己改分母）",
       not MU.apply_mutation(S, "max_steps", 13).ok,
       MU.apply_mutation(S, "max_steps", 13).why[:30])

    # ── P4/P5：env 是唯一通道，所以它必须被清干净 ─────────────────────────
    ck("P4 字段名 → 环境变量的映射唯一",
       MD.env_key("max_stall") == "V1_MAX_STALL"
       and MD.env_key("working_memory_items") == "V1_WORKING_MEMORY_ITEMS",
       f"{MD.env_key('max_stall')} / {MD.env_key('working_memory_items')}")

    fields = MD.fields_of_limits()
    os.environ["V1_MAX_STALL"] = "5"
    os.environ["V1_RECALL_K"] = "9"
    os.environ["V1_LLM_MODEL"] = "keep-me"
    e = MD.clean_env({"V1_MAX_STALL": "3"})
    # ⚠️ 这个数字是**故意的硬锚**：加 `Limits` 字段时它必然红，
    #    逼人回来确认「新字段确实进了清 env 的集合」。
    #    17 → 18（第八十轮加 `verify_static`）；
    #    18 → 19（第九十六轮加 `close_overrun_s`，§63）。这一次的复核结论：
    #    `clean_env` 走的是 `fields_of_limits()` **动态**取字段名 ⇒ 新字段
    #    自动进清理集合（左半边断言已通过，红的只有这个计数锚）——
    #    也就是说这条硬锚**干了它该干的事**：逼我确认一遍，而不是让我猜。
    #    不是为了让这条变绿而改的。
    ck("P5 ★`clean_env` 清掉**全部** `Limits` 字段的同名 env，但保住模型配置",
       e.get("V1_MAX_STALL") == "3" and "V1_RECALL_K" not in e
       and e.get("V1_LLM_MODEL") == "keep-me" and len(fields) == 19,
       f"limits 字段 {len(fields)} 个 ｜ V1_RECALL_K={'在' if 'V1_RECALL_K' in e else '已清'}")
    for k in ("V1_MAX_STALL", "V1_RECALL_K", "V1_LLM_MODEL"):
        os.environ.pop(k, None)

    # ── P6：守门员的"生效 + 单变量"，每格都喂一个坏样本 ────────────────────
    cand = MD.Arm("cand", {"V1_MAX_STALL": "3"}, {"max_stall": 3})
    plac = MD.Arm("placebo", {"V1_WORKING_MEMORY_ITEMS": "32"},
                  {"working_memory_items": 32})
    basearm = MD.Arm("base", {}, {})

    def rec(**over):
        lim = dict(base)
        lim["max_stall"] = 3
        lim.update(over)
        return {"caliber": {"limits": lim}, "run_id": "1@1"}

    def rejected(r, a=None):
        try:
            MD.check_record(r, a if a is not None else cand, base)
        except RuntimeError:
            return True
        return False

    dead = rejected(rec(max_stall=2))
    multi = rejected(rec(recall_k=8))
    norid = rejected({**rec(), "run_id": ""})
    nolim = rejected({"caliber": {}, "run_id": "1@1"})
    basehit = rejected({"caliber": {"limits": {**base, "max_stall": 3}}, "run_id": "1@1"},
                       basearm)
    twok = rejected(rec(), MD.Arm("x", {}, {"max_stall": 3, "recall_k": 8}))
    ck("P6 ★守门员：合法通过，五种坏样本全部拒绝（没生效/多变量/缺 run_id/缺 limits/base 有差异/声明多旋钮）",
       (not rejected(rec())) and dead and multi and norid and nolim and basehit and twok,
       f"通过={not rejected(rec())} 没生效={dead} 多变量={multi} "
       f"缺run_id={norid} 缺limits={nolim} base差异={basehit} 多旋钮={twok}")
    ck("P6b 安慰剂臂用它**自己**的旋钮判（第一版固传处理臂旋钮，它被自己判红）",
       not rejected(rec(max_stall=2, working_memory_items=32), plac))

    # ── P7：池子四重过滤 ─────────────────────────────────────────────────
    tmp = Path(tempfile.mkdtemp(prefix="v1_probe_md_")) / "runs.jsonl"
    kl = dict(base, max_stall=3)

    def row(sig, solver, lim, rid, mean=0.5):
        return json.dumps({"run_id": rid, "caliber_sig": sig, "solver": solver,
                           "caliber": {"limits": dict(lim)},
                           "measurement": {"mean": mean}})

    tmp.write_text("\n".join([
        row("AA", "内核", kl, "1@1"), row("AA", "内核", kl, "2@2"),
        row("AA", "内核", kl, "3@3"),
        row("AA", "裸模型", kl, "4@4"), row("AA", "裸模型", kl, "5@5"),
        row("BB", "内核", kl, "6@6"),
        row("AA", "内核", dict(base, max_stall=4), "7@7"),
        json.dumps({"caliber_sig": "AA", "solver": "内核",
                    "caliber": {"limits": kl}, "measurement": {"mean": 0.5}}),
    ]) + "\n", encoding="utf-8")
    keep, drop = MD.pool_from_ledger(tmp, arm=cand, base_limits=base,
                                     sig="AA", solver="内核")
    nosv, _ = MD.pool_from_ledger(tmp, arm=cand, base_limits=base,
                                  sig="AA", solver=None)
    ck("P7 ★池子四重过滤（口径/solver/limits/run_id）⇒ 只留 3 条正品",
       len(keep) == 3 and sorted(r["run_id"] for r in keep) == ["1@1", "2@2", "3@3"]
       and drop["no_run_id"] == 1,
       f"留 {len(keep)} 条 ｜ 拒收 {drop}")
    ck("P7b 反证：去掉 solver 过滤就混进另一个系统（5 条）—— "
       "`Caliber` 不含 solver ⇒ 同口径 ≠ 同系统",
       len(nosv) == 5, f"去掉后 {len(nosv)} 条")

    # ── P8：从冻结证据重算裁决（同一输入必须同一结论） ────────────────────
    ev = ROOT / "bench" / "evidence" / "evolve_s76"

    def obs(name):
        p = ev / f"pool_{name}.jsonl"
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [NF.from_record(r) for r in rows]

    ob = {k: obs(k) for k in ("base", "cand", "placebo")}
    fl = {k: NF.noise_for_verdict(v) for k, v in ob.items()}
    mean = {k: sum(o.mean for o in v) / len(v) for k, v in ob.items()}
    nf = max(v for v in fl.values())
    dlt = mean["cand"] - mean["base"]
    pdl = mean["placebo"] - mean["base"]
    v, why = MC.mutation_verdict(knob="max_stall", diff=["max_stall"],
                                 base_mean=mean["base"], cand_mean=mean["cand"],
                                 placebo_delta=pdl, noise_floor=nf)
    v0, _ = MC.mutation_verdict(knob="max_stall", diff=["max_stall"],
                                base_mean=mean["base"], cand_mean=mean["cand"],
                                placebo_delta=pdl, noise_floor=0.0)
    ck("P8 ★冻结证据重算 ⇒ 同一结论，且**噪声置 0 也不变**（判决不靠噪声口径撑着）",
       v == MC.NO_EVIDENCE and v0 == MC.NO_EVIDENCE
       and abs(dlt - pdl) < 1e-12 and fl["base"] >= 0.03,
       f"Δ={dlt:+.4f} 安慰剂Δ={pdl:+.4f} 噪声={nf:.4f} 裁决={v}/{v0}")
    ck("P8b 三臂各 5 次独立运行、口径互不相同（`limits` 进 sig ⇒ 改旋钮必换口径）",
       all(len(x) == 5 for x in ob.values())
       and len({next(iter({o.sig for o in x})) for x in ob.values()}) == 3,
       " ｜ ".join(f"{k}={len(x)} 次" for k, x in ob.items()))

    # ── P9：反证 —— 把那条"第一次运行"抹平，读数锚必须失效 ────────────────
    flat = [NF.Obs(arm="base", sig=o.sig, run_id=o.run_id, mean=0.5595,
                   repeat=o.repeat, per_task=o.per_task, rep_scores=o.rep_scores)
            for o in ob["base"]]
    ck("P9 反证：抹平 base 池的最低价 ⇒ 极差变 0（说明噪声真的是算出来的，不是常量）",
       fl["base"] > 0 and NF.noise_for_verdict(flat) == 0.0,
       f"原极差 {fl['base']:.4f} ⇒ 抹平后 {NF.noise_for_verdict(flat):.4f}")

    print()
    if _fail:
        print(f"❌ 驱动器自检失败 {len(_fail)} 项：{_fail}")
        return 1
    print("✅ 驱动器自检 11 项全过：变异 → 注入 → 守门 → 分池 → 噪声 → 裁决，"
          "每一格都配了反证")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

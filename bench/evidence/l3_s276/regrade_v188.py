"""V188 重判：**不跑模型**，用 V187 冻结的 24 格输出，在两个装置上各判一次。

    python3 bench/evidence/l3_s276/regrade_v188.py

为什么这样设计（PREREG_v188 §三）：
  · 模型侧输入逐字相同（`readings_v187.json` 的 kind/types/slots）
  · 老装置 = `git show d56230b:mw/{critgen,metacrit}.py`（本轮冻结，sha256 在 `snapshot.json`）
  · 新装置 = 当前 `mw/`
  ⇒ 任何差分 100% 归因于装置，不含采样噪声。

三条自检（不过就当场炸，⛔ 不许带着坏重放臂出读数）：
  I1 老装置必须逐格复现 V187 记录的 pass/bite/disc/admit
  I2 新旧 `build()` 的判据源码必须逐字相同（sha256）—— 「没有为了读数好看而放宽判据」
  I3 判分只走 `substrate.judge`（= `mw.verify.run_example`），两侧同一把尺
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "l3_s271"))
sys.path.insert(0, str(HERE.parent / "l3_s272"))
sys.path.insert(0, str(ROOT))

import substrate as S                                    # noqa: E402
import arms_v184 as A4                                   # noqa: E402
from mw import critgen as NEW                            # noqa: E402
from mw import metacrit as NEWMC                         # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OLD = _load("critgen_v187", HERE / "_critgen_v187.py")
OLDMC = _load("metacrit_v187", HERE / "_metacrit_v187.py")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def replay(t: dict, rec: dict, dev, met) -> dict:
    """在给定装置上重跑一格。⛔ 判分只调 `A4.judge4`（内核同一把尺）。"""
    fn = S.fn_name(t)
    n = int(rec["n_args"])
    kind, types, slots = rec["kind"], rec["types"], rec["slots"] or {}
    out = {"built": False, "pass_oracle": False, "bite_partial": False,
           "bite_stub": False, "disc": False, "admit": False, "fail": "",
           "admit_plain": False, "src": "", "src_sha": "", "oracle_const": None}
    d = dev.diagnose(kind, types, slots, n)
    if not d["ok"]:
        out["fail"] = d["code"]
        out["src"] = ""
        return out
    allin = dev.inputs_for(kind, types)
    legal = dev.legal_indexes(t["oracle"], fn, allin)
    if legal is None:
        out["fail"] = "PROBE_FAULT"
        out["src"] = ""
        return out
    if not legal:
        out["fail"] = "OUT_OF_DOMAIN"
        out["src"] = ""
        return out
    use = [allin[i] for i in legal]
    src = dev.build(kind, types, slots, fn, use)
    if not src:
        out["fail"] = "SHAPE"
        out["src"] = ""
        return out
    out["built"] = True
    out.update(A4.judge4(t, src))
    out["disc"] = out["pass_oracle"] and out["bite_partial"]
    out["src"] = src
    out["src_sha"] = _sha(src)
    # 口径差异就在这两行：`params` 与三值读数
    out["admit_plain"] = met.admit(src)[0]
    out["admit"] = (met.admit(src, dev.spec_params(kind))[0]
                    if hasattr(dev, "spec_params") else met.admit(src)[0])
    out["oracle_const"] = dev.constant_on(t["oracle"], fn, use) if hasattr(dev, "constant_on") else None
    out["fail"] = "" if out["pass_oracle"] else "SEMANTIC"
    return out


def verdict3(r: dict) -> str:
    """三值（PREREG §2.2）。⛔ 不在这里另写一份：口径只有 `critgen.verdict()` 一处。"""
    return NEW.verdict(built=bool(r.get("built")), pass_oracle=bool(r["pass_oracle"]),
                       oracle_const=r.get("oracle_const"), fail=r.get("fail") or "")


def main() -> int:
    recs = json.loads((HERE.parent / "l3_s275" / "readings_v187.json").read_text(encoding="utf-8"))
    tasks = S.tasks()
    rows, i1_bad, i2_bad = [], [], []
    for rec in recs:
        t = tasks[rec["id"]]
        old = replay(t, rec, OLD, OLDMC)
        new = replay(t, rec, NEW, NEWMC)
        if (old["pass_oracle"], old["bite_partial"], old["bite_stub"],
                old["disc"], old["admit_plain"]) != (
                rec["pass_oracle"], rec["bite_partial"], rec["bite_stub"],
                rec["disc"], rec["admit"]):
            i1_bad.append({"arm": rec["arm"], "id": rec["id"],
                           "recorded": [rec["pass_oracle"], rec["bite_partial"],
                                        rec["bite_stub"], rec["disc"], rec["admit"]],
                           "replayed": [old["pass_oracle"], old["bite_partial"],
                                        old["bite_stub"], old["disc"], old["admit_plain"]]})
        if old.get("src_sha") != new.get("src_sha"):
            i2_bad.append({"arm": rec["arm"], "id": rec["id"],
                           "old": old.get("src_sha"), "new": new.get("src_sha")})
        rows.append({"arm": rec["arm"], "id": rec["id"],
                     "kind": rec["kind"], "types": rec["types"],
                     "old": {k: old.get(k) for k in
                             ("built", "pass_oracle", "bite_partial", "bite_stub",
                              "disc", "admit", "admit_plain", "fail", "src_sha")},
                     "new": {k: new.get(k) for k in
                             ("built", "pass_oracle", "bite_partial", "bite_stub",
                              "disc", "admit", "admit_plain", "fail", "src_sha",
                              "oracle_const")},
                     # 旧口径 = 同一函数、`oracle_const=False`（没有第三值这件事本身）
                     "verdict_old": NEW.verdict(built=bool(old["built"]),
                                                pass_oracle=bool(old["pass_oracle"]),
                                                oracle_const=False,
                                                fail=old.get("fail") or ""),
                     "verdict_new": verdict3(new)})

    # ── 汇总 ───────────────────────────────────────────────────────────
    def arm_tab(which: str) -> dict:
        out = {}
        for arm in ("A", "B", "C"):
            rs = [r for r in rows if r["arm"] == arm]
            pk = {r["id"] for r in rs if r[which]["pass_oracle"]}
            dk = {r["id"] for r in rs if r[which]["disc"]}
            ad = {r["id"] for r in rs if r[which]["admit"]}
            out[arm] = {"pass": len(pk), "pass_set": sorted(pk),
                        "disc": len(dk), "disc_set": sorted(dk),
                        "admit": len(ad), "admit_set": sorted(ad),
                        "n": len(rs)}
        return out

    tab_old, tab_new = arm_tab("old"), arm_tab("new")
    und = [r for r in rows if r["verdict_new"] == "UNDECIDED"]
    flipped = [r for r in rows if r["verdict_old"] != r["verdict_new"]]
    admit_flip = [r for r in rows if r["old"]["admit"] != r["new"]["admit"]]
    type_reject = [r for r in rows if r["new"]["fail"] == "TYPES" and r["old"]["fail"] != "TYPES"]

    # ── P3 后半：V79 手写断言（params=() ⇒ 口径必须逐条不变） ────────────
    v79 = S.v79_asserts()
    v79_rows, v79_flip, v79_false = [], [], 0
    for tid, asserts in v79.items():
        for i, a in enumerate(asserts):
            o = OLDMC.admit(a)[0]
            nn = NEWMC.admit(a)[0]
            if o != nn:
                v79_flip.append((tid, i))
            v79_false += (not nn)
            v79_rows.append({"id": tid, "i": i, "old": o, "new": nn, "src": a[:160]})

    out = {
        "frozen": {"critgen_v187_sha256": _sha((HERE / "_critgen_v187.py").read_text(encoding="utf-8")),
                   "metacrit_v187_sha256": _sha((HERE / "_metacrit_v187.py").read_text(encoding="utf-8"))},
        "checks": {"I1_replay_mismatch": i1_bad, "I2_src_changed": i2_bad},
        "arms_old": tab_old, "arms_new": tab_new,
        "flipped_verdict": flipped, "admit_flip": admit_flip, "type_reject": type_reject,
        "undecided": [{"arm": r["arm"], "id": r["id"], "kind": r["kind"],
                       "types": r["types"]} for r in und],
        "v79": {"n": len(v79_rows), "flip": v79_flip,
                "rejected_new": v79_false, "rows": v79_rows},
        "rows": rows,
    }
    (HERE / "readings_v188.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 打印 ───────────────────────────────────────────────────────────
    print("=" * 78)
    print("I1 老装置重放（必须全同）:", "OK" if not i1_bad else f"✗ {len(i1_bad)} 格")
    for b in i1_bad:
        print("   ", b)
    print("I2 判据源码（新旧必须逐字相同）:", "OK" if not i2_bad else f"✗ {len(i2_bad)} 格")
    for b in i2_bad:
        print("   ", b)
    print("=" * 78)
    print(f"{'臂':<3}{'旧 pass':>9}{'新 pass':>9}{'旧 disc':>9}{'新 disc':>9}{'旧 admit':>10}{'新 admit':>10}")
    for arm in ("A", "B", "C"):
        o, n = tab_old[arm], tab_new[arm]
        print(f"{arm:<3}{o['pass']:>9}{n['pass']:>9}{o['disc']:>9}{n['disc']:>9}"
              f"{o['admit']:>10}{n['admit']:>10}")
    print("\nP2 变判定的格:", [(r["arm"], r["id"], r["verdict_old"]) for r in flipped] or "无")
    print("P3 准入翻 True 的格:", [(r["arm"], r["id"], r["kind"]) for r in admit_flip] or "无")
    print("P1 同类型约束拒掉的格:", [(r["arm"], r["id"], r["types"]) for r in type_reject] or "无")
    print(f"UNDECIDED: {len(und)} 格 ->",
          [(r["arm"], r["id"], r["kind"], r["types"]) for r in und] or "无")
    print(f"\nV79 手写断言 {len(v79_rows)} 条：翻转 {len(v79_flip)} 条、"
          f"新口径下被拒 {v79_false} 条")
    for r in v79_rows:
        print(f"   {r['id']:<20} #{r['i']} old={r['old']} new={r['new']}  {r['src'][:70]}")
    return 0 if (not i1_bad and not i2_bad) else 1


if __name__ == "__main__":
    raise SystemExit(main())

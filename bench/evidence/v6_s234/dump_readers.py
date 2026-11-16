"""把两个读者（`ledger_verdict` / `ledger_audit`）的输出全部倒出来，供新旧对比。

用法：`PYTHONPATH=<某个仓库快照> python3 dump_readers.py <账本>` ⇒ stdout JSON。

⛔ 这份脚本不改任何东西；它存在的唯一目的是让「新旧逐字相同」这句话**可执行**，
   而不是靠我读代码说「应该一样」（纪律 50：记录版装置必须自校验）。
"""

import json
import sys
from pathlib import Path

import hashlib

import mw.bench as _B
from mw.bench import ledger_audit, ledger_audit_strata, ledger_verdict

#: 自证：这份输出是**哪个文件**跑出来的。⛔ 少了这一格，「新旧对比」有可能
#: 其实是「新 vs 新」——v6_s234 第一次跑 J4 就栽在这里（`python3 -c` 的
#: `sys.path[0]` 是 cwd，PYTHONPATH 排在后面）。
_MODULE = _B.__file__
_SHA = hashlib.sha256(open(_MODULE, "rb").read()).hexdigest()[:12]


def main():
    path = Path(sys.argv[1])
    recs = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rids = [r.get("run_id") for r in recs if r.get("run_id")]
    # 探针：真实存在的每一行 + 三种缺席（普通 / 非 ASCII / 空串）
    probes = rids + ["no-such@0", "r-不存在", ""]
    out = {"module": _MODULE, "sha": _SHA, "audit": {}, "verdict": {}}
    for rid in probes:
        out["audit"][rid] = list(ledger_audit(path, rid))
        out["verdict"][rid] = list(ledger_verdict(path, rid))
    st = ledger_audit_strata(path)
    out["strata"] = {k: st.get(k) for k in
                     ("rows", "VERIFIED", "MISMATCH", "UNVERIFIABLE")}
    print(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=1))


if __name__ == "__main__":
    main()

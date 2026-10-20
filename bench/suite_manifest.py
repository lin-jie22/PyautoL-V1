#!/usr/bin/env python3
"""套件指纹 manifest —— 考题完整性闸（★7p 补强②）。

对齐的威胁模型：heldout 考题躺在仓库里，任何一次"顺手改改题面"都会
**无声地换掉尺子** —— `run.py` 运行期印的 `suite_sha` 只在那一刻有对照，
没有人会在改题后回头改历史证据。本闸把"改考题"变成一件必须**刻意**
做的事：改题 ⇒ sha 变 ⇒ verify 报红 ⇒ 必须重新生成 manifest 并在
DESIGN 里登记"为什么改题"。

刻意的摩擦就是功能本身（对齐 Google 双盲评测的"考题完整性"精神）。

用法：
    python3 bench/suite_manifest.py build     # 生成/更新 manifest
    python3 bench/suite_manifest.py verify    # 逐文件对拍；rc!=0 = 被改过
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
HELDOUT = V1 / "bench" / "heldout"
MANIFEST = V1 / "bench" / "suites.manifest.json"


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build() -> dict[str, str]:
    files = sorted(HELDOUT.glob("*.jsonl"))
    if not files:
        raise SystemExit(f"❌ {HELDOUT} 下没有任何 .jsonl")
    m = {p.name: _sha_file(p) for p in files}
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1,
                                   sort_keys=True) + "\n")
    return m


def verify() -> tuple[bool, list[str]]:
    if not MANIFEST.exists():
        return False, ["manifest 不存在（先跑 build）"]
    want = json.loads(MANIFEST.read_text())
    have = {p.name: _sha_file(p) for p in sorted(HELDOUT.glob("*.jsonl"))}
    lines: list[str] = []
    ok = True
    for name in sorted(set(want) | set(have)):
        if name not in have:
            ok = False
            lines.append(f"  ❌ {name}: manifest 有、磁盘没有（考题被删？）")
        elif name not in want:
            ok = False
            lines.append(f"  ❌ {name}: 磁盘有、manifest 没有（新考题未登记）")
        elif want[name] != have[name]:
            ok = False
            lines.append(f"  ❌ {name}: sha 不一致（考题被改，未重新登记）")
        else:
            lines.append(f"  ✅ {name}")
    return ok, lines


def main() -> int:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "verify")
    if cmd == "build":
        m = build()
        print(f"manifest 已写 {MANIFEST.relative_to(V1)}，共 {len(m)} 份：")
        for k, v in sorted(m.items()):
            print(f"  {v[:16]}…  {k}")
        return 0
    ok, lines = verify()
    print("\n".join(lines))
    print(f"{'✅ 考题完整性：与 manifest 逐一致' if ok else '❌ 考题完整性破坏'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

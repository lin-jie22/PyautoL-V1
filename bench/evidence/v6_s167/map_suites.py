"""把 `suite_sha` 映射回具体题集文件（`Suite.sha` 是内容哈希，不是文件哈希）。"""
import glob
import sys

sys.path.insert(0, ".")
from mw.bench import Suite  # noqa: E402

for path in sorted(glob.glob("bench/tasks/*.jsonl") + glob.glob("bench/heldout/*.jsonl")):
    try:
        suite = Suite.load(path)
        print(f"{suite.sha()[:12]}  n={len(suite):2d}  {path}  {suite.ids()}")
    except Exception as exc:  # noqa: BLE001
        print(f"----          {path}  ({exc})")

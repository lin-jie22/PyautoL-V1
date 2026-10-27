#!/usr/bin/env bash
# 第九十八轮（§65）复跑脚本 —— 零模型、零网络。
#   ① 探针两臂 + 七条反证
#   ② 全库真实任务日志的重复/停滞分母（只读 bench/evidence/**/*.log）
set -uo pipefail
cd "$(dirname "$0")/../../.." || exit 2
PY="${PY:-/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3}"

echo "=========== ① 探针 --self-check（P1~P7）==========="
"$PY" bench/probe_stallpath.py --self-check
echo "EXIT=$?"

echo
echo "=========== ② 全库真实日志口径 ==========="
"$PY" - <<'PY'
import glob, sys
from pathlib import Path
sys.path.insert(0, "bench"); sys.path.insert(0, ".")
import probe_stallpath as P
paths = [Path(p) for p in glob.glob("bench/evidence/**/*.log", recursive=True)]
print("候选日志", len(paths))
raise SystemExit(P.measure_logs(paths))
PY
echo "EXIT=$?"

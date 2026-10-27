#!/usr/bin/env bash
# 第九十七轮 §64：对账「代理侧成功返回、内核侧没拿到」（零模型、只读冻结台账）
#
# ⛔ 为什么不用重新跑模型：两个台账（proxy_*.jsonl / run_*.log）在第九十六轮已经冻结进
#    bench/evidence/overrun_s96/，本轮的读数完全来自它们。重跑会引入新的采样运气，
#    而 §63 刚刚量出「T=0 不是逐字确定」—— 那样得到的数只会更不可比。
#
# 用法：bash bench/evidence/discard_s97/run_discard.sh
set -uo pipefail
cd "$(dirname "$0")/../../.." || exit 2
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
E=bench/evidence/overrun_s96

echo "================ §64 丢弃对账（第十四件量具的自检）================"
"$PY" bench/probe_discard.py --self-check || exit 1

echo
echo "================ 对照臂：V1_CLOSE_OVERRUN_S=0 ================"
"$PY" bench/probe_discard.py --proxy "$E/proxy_old.jsonl" --run "$E/run_old.log" \
    --wall 120 --reserve 45 --base 120 --overrun 0

echo
echo "================ 新臂：V1_CLOSE_OVERRUN_S=30 ================"
"$PY" bench/probe_discard.py --proxy "$E/proxy_new.jsonl" --run "$E/run_new.log" \
    --wall 120 --reserve 45 --base 120 --overrun 30

echo
echo "================ 重复动作（两臂）================"
# ⛔ 不用 `grep -c '重复动作'`：BSD grep 对多字节的模式不认（纪律⑨），
#    用 Python 数，且**只有文件在**时才数（缺文件必须显式报，不许静默 0）。
"$PY" - "$E" <<'PY'
import sys
from pathlib import Path
E = Path(sys.argv[1])
for arm in ("old", "new"):
    p = E / f"run_{arm}.log"
    if not p.exists():
        print(f"  {arm:4s} 缺 {p}")
        continue
    print(f"  {arm:4s} {p.read_text(encoding='utf-8', errors='replace').count('重复动作已跳过')} 次")
PY

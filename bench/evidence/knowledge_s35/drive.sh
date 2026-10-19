#!/bin/bash
# §17-7g 全档位驱动（每次调用 = 一个档位的 H/P 两臂 × 2 repeats）
set -u
cd /Users/linshaojie/V1
for L in o1_l60 o1_l40 o1_l120 o1_l84 o2_l40 o2_l120; do
  echo "###### $(date '+%H:%M:%S') 档位 $L"
  bash bench/run_g7n.sh /tmp/v1_g7n/seed_7g_$L /tmp/v1_g7n/ev_$L 2
done
echo "###### $(date '+%H:%M:%S') E 臂（空表，与档位无关）"
V1_ARMS=E bash bench/run_g7n.sh /tmp/v1_g7n/seed_E /tmp/v1_g7n/ev_E 2
echo "###### $(date '+%H:%M:%S') ALL DONE"

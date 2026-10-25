#!/usr/bin/env bash
# V1 产品化 R-a：快速上手（DESIGN §19，第四十五轮登记）。
# 零第三方依赖、零密钥即可跑通全部四步；真模型用法见 README 第六节。
# ⛔ 本脚本不写任何文件进仓库。
set -uo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

say "1/4 离线端到端自检（零网络，数秒）"
"$PY" run.py --selftest || { echo "❌ selftest 失败"; exit 1; }

say "2/4 契约测试（585 条断言；每条都有配套撤回态证明不是恒真）"
"$PY" tests/contract_test.py | tail -3 || { echo "❌ 契约红"; exit 1; }

say "3/4 锚层（bench_test：五把假尺子的反证组 —— 尺子先自证有分辨力）"
"$PY" tests/bench_test.py 2>&1 | tail -3

say "4/4 测量模式（stub 模型；--repeat≥2 即进入测量）"
"$PY" run.py --selftest --repeat 2 --budget 1 2>&1 | tail -6

say "下一步"
cat << 'NEXT'
  · 完整验证:  ./run_tests.sh   （契约 + 锚层 + R13 撤回态全量）
  · 真模型:    export V1_LLM_BASE_URL=... V1_LLM_API_KEY=... V1_LLM_MODEL=...
               python3 run.py --bench --solver llm --suite bench/heldout/g7j_n0_heldout.jsonl
  · 读 README 第六节「怎么跑」+ 第七节「V1 明确没有做到的事」。
NEXT

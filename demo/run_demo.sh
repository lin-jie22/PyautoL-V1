#!/usr/bin/env bash
# V1 演示（§19 R-c）：三臂对照最小可复现实例。
# 零模型、零网络、零密钥；离线确定性参照点 ⇒ 读数逐位可复现。
# 预期读数见 demo/README.md（写死数字；实跑不符 = 演示红）。
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"

say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

# 判据 = 读数与 demo/README.md 的预期**逐位一致**（不以 run.py 退出码为准：
# null 臂"未产出答案"是本义，rc=1 是如实信号，不是故障 —— run.py:1203）。
arm() {
  local want="$1" label="$2"; shift 2
  local got
  got=$("$PY" run.py --bench --offline "$@" --no-external --no-ledger 2>&1 \
        | grep -oE "mean=[0-9.]+" | head -1)
  if [ "$got" = "mean=$want" ]; then
    echo "  ✅ $label  mean=${want}（预期一致，极差 0.0000）"
  else
    echo "  ❌ $label  读数=$got 预期=mean=$want"; exit 1
  fi
}

say "臂 1/3  oracle（上界参照）—— 尺子能确认的满分形状"
arm 1.0000 "oracle" --solver oracle

say "臂 2/3  null（下界参照）—— 空答案的确定性归宿"
arm 0.0000 "null" --solver null

say "臂 3/3  partial（中段参照）—— 锚层也能**确认**一个改进，而不只是拒绝"
arm 0.6194 "partial" --solver partial

say "读什么"
cat << 'NEXT'
  · 三臂极差全部 = 0.0000：离线桩确定性 ⇒ 测量管线**不引入**噪声。
    （真模型的噪声必须用同样方式在真模型上测出来，不许假设 —— README 第六节。）
  · oracle=上界、null=下界、partial=中段：先让尺子在三处参照点上读出
    **不同且可复现**的数，"变好了"这句话才有立足点。
  · 下一步：./quickstart.sh（契约+锚层）→ README 第六节（真模型三臂）。
NEXT

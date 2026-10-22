#!/usr/bin/env bash
# 第六十轮（CI 上线）的可重跑探针：**权限/运行/读数**三件事都用实测回答。
#
#   GH_TOKEN=... bash bench/evidence/knowledge_s63/ci_probe.sh
#
# ⛔ 本脚本**不存 token**、也不打印它：只从环境读。缺了就退出 2，不猜。
# ⛔ 探针会建一个临时 ref 再删掉 —— 这是唯一能区分"只读"与"可写"的办法：
#    `GET /repos` 返回的 permissions.admin=true **不能**当成写权限的证据
#    （第四十八轮实测：admin:true 的同时 POST /git/refs 是 403）。
#
# ⚠️ 本文件第一版在 `set -u` 下直接炸：`echo "HTTP=$DEL（204）"` ——
#    bash 3.2 把紧跟变量名的**全角括号**并进了变量名（`DEL（`），报
#    `DEL\xef...: unbound variable`。中文注释里贴变量一律写 `${VAR}`。
#    同类隐患可静态自查：正则 `\$[A-Za-z_]\w*[^\x00-\x7f]`（本目录有记录）。
set -uo pipefail

: "${GH_TOKEN:?需要 GH_TOKEN（Contents: Read and write + Workflows: Read and write）}"
REPO="${REPO:-lin-jie22/PyautoL-V1}"
API="https://api.github.com/repos/$REPO"
H=(-H "Authorization: Bearer $GH_TOKEN" -H 'Accept: application/vnd.github+json')

echo "=== 1) 写权限：POST /git/refs（201 = 可写；403 = 只读）==="
SHA=$(git -C "$(dirname "$0")/../../.." rev-parse HEAD 2>/dev/null || echo "")
[ -z "$SHA" ] && { echo "❌ 取不到本地 HEAD（请在仓库里跑）"; exit 2; }
REF="refs/heads/probe-$(date +%s)"
CODE=$(curl -s -o /tmp/_ci_probe_ref.json -w '%{http_code}' -X POST "${H[@]}" \
        "$API/git/refs" -d "{\"ref\":\"$REF\",\"sha\":\"$SHA\"}")
echo "  HTTP=$CODE  HEAD=$SHA"
if [ "$CODE" = "201" ]; then
  echo "=== 2) 清理探针分支 ==="
  DEL=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "${H[@]}" "$API/git/refs/${REF#refs/}")
  echo "  DELETE HTTP=${DEL}（204 = 已删）"
else
  echo "  ⚠️ 不可写 ⇒ 后面的运行读数可能仍是旧的"
fi

echo "=== 3) Actions 可读性 ==="
curl -s "${H[@]}" "$API/actions/runs?per_page=3" -o /tmp/_ci_probe_runs.json
python3 - <<'PY'
import json
d = json.load(open('/tmp/_ci_probe_runs.json'))
print(f"  total_count={d.get('total_count')}")
for r in d.get('workflow_runs', []):
    print(f"  id={r['id']}  {r['name']}  head={r['head_sha'][:8]}  "
          f"{r['status']}/{r['conclusion']}  {r['created_at']}")
PY

echo "=== 4) 最近一次运行的逐 job 结论 ==="
RID=$(python3 -c "import json;d=json.load(open('/tmp/_ci_probe_runs.json'));print((d.get('workflow_runs') or [{}])[0].get('id',''))")
[ -z "$RID" ] && { echo "  （还没有任何运行 —— 推一个含 .github/workflows/ 的提交才会有）"; exit 0; }
curl -s "${H[@]}" "$API/actions/runs/$RID/jobs" -o /tmp/_ci_probe_jobs.json
python3 - <<'PY'
import json
d = json.load(open('/tmp/_ci_probe_jobs.json'))
for j in d.get('jobs', []):
    print(f"  {j['name']:32s} {j['status']:9s} {j['conclusion']}")
PY
echo
echo "⛔ 绿勾不是读数：要证明'三档读数相同'，请抓日志里的 PASS= 行"
echo "   curl -sL -H \"Authorization: Bearer \$GH_TOKEN\" $API/actions/runs/$RID/logs -o /tmp/ci.zip"

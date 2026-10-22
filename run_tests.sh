#!/usr/bin/env bash
# V1 一键验证。每一步都会跑，任一步失败即整体失败。
#
#   ./run_tests.sh
#
# 不依赖任何第三方包。只用系统/托管的 Python 3。
set -uo pipefail

# Python 解析：优先 $PY，其次 PATH 上第一个版本 ≥3.11 的解释器。
# ⛔ 不写死任何本机路径（别人的机器上没有那条路径），
#    也不静默降级到 3.9（契约用了 tomllib，3.11 起才有 ⇒ 降级只会给一句看不懂的 ImportError）。
if [ -z "${PY:-}" ]; then
  for _c in python3 python3.13 python3.12 python3.11; do
    command -v "$_c" >/dev/null 2>&1 || continue
    if "$_c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$_c"; break
    fi
  done
fi
if [ -z "${PY:-}" ] || ! command -v "$PY" >/dev/null 2>&1; then
  echo "❌ 找不到 Python 3.11+。装一个，或显式指定：PY=/path/to/python3.11 ./run_tests.sh"
  exit 2
fi
cd "$(dirname "$0")"

FAIL=0

echo "=============================================================="
echo "步骤 1/7  依赖审计（必须零第三方依赖）"
echo "=============================================================="
if "$PY" - <<'EOF'
import ast, sys, pathlib
ALLOWED = {
    "argparse","ast","collections","copy","dataclasses","hashlib","json","math","os","re",
    "shlex","sqlite3","statistics","subprocess","sys","tempfile","time","tokenize",
    "typing","urllib","pathlib","__future__","run","mw",
}
bad = []
for p in list(pathlib.Path("mw").glob("*.py")) + [pathlib.Path("run.py")]:
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods = [n.module.split(".")[0]]
        for m in mods:
            if m not in ALLOWED:
                bad.append(f"{p}: {m}")
print("  允许集合外的 import:", bad if bad else "无")
sys.exit(1 if bad else 0)
EOF
then echo "  ✅ 零第三方依赖"; else echo "  ❌ 发现外部依赖"; FAIL=1; fi

echo
echo "=============================================================="
echo "步骤 2/7  契约测试（离线；K 静默降级 / L 外部能力 / O 停滞 / Q 收尾"
echo "                    U 跨领域 / V 因果闭环 / W 抽象读数 / X 血缘完整性 /"
echo "                    Y 失配信号的分辨力 / Z 内核读数落盘 / AA 模块接线 /"
echo "                    AB 闭环通量 / AC 价值层 / AD 自我模型 / AE 消融安全 /"
echo "                    AF 失败签名（第二层判据：缺了会看到什么现象）/"
echo "                    AG 外部评审（只读 / 防自评 / 防跨口径）/"
echo "                    AH 可复核性（语言与沟通的真判据）/"
echo "                    AI 自我模型（跨会话账本 / 预测校准 / 口径闸承重）/"
echo "                    AJ 族预测力闸（测不出预测力 ⇒ 不许据此行动）/
                    AK 结构簇作为「族」的定义 / AL 外部奖励只读通道（写入/检索/归因）/
                    AM 证据必须自描述 + 语义选项冲突必须显式报错 /
                    AN 抽象层：「学会了什么」与「记住了什么」分开观测 /
                    AO 安慰剂臂（等长 / 不许空层 / 不许等于原文 / 索引必须同步）/
                    AP 假记忆臂（双射 / 确定性 / 题边界水位 / 判读只一份）/
                    AQ 安慰剂成为一等公民 + 形状臂（并包逐字节回归 / 单因子操纵））"
echo "=============================================================="
if "$PY" tests/contract_test.py > /tmp/v1_contract.out 2>&1; then
  echo "  ✅ $(grep -oE 'PASS=[0-9]+  FAIL=[0-9]+' /tmp/v1_contract.out | tail -1)"
else
  echo "  ❌ 契约测试失败："
  grep "❌" /tmp/v1_contract.out | head -20
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 3/7  外部锚层契约测试（含反证组：假尺子必须被判红）"
echo "=============================================================="
if V1_BENCH_DIR=/tmp/v1_bench_ci "$PY" tests/bench_test.py > /tmp/v1_bench.out 2>&1; then
  echo "  ✅ $(grep -oE 'PASS=[0-9]+  FAIL=[0-9]+' /tmp/v1_bench.out | tail -1)"
  grep -E "^  (校准|.*margin=)" /tmp/v1_bench.out | head -2 | sed 's/^/  /'
else
  echo "  ❌ 外部锚层契约测试失败："
  grep "❌" /tmp/v1_bench.out | head -20
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 3b 文档计数单源核对（AZ47：文档里的数字必须等于本次运行）"
echo "=============================================================="
# 为什么放在这一步、而不是写成一条契约断言：
#   被核对的对象里就含"契约这次跑出多少条"，断言自己也在那个数里 ⇒ N 与 N+1 自指。
#   AZ44 曾把 109 写死、真值涨到 110 后**锚自己把漂移锁住了**，就是这个结构的实证。
#   ⇒ 只有"被测集合之外"能同时看见文档声明与本次实测。数字的唯一来源是这两个日志。
if "$PY" bench/check_counts.py \
     --contract-log /tmp/v1_contract.out --anchor-log /tmp/v1_bench.out; then
  echo "  ✅ 文档声明 == 本次实测（数字只写一处，核对器会说清哪几处漏改）"
else
  echo "  ❌ 文档里的计数与本次实测不一致（改文档，不许改核对器）"
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 4/7  离线端到端自检（零网络 / 零密钥）"
echo "=============================================================="
# ⚠️ 状态目录必须隔离到临时目录：本步骤也是"测试"，不许写生产 state/
#    （V1 自己的第一条纪律就是"测试绝不碰生产目录"）。
_SELFTEST_TMP="$(mktemp -d)"
if V1_STATE_DIR="$_SELFTEST_TMP/state" V1_LOG_DIR="$_SELFTEST_TMP/logs" \
   V1_WORKSPACE="$_SELFTEST_TMP/ws" V1_LLM_OFFLINE=1 \
   "$PY" run.py --selftest > /tmp/v1_selftest.out 2>&1 \
   && grep -q "自检结论: ✅ 通过" /tmp/v1_selftest.out; then
  echo "  ✅ 主循环端到端通过"
  grep -E "^  (状态|预算|守卫拦截|世界模型|记忆|重复行)" /tmp/v1_selftest.out | sed 's/^/  /'
else
  echo "  ❌ 自检失败："
  tail -20 /tmp/v1_selftest.out
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 5/7  跨领域冒烟（同一把打分器 × 每个领域，oracle 参照）"
echo "=============================================================="
# 为什么要有这一步：`--suite-dir` 的价值是"**每个领域**都非零"。
# 这条冒烟只用 oracle 参照（不需要模型、几秒钟），但能保证：
#   ① 每个领域的数据仍能被 calibrate 判为"有分辨力"；
#   ② 每个领域的 oracle 仍拿满分（= 任务集与打分器没漂移）。
# 它同时替「跨领域」这件事把"数据坏了"变成**回归期就能发现**的事，
# 而不是等到某天真跑模型时才发现某个领域全是 0。
# ⚠️ V1_STATE_DIR 也隔离：本步骤会**读**跨会话分数账本（自我模型起跑用），
#    不隔离的话它会读到生产 state/score_ledger.jsonl —— 生产目录被测试读也是污染。
if V1_STATE_DIR=/tmp/v1_cross_state V1_BENCH_DIR=/tmp/v1_cross_ci \
   "$PY" run.py --bench --offline --solver oracle \
     --suite-dir bench/tasks > /tmp/v1_cross.out 2>&1; then
  echo "  ✅ $(grep -oE '可信领域 [0-9]+/[0-9]+.*' /tmp/v1_cross.out | tail -1)"
else
  echo "  ❌ 跨领域冒烟失败（某领域的校准或 oracle 不满分）："
  tail -12 /tmp/v1_cross.out | sed 's/^/  /'
  FAIL=1
fi

# ── 5b 语言领域（**另一把尺子**）────────────────────────────────────────
# ⚠️ 为什么不和上一步合并：`--suite-dir` 一次只用**一把**打分器，而
#    `bench/tasks_lang/` 量的是语言（答案里的断言可不可核对），
#    `bench/tasks/` 量的是代码（能不能跑通）。**一把尺子量一种东西。**
#    把它们塞进同一个目录，就是让调用方去猜"这题该用哪把尺子" ——
#    而判据该由被测对象声明，不由调用方推断。
echo "步骤 5b  语言领域冒烟（assertion 打分器 × 可复核性）"
echo "=============================================================="
if V1_STATE_DIR=/tmp/v1_lang_state V1_BENCH_DIR=/tmp/v1_lang_ci \
   "$PY" run.py --bench --offline --solver oracle \
     --scorer bench/scorers/assertion.py \
     --suite-dir bench/tasks_lang > /tmp/v1_lang.out 2>&1; then
  echo "  ✅ $(grep -oE '可信领域 [0-9]+/[0-9]+.*' /tmp/v1_lang.out | tail -1)"
else
  echo "  ❌ 语言领域冒烟失败（校准或 oracle 不满分）："
  tail -12 /tmp/v1_lang.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6：自我模型的判据（跨会话预测校准）────────────────────────────
# 为什么单独一步：它测的不是"内核跑不跑得通"，而是"自我模型说的话算不算数"。
# 判据 = 用历史会话预测未来会话，误差必须**优于**不区分能力族的对照基线；
# 且自带三个反证组（族间无差异 / 混入异口径 / 闸该关时必须关），
# 缺一个就是恒真断言。
echo "步骤 6/7  自我模型判据（跨会话预测校准 + 三个反证组 + 族预测力闸）"
echo "=============================================================="
if "$PY" bench/probe_selfmodel.py \
     --real bench/evidence/score_ledger_real_qwen25_7b_T0_8sess.jsonl \
     > /tmp/v1_self.out 2>&1; then
  grep -E "^  (自我模型预测|对照基线预测|优势|判定阈值|⇒|只收同口径|混入异口径)" \
    /tmp/v1_self.out | sed 's/^/  /'
  echo "  ── 族预测力闸（真实账本）──"
  grep -E "闸=|⛔|行=|自我模型 MAE=" /tmp/v1_self.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '结论：.*' /tmp/v1_self.out | tail -1)"
else
  echo "  ❌ 自我模型判据不成立（判据不能分辨，或反证组没转红）："
  tail -20 /tmp/v1_self.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6b：族的**定义**对照（「族」是概念还是文件名？）──────────────────
# 为什么单独一步：步骤 6 证明"分族能预测"，但没问"族是什么"。
# 如果族 = 文件名，那"能预测"测的是**我怎么分装题目**，不是题本身。
# ⇒ 用同一份真实账本、同一把尺子，只把族换成题面结构自动聚出来的类，对照 snr。
# ⛔ 本步骤的期望输出里有**负面结论**（结构族更弱）。它失败（≠0）才是异常。
echo "步骤 6b 族定义对照（结构族 vs 文件名族，零新模型调用）"
echo "=============================================================="
if "$PY" bench/probe_family_def.py > /tmp/v1_famdef.out 2>&1; then
  grep -E "^  (文件名族|结构族)" /tmp/v1_famdef.out | sed 's/^/  /'
  grep -E "^判定|^  (结构族 snr|现有族预测力)" /tmp/v1_famdef.out | sed 's/^/  /'
  echo "  ✅ 对照跑通（结论为**负面**是预期结果，不是失败）"
else
  echo "  ❌ 族定义对照跑不起来："
  tail -12 /tmp/v1_famdef.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6c：安慰剂对照的**结论探针**（第十七轮）──────────────────────────
# 为什么单独一步：第十六轮踏完了才明白，"读到抽象层 +0.05" 里有可能一大部分
#   只是"上下文里多了一段文本"。第十七轮加了三条等长安慰剂把这件事劈开，
#   结论是**负的**（真 Δ 落在安慰剂带内，不得认领）。
#   这一步不跑模型 —— 它把冻结的 runs.jsonl **重算**一遍：
#     · 数字写在散文里改一个字没人知道，写在探针里改数据它会自己耸；
#     · ⛔ 期望输出是**负面结论**。它失败（≠0）才是异常。
echo "步骤 6c 安慰剂对照结论探针（零模型调用，重算第十七轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_placebo.py > /tmp/v1_placebo.out 2>&1; then
  grep -E "^  (题|k0|mean|   |    [A-D])" /tmp/v1_placebo.out | head -10 | sed 's/^/  /'
  grep -E "安慰剂 Δ 区间|极差 [0-9]" /tmp/v1_placebo.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '结论探针.*' /tmp/v1_placebo.out | tail -1)"
else
  echo "  ❌ 安慰剂结论探针失败（数据换过？判据松了？）："
  tail -20 /tmp/v1_placebo.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6d：假记忆对照的**结论探针**（第十八轮）──────────────────────────
# §16.14 那个 +0.2000（隔离 0.5600 → 累积 0.7600）当时只有「隔离 vs 累积」
#   一个对照，没有安慰剂 ⇒ 分不清"学到了"与"上下文里多了一堆字"。
#   第十八轮把前面几题留下的东西换成**等长、等条数、等结构**的假货。
#   这一步同样不跑模型：它把冻结证据按事前登记的 R1–R5 判读一遍，
#   并核对"当时落盘的判定"是否等于"现在重算的判定"。
#   ⛔ 期望输出随数据而定 —— 它失败（≠0）才是信号。
echo "步骤 6d 假记忆对照结论探针（零模型调用，重算第十八轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_fakemem.py > /tmp/v1_fakemem.out 2>&1; then
  grep -E "^  (P[0-9]|E3|task|mean|z0)" /tmp/v1_fakemem.out | head -14 | sed 's/^/  /'
  echo "  ✅ $(grep -oE '结论探针.*' /tmp/v1_fakemem.out | tail -1)"
else
  echo "  ❌ 假记忆结论探针失败（数据换过？判据松了？事前规则被改？）："
  tail -20 /tmp/v1_fakemem.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6e：形状对照的**结论探针**（第十九轮）────────────────────────────
# 第十八轮把前面的答案换成等长等行的乱码（E1）之后，5 份答卷**逐字相同**
#   ⇒ 语义贡献为 0。但 E1 把字符数、逐行长度、缩进、标点分布**全部保留**了：
#   它证得了"语义没用"，证不了"形状有没有用"。
#   第十九轮加 F 臂（reshape）：**保字符序列、破行几何**。
#   这一步不跑模型，只做两件事：
#     ① 按事前登记的 S1–S6 重算判读（唯一实现，在 probe_shape.py 里）；
#     ② 把前五臂的**逐题**读数与第十八轮冻结值逐位比对
#        —— 并包重构若悄悄改了任何一臂，这里会当场转红。
#   ⛔ 期望输出随数据而定 —— 它失败（≠0）才是信号。
echo "步骤 6e 形状对照结论探针（零模型调用，重算第十九轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_shape.py > /tmp/v1_shape.out 2>&1; then
  grep -E "^  (P[0-9]|E[0-3]|F |task|mean|z0)" /tmp/v1_shape.out | head -16 | sed 's/^/  /'
  grep -E "E3 − F|⇒ 判读" /tmp/v1_shape.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '结论探针.*' /tmp/v1_shape.out | tail -1)"
else
  echo "  ❌ 形状结论探针失败（数据换过？仪器不是同一台？判据松了？）："
  tail -20 /tmp/v1_shape.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6f：按需回看的**结论探针**（第二十轮）──────────────────────────
# 缺陷（§17-9）：`stored = full[:300]` ⇒ 5000 字观测只看见 300 字，
#   其余部分当场消失**且没有任何路径能取回**。修法不是调大 300（那会让一步
#   吃掉整个上下文），而是「整段落盘 + 索引 + 只在意外时取回 + 有上限」。
# 这一步不跑模型，只做两件事：
#   ① 按事前登记的 R1–R4 重算判读（唯一实现在 bench/probe_recall.py）；
#   ② 与落盘判定对账（改过数据或改过阈值，这里会自己转红）。
echo "步骤 6f 按需回看的结论探针（零模型调用，重算第二十轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_recall.py > /tmp/v1_recall.out 2>&1; then
  grep -E "^  (✅|❌) P[0-9]" /tmp/v1_recall.out | head -10 | sed 's/^/  /'
  grep -E "⇒ 判读" /tmp/v1_recall.out | sed 's/^/  /'
  echo "  ✅ 结论探针 全部通过"
else
  echo "  ❌ 回看结论探针失败（数据换过？仪器不是同一台？判据松了？）："
  tail -20 /tmp/v1_recall.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6g：校验闸的**结论探针**（第二十一轮）──────────────────────────
# 缺陷：`tools.py` 首屏写着内核做四件事「预算、记忆、规划、**校验**」。
#   前三件都在，第四件在此之前**零实现** —— 这不是能力缺口，是"声明了没有"。
#   而它同时是边界 6（预算→能力）、边界 10（没有可学的失败）、§17-6（知识不可执行）
#   三条读数的同一个洞。
# 这一步不跑模型，做三件事：
#   ① 按事前登记的 R1–R5 重算判读（唯一实现在 bench/probe_verify.py）；
#   ② **校准**判据：21 道题的参考解必须全过（自检不许比尺子更严）；
#   ③ 与落盘判定对账（改过数据或改过阈值，这里会自己转红）。
echo "步骤 6g 校验闸的结论探针（零模型调用，重算第二十一轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_verify.py > /tmp/v1_verify.out 2>&1; then
  grep -E "^  (✅|❌) P(1|2|3|4|5|7|8|9|11|12|13|14)/" /tmp/v1_verify.out \
    | sed 's/^/  /'
  grep -E "⇒ 判读" /tmp/v1_verify.out | sed 's/^/  /'
  echo "  ✅ 结论探针 全部通过"
else
  echo "  ❌ 校验结论探针失败（判据在造假失败？闸只拦不修？关不掉？）："
  tail -24 /tmp/v1_verify.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6g′：**真模型读数**的重算（零模型调用，读冻结的 runs.jsonl）────────
# 这一步的存在理由是一条纪律：**数字写在散文里改一个字符不容易被发现**。
# 三臂（OFF / ON / 内容无关安慰剂）的结论必须能从落盘文件里重新算出来，
# 而且算出来的判定必须与落盘判定一致。⛔ 期望输出是**负面结论**（Q3），
# 它失败（≠0）才是异常。
echo "步骤 6g′ 校验闸·真模型读数重算（OFF / ON / 安慰剂，三臂）"
echo "=============================================================="
if "$PY" bench/probe_verify_real.py > /tmp/v1_verify_real.out 2>&1; then
  grep -E "^  (✅|❌) Q[0-9]" /tmp/v1_verify_real.out | sed 's/^/  /'
  grep -E "ON − OFF|ON 与 PL|mean: " /tmp/v1_verify_real.out | sed 's/^/  /'
  grep -E "⇒ 判读" /tmp/v1_verify_real.out | sed 's/^/  /'
else
  echo "  ❌ 真模型读数重算失败（证据换过？判据松了？）："
  tail -20 /tmp/v1_verify_real.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6h：路线①·外部能力的**结论探针**（第二十三轮）──────────────────
# 这一轮换的不是判据，是**能力来源**：`agents.json` 从"回显示例"换成
#   `external/code_agent.py`（多角度生成 + 写 assert + 真跑筛选）。
#   事前 R1–R5 写在 DESIGN §16.22 ③，本步骤只重算它。
# 这一步不跑模型，做三件事：
#   ① 读四臂冻结证据（OFF/ON × struct_convert/heldout），按事前规则重算判读；
#   ② **双口径并列**（配对 / 缺题补零）—— 两口径判读不一致即强制 UNKNOWN；
#   ③ 核对"某臂没量到的题"被**点名**而不是被补成 0 分。
#   ⛔ 期望输出随数据而定 —— 它失败（≠0）才是信号。
echo "步骤 6h 外部能力对照结论探针（零模型调用，重算第二十三轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_external_real.py > /tmp/v1_external.out 2>&1; then
  grep -E "^  (sc|ho):|Δ\(|⚠️ |两口径判读" /tmp/v1_external.out | sed 's/^/  /'
  grep -E "^  (外部读数|【)" /tmp/v1_external.out | sed 's/^/  /'
  echo "  ✅ 结论探针 已重算（判读见上）"
else
  echo "  ❌ 外部能力结论探针失败（证据缺失？判据被改？补零又回来了？）："
  tail -24 /tmp/v1_external.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6i：2x 档的证据**必须可核验**（第二十四轮）──────────────────────
# 为什么单列一步：`external_s23` 那批证据产生于口径签名补上 `tool_catalog_sha`
# **之前** ⇒ 它只能判 `unverifiable`（不许读成 pass）。而 2x 那批是新代码跑的，
# **必须**判成 `pass`（只差声明过的 `limits` 与 `tool_catalog_sha`）。
# ⛔ 这一步一旦变成 `fail`，意味着"两臂只差外部能力"这句话又变回散文。
echo
echo "=============================================================="
echo "步骤 6i 2x 档证据的口径核验（必须是 pass，不是 unverifiable）"
echo "=============================================================="
if "$PY" bench/probe_external_real.py --dir bench/evidence/external_s24_2x \
      > /tmp/v1_external_2x.out 2>&1; then
  grep -E "口径核验|^(  sc|  ho):|Δ\(配对|【" /tmp/v1_external_2x.out | sed 's/^/  /'
  if grep -q "口径核验: pass" /tmp/v1_external_2x.out; then
    echo "  ✅ 2x 证据可核验：两臂只差声明的变量"
  else
    echo "  ❌ 2x 证据**不可**核验（判成了 unverifiable/fail）⇒ 那次对照不算同一个实验"
    FAIL=1
  fi
else
  echo "  ❌ 2x 结论探针失败（证据缺失？判据被改？）："
  tail -20 /tmp/v1_external_2x.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6j：重试链守墙钟的**正式探针**（第二十五轮）──────────────────────
# 缺陷：`timeout_s=120` + `llm_retries=2` + `wall_s=120` ⇒ 一次逻辑调用串起
#   361.5s，超支 3 倍，而日志只留一个 `TimeoutError`、`steps=0`。
#   实现**违反了自己声明的硬上限** —— 这不是"预算设小了"。
# 这一步起一个**本机**慢端点（127.0.0.1，零外网），把两件事一起测：
#   ① 对照臂**逐字复刻 HEAD 的旧语义**（固定 timeout + 重试不查墙钟）
#      ⇒ 没有它就只是"新逻辑跑得快"，而"跑得快"可以被任意调小 timeout 伪造；
#   ② 修复臂的耗时 ≤ wall_s + 5、只发 1 次请求、且余量还在。
# ⛔ 两臂用同一个端点、同一组参数 ⇒ 唯一的自变量是**重试链如何看待墙钟**。
echo "步骤 6j 重试链守墙钟探针（本机慢端点，约 10 秒，零外网）"
echo "=============================================================="
# ⚠️ 必须绕过代理：本机地址若走代理，超时行为会失真（实测踩过一次）。
if NO_PROXY=127.0.0.1 no_proxy=127.0.0.1 "$PY" bench/probe_wall_retry.py \
      > /tmp/v1_wall.out 2>&1; then
  grep -E "^  (✅|❌) R1|^   (耗时|计数|剩余)" /tmp/v1_wall.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '探针结论.*' /tmp/v1_wall.out | tail -1)"
else
  echo "  ❌ 重试链仍在超支预算（或对照臂不再复刻旧语义）："
  tail -20 /tmp/v1_wall.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 7：证据来源指纹（第十五轮实测逼出）──────────────────────────────
# 起因：同一套 struct_convert 在 --max-tokens 1024 / 1536 下给出 0.400 / 0.7600
#   两个相差近一倍的读数。回头查那是哪个预算，却发现四份真实账本里**一行都不带
#   可读的口径字典**（只有 16 位哈希），而生成它们的实验脚本也没进仓库。
#   ⇒ 那是一份六个月后打开就哑了的证据。这一步不跑模型，只做两件事：
#     ① 把"产生证据的脚本"的 sha256 念出来 ⇒ 谁改过脚本，指纹就变；
#     ② 把"账本里有多少行不可复核"念出来 ⇒ 历史欠账不沉默。
echo "步骤 7/7  证据来源指纹（脚本 sha256 + 账本可复核性）"
echo "=============================================================="
if "$PY" - <<'EOF'
import hashlib, pathlib, sys
sys.path.insert(0, ".")
from mw.ledger import ScoreLedger, CALIBER_FIELD

targets = sorted(pathlib.Path("bench").glob("*.sh")) + \
          sorted(pathlib.Path("bench").glob("*.py"))
for p in targets:
    h = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    print(f"  {h}  {p.as_posix()}")
print("  ── 账本可复核性 ──")
worst = 0
for p in sorted(pathlib.Path("bench/evidence").glob("*.jsonl")):
    L = ScoreLedger(p)
    rows = L.load()
    if not rows:
        continue
    sig = max(set(r.get("caliber_sig", "") for r in rows),
              key=lambda s: sum(1 for r in rows if r.get("caliber_sig") == s))
    L.admissible(sig)
    n = len(rows)
    legacy = L.stats.legacy_no_caliber
    worst = max(worst, 0 if n == 0 else legacy / n)
    flag = "⚠️" if legacy else "  "
    print(f"  {flag} {p.name:52s} {n:4d} 行 不可复核={legacy}")
print(f"  ⇒ 历史账本不可复核占比最高 {worst:.0%}（这些行有分数、没有可读口径）")
sys.exit(0)
EOF
then echo "  ✅ 指纹已列出"; else echo "  ❌ 指纹步骤失败"; FAIL=1; fi

echo
echo
if [ "$FAIL" -eq 0 ]; then
  echo "全部通过。"
else
  echo "有步骤失败（见上）。"
fi
exit "$FAIL"

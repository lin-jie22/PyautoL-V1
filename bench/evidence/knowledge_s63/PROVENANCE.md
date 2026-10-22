# 第六十轮证据（§17-7 收口后的产品交付轨）—— **CI 真正上线**

日期：2026-10-22 13:2x–14:0x　Python：3.13.12（本机）/ 3.11、3.12、3.13（CI 矩阵）
零模型调用：本轮不调模型、不开臂，`mw/` 零 diff。

## 一、起因

第五十六轮写好 `ci.yml`，两次推不进去（旧 PAT 缺 `workflow` scope；细粒度 token 只读）。
第五十八、五十九轮复测仍是只读（`POST /git/refs` → 403）。用户本轮补齐权限：
**Contents: Read and write + Workflows: Read and write**。

## 二、权限：**实测**，不看 `permissions` 字段

`token_probe.txt`（可重跑：`GH_TOKEN=... bash ci_probe.sh`）：

| 探针 | 结果 | 判读 |
|---|---|---|
| `POST /git/refs`（建临时分支） | **201** | 可写 |
| `DELETE /git/refs/...`（清理） | **204** | 已删（远端只剩 `main`） |
| `GET /actions/runs` | **200**，`total_count=0` | Actions 可读；仓库从未跑过 CI |

⛔ 仍在守第四十八轮那条：`GET /repos` 的 `permissions.admin=true` **不是**写权限的证据
（当时实测 admin:true 而 `POST /git/refs` 403）。唯一可信的是**真去写一次**。

## 三、CI 首跑：绿勾 → **读数**

    run 39728371572   ci   head=e1428bc8   completed/success
    ── run_tests.sh (py3.11) ── 日志 25262 字符
        ✅ PASS=536  FAIL=0 / ✅ PASS=97  FAIL=0
        ✅ 全部一致（核对 19 处声明，0 处漂移）。/ 全部通过。
    ── run_tests.sh (py3.12) ── 日志 25262 字符  （同上）
    ── run_tests.sh (py3.13) ── 日志 25262 字符  （同上）

→ `ci_run1_readings.log`（抽取器 `read_ci_log.py`，可对任意 run id 重跑）。

★ 判据是 **PASS 行逐字相同**，不是绿勾，**也不是日志字符数**：
run #1 三份恰好都是 25262，而 run #2（`39730018434`，head `cd298b5`）是
**25314 / 25314 / 25315**。查下去差的那点全在**环境元数据**
（`hostedtoolcache/Python/3.11.17` vs `3.13.16`、时间戳），读数行逐字相同
（`ci_run2_logdiff.txt`：22 条差异行全是路径/时间）。
⇒ **字符数是脆指标**（依赖环境元数据长度），差点被当成"三档读数一致"的证据。

`bench_test.py`（锚层 97）在 Ubuntu 上也过，说明 FTS5、sqlite3、
本机慢端点（步骤 6j 绑 127.0.0.1）在 Actions 里都成立。

⛔ 量具坑：第一版"剥时间戳"的正则漏了行首 BOM（`\ufeff`），于是"时间戳已剥"是假的，
diff 里全是时间戳噪声。老账重演：**先 `print(repr())` 再下判据**。

⛔ CI **不跑 bench**：基准要本机 Ollama + qwen2.5:7b，拉模型既慢又让分数依赖第三方服务。
分数证据仍以 `bench/evidence/knowledge_s*/`（PROVENANCE + FILES.sha256）入库。

## 四、AZ47 扩覆盖：CI 文件自己就是"数字副本"

`ci.yml` 里写着"让『契约 535/0、撤回态 110 条』这句话别人 clone 后能自证"，
而它**自己就是那句声明的副本**，且已经过期（实测 536/112）。
⇒ `check_counts.py` 默认扫描范围加 `.github/workflows/*.yml`。

| 阶段 | 读数 |
|---|---|
| 扩覆盖首跑（未改 ci.yml） | 核对 19 处，**2 处红**，全在 `ci.yml:3` |
| 改完 ci.yml | 核对 19 处，**0 处漂移** |
| 反证：把 ci.yml 改回 535/110 | **EXIT=1**，只点名 `ci.yml:3` 两处，其余 17 处仍绿（`neg_ci_counts.log`） |
| 还原 | `shasum` 逐字节一致 |

覆盖 17 → 19。

★ 随后撞上**同一个自指病的第二次实证**：README 补首跑读数时写了"…契约 536/0、锚层 97/0、
**核对 19 处声明** 0 漂移"——这句里含 `契约 536/0`，于是它自己成了第 20 处，
README 里的"19"当场变假。核对器不会报（没有规则认"核对 N 处"，也不该加：
加了它就把自己算进去）。⇒ **扫描计数不许写进被扫描的文档**；README 改为
"文档计数 0 漂移"，N 落在 **20**（README 16 + demo 2 + ci.yml 2），这个数只写在
DESIGN / PROVENANCE（不在扫描范围）。⛔ 只扫"活文档"（README / demo / workflows）；
DESIGN 与 PROVENANCE 是历史台账，按今天真值改 = 伪造记录。

## 五、CI 脚本自身的一处减法

原最后一步 `python3 tests/contract_test.py | tail -3` 与 `run_tests.sh` 步骤 2 完全重叠
（成功时已打印 `PASS=`，失败时已打前 20 条 ❌），等于把最贵的一步（约 50s）翻倍。
改成 `if: failure()` 的"红点诊断"，只补"七步根本没跑到"（依赖审计先挂）那一种情况。

## 六、本轮自踩三处（都当场修好并登记）

1. **`git -c credential.helper='!f()…'` 是追加，不是覆盖。** 本仓库系统级已配 `osxkeychain`，
   它先被问到并命中**旧 `ghp_` token** ⇒ push 被拒，而报错写的是
   `without 'workflow' scope`（classic token 的概念，与细粒度 token 无关）——
   **报错文本把我引向了错误的排查方向**（我一度以为是权限又没生效）。
   正确写法：`git -c credential.helper= -c credential.helper='!f()…'`（第一个空值清空列表）。
2. **bash 3.2 把全角括号并进变量名。** `echo "HTTP=$DEL（204）"` → `DEL（: unbound variable`，
   `set -u` 直接退出。中文紧贴变量名一律写 `${VAR}`。静态自查正则：
   `\$[A-Za-z_]\w*[^\x00-\x7f]`。
3. **macOS `unzip` 撞中文文件名。** `unzip` 按 CP437 解码 ⇒ `write error (disk full?)`
   并交互式卡住（**不是真的磁盘满**，这条报错本身有误导性）。改为在内存里读 zip
   （`read_ci_log.py`）。

## 七、交付

- 入库：`.github/workflows/ci.yml`、`bench/check_counts.py`（+`.github` 覆盖）、
  `README.md`（"未能入库" → 已上线 + 首跑读数 + badge）、tag `v1.0.0`。
- 本目录：`ci_probe.sh` / `token_probe.txt` / `read_ci_log.py` / `ci_run1_readings.log` /
  `check_counts.log` / `neg_ci_counts.log` / `tests311.log` / `tests313.log` / `FILES.sha256`。
- 读数：契约 **536/0**、锚层 **97/0**、撤回态 **112 条**、文档核对 **20 处 0 漂移**；
  本机双档与 CI 三档**逐位相同**。

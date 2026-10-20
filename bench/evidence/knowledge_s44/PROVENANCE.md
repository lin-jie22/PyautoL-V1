# §17-7n PROVENANCE（2026-10-20 11:40–12:35）

## 目的
7m-γ 收口留下的问题：1.0 依赖 prompt 字符串本身还是 loop 轨迹？
★7n 登记为直发探针（零内核改动、真 Planner + 真 HTTPLLM + 冻结 state 单发重放）。

## 实际执行（与登记的偏差全部如实登记）
1. `probe_7n.py` 三次迭代，各次缺陷与读数：
   - run1（`probe_7n_run1_broken_registry.txt`）：`ToolRegistry()` 空表构造 ⇒
     模型点名的 write_code 判 unknown_tool、兜底也无工具 ⇒ 全 NO_PLAN。
     **量具病，不进判读**（判读闸第一版 else 分支会把它误标成 M2，已修）。
   - run2（`probe_7n_run2_4tool_cat.txt`）：4 工具目录（allow_note=False）。
     S1 ADOPT / S2 BASE / S3 ADOPT / S4 BASE / S0 OTHER —— 与 loop 对照呈混合形态。
   - run3/4（`probe_7n_run3/4`）：5 工具目录。全 ADOPT + S0 DIVERGE:recall。
2. **抓包对拍**（决定性步骤）：本地捕获代理 `/tmp/llm_proxy.py`
   （转发 + 请求/响应原文落盘）+ 真实 `run.py` 过代理。
   - `capture_run12_29_with_resp.jsonl`：s42（7m-β）H 臂单 repeat 完整 6 条
     （请求/响应成对）。发现：真实 propose **n=3**（非 1）、**max_tokens=2048**
     （`--max-tokens 1024` 没进 Planner）、**catalog 无 note**（4 工具）——
     口径签名里的 `tool_catalog_sha=8decb60895d4` 是 `catalog_sha()` **默认参数**
     （allow_note=True）算的，与活注册表（allow_note=False）不一致 = 内核自己
     的两个真相。量具病②③。
3. **逐字节重放**：捕获的 propose 请求体原样重发 ×5 + `ollama stop` 冷启动 ×1
   ⇒ 全部 ADOPT；而 12:19 loop 同字节交付 BASE ⇒ **同字节同服务器 T=0
   跨时刻答案可翻**（ollama KV/批处理非确定性；7g 单格翻转先例的放大版）。
4. **交付路径取证**（`delivery_path_forensics.txt` + capture）：两次独立 loop run
   （12:19 `loop_run3_BASE.log` / 12:29 `loop_run4_BASE.log`）交付都是 BASE，
   路径都是 propose(finish, args 空) → `planner.answer()`（user 只有 goal、
   **无语义记忆**）→ 基线代码。而 propose 响应里 strip().lower() 惯例代码
   **完整在场**（内嵌在 run_shell 候选的 cmd 里）。

## 判读
- ★7n 登记的 M1/M2/M3 闸**被事件超越**：探针在到达判读闸之前先挖出了
  装置级结构 —— **交付路径双态**（propose 直答 vs 记忆盲补救），
  0.2 格的交付由看不到语义记忆的补救路径写出。
- 7m-γ"读而不取"**撤回**，改判**"取而交错道"**（adopted-but-misdelivered）。
- 7m-β"E 实锤"/7m-γ"pad15 中性追加也抹除"的机制解释改写：抹除读数 =
  协议握手失败 × 记忆盲补救，⛔ 不能再读成"惯例采用被题面形状抹除"。
- 7k R3a（n0 Δ_H=+0.25）：行为分数真实保留；机制注解改为
  "记忆在场 ⇒ propose 直答路径成功且答案含惯例"。n4 的 propose 响应
  是否也含惯例代码**未测**（登记为 ★7o）。

## 环境
model=qwen2.5:7b（ollama, localhost:11434）T=0.0；内核零改动
（本轮 `git diff mw/` 为空；probe 与代理全在 bench/ 与 /tmp）。
自踩：①`ToolRegistry()` 空表；②catalog sha 以 `catalog_sha()` 默认参数
对标活注册表；③后台代理进程被工具沙箱按命令收割（须用受控后台任务）；
④判读闸第一版把 NO_PLAN 误标成 M2。

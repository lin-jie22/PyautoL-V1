# V238（§309）预注册：4B 进候选池 —— 按 provider 选思考字段

> 写入时刻 = **未跑任何换路读数**、**未改产品代码**。协议取证已做（见 §2），
> 它只决定「怎么实现」，不产生关于效果的结论。

---

## 1. 为什么做这一刀

V236（§307）量到：MODEL 轴能带来 7b 没有的**行为类**。
`qwen2.5:7b` / `qwen2.5:14b` 的 5/6 都错在 `parse_duration('10m')`（`vec=111011`）；
`Qwen3.5-4B`（**关思考**、走原生 `/api/chat`、预注册外单列）错在
`parse_duration('abc')`（`vec=111110`）—— **另一条断言**。

V237（§308）把「再加证据类输入」整条路关掉，唯一剩下的杠杆是：
**换掉权威槽里那份代码的产生机制**。而 4B 是唯一有「新行为类」证据的来源。

⛔ 但 4B **进不了产品通道**：`LLMConfig.thinking` 发的是
`"thinking": {"type": ...}`，V236 实测对 ollama 的 `/v1` 兼容层**静默忽略**
（900 / 2400 / 4000 三档全 `finish=length`、`content` 0 字符）。

⇒ 本轮的**全部内容**是：让产品通道能按 provider 选正确的思考字段，
从而让 4B 真的能被选进候选池。**这是协议适配，不是「让系统变强」。**

## 2. 协议取证（改动前已量，5 条）

同一提示、`max_tokens=200`、`temperature=0`，实测：

| 通道 | 字段 | content | thinking | 结束原因 |
|---|---|---|---|---|
| 原生 `/api/chat` | `think: false` | **14 字符** | 0 | `stop` |
| 原生 `/api/chat` | `think: true` | 0 | 768 | `length` |
| `/v1/chat/completions` | `think: false` | **0** | 768（`reasoning`） | `length` |
| `/v1/chat/completions` | `thinking:{"type":"disabled"}` | **0** | 768 | `length` |
| 原生 `/api/chat` | `qwen2.5:7b` + `think:false` | 17 字符 | 0 | `stop` |

**三条结论**：
1. `/v1` 兼容层对**两种**写法都静默忽略 ⇒ 只换字段名不够，**必须换端点**。
2. 原生 `/api/chat` 的 `think: bool` 有效。
3. `think:false` 对**不支持思考的模型**（`qwen2.5:7b`）**无害**（`stop`、内容正常）
   ⇒ 同一 provider 可以覆盖池里所有本地模型，不必按模型白名单。

ollama 版本 `0.40.16`。

## 3. 实现契约（改哪些、不动哪些）

### 3.1 `mw/config.py`：新增 `provider`

- `_PROVIDERS: tuple[str, ...] = ("", "ollama")`
- 字段 `provider`，`default_factory` 读 `V1_LLM_PROVIDER`，`.strip()`
- `__post_init__` 里**拼错即抛**（与 `thinking` 逐字同一判据）
- `cond()` 里 **只在非空时**加 `provider` 键 —— 理由与 `thinking` 完全相同：
  空串 ⇒ 请求体与历史逐字节相同 ⇒ 签名若多一键就是**反向撒谎**

### 3.2 `mw/llm.py`：`HTTPLLM.complete` 按 provider 分支

`provider == ""` ⇒ **一个字节都不许变**（现有路径原样）。

`provider == "ollama"` ⇒ 走原生：
- URL：`base_url` 去掉尾部 `/v1` 或 `/v1/`，再拼 `/api/chat`
- body：`{"model", "messages", "stream": false, "options": {"num_predict": max_tokens, "temperature": t}}`
- 思考：`thinking=="disabled"` ⇒ `think:false`；`"enabled"` ⇒ `think:true`；
  `""` ⇒ **不发该字段**（与 OpenAI 路径同一条规矩）
- 响应取 `payload["message"]["content"]`
- ⛔ **`done_reason == "length"` 必须抛 `LLMError`**：这正是 V236 的失败形态
  （思考吃满、`content` 0）。静默返回空串会把「装置故障」记成「模型不会」。
  这与 `mw/llm.py` 开头「静默降级是经典病灶」是同一条判据。

### 3.3 `run.py`：换路条目支持三元组

`--goals-swap-models` 的每个条目：

| 写法 | 含义 |
|---|---|
| `qwen2.5:14b` | 只换 model（**V235/V236 逐字节同行为**） |
| `Qwen3.5-4B@ollama` | 换 model + provider，thinking 继承 cfg |
| `Qwen3.5-4B@ollama:disabled` | 再显式设 `thinking` |
| `Qwen3.5-4B@ollama:` | 显式清空 thinking（= 不发该字段） |

⚠️ **解析顺序必须是先 `@` 再 `:`** —— 模型名本身含冒号（`qwen2.5:7b`），
先切冒号会把 `qwen2.5` 和 `7b@ollama` 当成两段。

## 4. 判据（P1~P6，逐条可判）

| # | 判据 | 怎么量 |
|---|---|---|
| **P1** | `provider=""` 的请求体与改动前**逐字节相同** | 拦一个本地 mock 端点，比对 json bytes；再用改动前的 body 构造函数硬编码 sha |
| **P2** | `V1_LLM_PROVIDER=bogus` **构造即抛** `ValueError` | 直接构造 |
| **P3** | 原生通道**正例**：`Qwen3.5-4B` + `think:false` + `max_tokens=900` 得 `content>0` | 真调用一次（V236 同参数是 0 字符） |
| **P4** | 三元组解析：`qwen2.5:7b@ollama:disabled` ⇒ `("qwen2.5:7b","ollama","disabled")` | 单元断言 |
| **P5** | 部署态：池里含 `Qwen3.5-4B@ollama:disabled` 时，换路**至少 1 次产出非空且 `vec` 可判** | 真跑 `--goals-swap`，读 `goal_obs.jsonl` |
| **P6** | `qwen2.5:7b@ollama:disabled` 无害（`content>0`、判分出得来） | 同一装置 |

**主判据 = P5**。P1/P2/P4 是不许破的回归，P3/P6 是装置正例/无害性。

### 4.1 我押什么（先写下来，事后不许改）

> **押：4B 在**产品通道**上的 `vec` 与 V236 旁路采样（原生 `/api/chat`、850 token）
> **同类** —— 即它仍挂 `'abc'` 那格而不是 `'10m'` 那格。**

理由：提示文本由同一个 `build_proposal_prompt` 构造，协议只是通道；
若行为变了，说明**思考字段/预算**真的改了它的解题路径，那本身就是一条读数
（但不能算「复现」）。

⚠️ 若 4B 在产品通道给出 6/6：**不是新行为类**（那是最保守的那类），
⛔ 不许把它记成「4B 更强」。

## 5. 装置与成本

- 真 ollama，本地，**零 API 费用**。
- 速度闸（纪律 116）：跑前量 `Qwen3.5-4B` 真实 tok/s，`<15` 不启动。
  V236 量到 ≈17.05（关思考）。本轮改完通道后**重测**，两个读数都留档。
- 部署态规模：`--goals-swap 4`，池 = `qwen2.5:14b, Qwen3.5-4B@ollama:disabled`，
  2 轮 ⇒ 最多 8 次生成。单次上限 300 s（纪律 116）。

## 6. 预先声明的失效条件

以下任一成立 ⇒ 本轮**不产出关于效果的结论**，只报装置：

1. P1/P2/P4 任一不过（回归破了，先修，不跑读数）。
2. P3 不过（原生通道仍拿不到 `content`）⇒ 协议适配没做成，**不得**用
   「4B 能力不行」解释（纪律 77：没接通的开关给最顺眼的读数）。
3. P5 跑完但换路次数 < 2 ⇒ 样本量不足，只报装置。
4. 4B 产出全判 0 且 `vec` 全空 ⇒ 只报「通道通了但行为没变」，不报「4B 无效」。

## 7. 不许写进结论的句子

- ⛔「4B 让系统变强」/「候选空间扩大了」
- ⛔「L3 闭环了」/「内核会自己变强」
- ⛔ 拿单题 6/6 说泛化
- ⛔ 拿 `provider` 通当作「模型能力」的证据（那是协议，不是模型）

# PREREG V207b：**题族**聚簇（V207 漏掉的第三个聚类键）

> 写于跑装置之前。原料与 V207 完全相同（11 条真失败 / 6 题），只换聚类键 ⇒ **不新增真跑**。

## 0. 为什么补这一臂

V207 试了两个聚类键：`error_class`（3 簇）与**题**（6 簇）。
漏掉的第三个是**题族** —— 而 V207 的失败恰好落在一个有 3 道题的族上：

| 族 | 失败题 | 条数 |
|---|---|---|
| **解析**（`parse_*`） | `t01_parse_duration`、`z03_parse_query`、`g7s2_parse_headers` | **6** |
| 结构转换 | `z02_to_csv`、`g7s1_flatten_nested` | 4 |
| 序列 | `t05_top_k` | 1 |

族是**唯一一个「跨题且同质」的候选**：3 道题共享「把字符串解析成结构化值」这一个动作。
⇒ 如果跨题归纳在本题集上有任何可能，它必须在这里出现。

## 1. 族的定义（**预先声明**，按题名前缀，不按题面语义事后归并）

```
解析     : parse_*                      （duration / query / headers / date_flex / flag / pairs）
结构转换 : flatten* / unflatten / diff_tree / to_csv / csv_row / to_ini / encode_*
序列     : merge_ranges / rle / dedupe* / top_k / spiral_order / next_permutation / median
字符串   : commas / slugify / truncate* / normalize_ws / mask_email / word_freq / quote_join
数值     : base_convert / gcd_lcm / split_amount / fmt_amount / roman_to_int / min_meeting_rooms
校验     : is_valid_*
```

⛔ 一个题只进一个族；族名先于读数定死，跑完不许改。

## 2. 装置

与 V207 的 A/B/C 三臂同一套 `_DISTILL_SYSTEM` / `temperature=0.0` / `max_tokens=160` / 同一判定函数。
每族一次调用，原料是该族全部失败原文（每条截 180 字，最多 8 条）。

## 3. 押注

| # | 命题 | 押 | 依据 |
|---|---|---|---|
| P1 | 族级产出「族内可迁移」知识 ≥ 1 | **1** | 3 题共享一个动作 |
| P2 | 族级输出与题面最长共同子串占输出比 ≥ 40% | **是** | 拼了 3 个题面，更容易复述 |
| P3 | 族级可迁移条数 ≥ 错误类簇条数 | **是** | 同上 |
| P4 | 解析族产出「解析类要处理空/畸形输入」 | **否** | `t01` 题面已写「空串返回 None」⇒ 复述 |

## 4. 判定

沿用 V207 的三分类（跨题 / 题级 / 空话），本轮的「跨题」放宽为**族内跨题**：
一句话在**本族的另两道题**上仍然成立且不含题名，即算。

⛔ 仍需人判（第③条无法机械化 —— V207 已踩过这个坑）。
⛔ 与 V207 的 A/B/C 三臂比较时，只比**绝对条数**，不比比率（分母 3 vs 11，纪律 67）。

## 5. 作用域

`qwen2.5:7b` × 11 条真失败 × 3 族。⛔ 不覆盖其它族（无失败原料）、不覆盖 T=0 分布。

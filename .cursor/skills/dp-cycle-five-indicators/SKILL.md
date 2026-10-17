> **dzq 独立工程**：本 Skill 随 `dzq` 仓库；无 Qt GUI。网页模板在 `web/dp_cycle/`；入库 `scripts/update_db.py`；报告 `scripts/build_web_report.py`。

---
name: dp-cycle-five-indicators
description: >-
  ftqtz 大周期散户决策系统（DP cycle）：盯其他几路资金重大改变 → 打分 →
  离场/减仓/持有/加仓/抄底；DuckDB、交叉验证、LWC 联动网页与离线 HTML/PDF。
  Use when the user mentions 大周期, 五项指标, dp_cycle, DP周期, 散户决策,
  郭嘉队ETF, 融资分位, 主力资金, 资金行为, or continuing DP work on another machine.
---

# 大周期 · 散户决策（DP Cycle）

接到「大周期 / dp_cycle / 散户决策 / 郭嘉队 / 资金行为」时走本 Skill。**先读口径，再动代码。**

## 核心思想（勿歪）

用户是**散户**。其他几路资金一边宣传价值投资，一边高抛低吸：低位恐吓离场、高位唱多接盘。  
本系统只做一件事：

> **当其他几路资金行为发生重大改变 → 第一时间打分 → 给出决策结论（仓位配比）。**

不是用财报解释每一根 K 线。五项指标是指南针的可画子集，可继续加领先指标；**缺项不记 0 分**。

权威文档：

| 文档 | 作用 |
|------|------|
| [`research/DP/大周期/框架.md`](../../../research/DP/大周期/框架.md) | 口径（尤其 **§6 散户决策**） |
| [`research/DP/散户决策_推演核查.md`](../../../research/DP/散户决策_推演核查.md) | **推演路径 + 精度核查 + 国内外对照** |
| [`research/DP/README.md`](../../../research/DP/README.md) | 换机总览 |
| [`research/DP/五项指标_数据源评估.md`](../../../research/DP/五项指标_数据源评估.md) | 主备源 |
| [`research/DP/资金行为.jpg`](../../../research/DP/资金行为.jpg) | 黑买红卖阶段图 |
| [reference.md](reference.md) | 表结构、LWC 联动 |

## 进度清单

```
DP Cycle Progress:
- [ ] 0. 读 框架.md §6 + 散户决策_推演核查.md
- [ ] 1. 跑 scripts/research/_audit_dp_path.py（路径/精度）
- [ ] 2. 确认库 output/user/dp_cycle/dp_cycle.duckdb
- [ ] 3. 改数：diagnosis.py / allocation.py / 取数模块
- [ ] 4. 改图：web/dp_cycle/*
- [ ] 5. 重建 web_snapshot
- [ ] 6. 同步核查文档缺口
```

## 0. 产品形态（勿回退）

- GUI：`★ 大周期`（`page_id=dp_cycle`）
- **首屏**：决策（离场/减仓/持有观望/加仓/抄底）+ 仓位带 + 合计分  
- 其下：三资金卡片 → 行为/解禁 → 系统性风险 → 资金行为路径  
- 顶图：指数**周线**，叠融资 + **郭嘉队ETF**（可开关）；其下打分曲线 (−10～10)  
- 副图：01 主力月/周、北向、02 郭嘉队ETF、03 融资、04 偏离、05 广度、06 解禁  
- LWC；时间轴全图联动；十字线时间同步、价格用各图自身点  
- 数据 2015+ DuckDB；作者对照线仅叠线（月 −4000 / 周 −2000 / 偏离 17）

## 1. 核心推演路径（改规则必守）

```text
郭嘉队ETF(分位×月变, 最高优先)
 + 融资(分位×方向, 非裸增减)
 + 主力/偏离/广度/解禁(有数才进分)
 → 合计分
 → 硬信号优先 → 五档决策 + 仓位带
```

硬信号（`allocation.decide_retail_action`）：

| 信号 | 决策 |
|------|------|
| 郭嘉队 **高位减** | **离场** 0–20% |
| 融资 **高位加** | 减仓/离场 |
| 融资 **高位减** | **减仓**（杀杠杆） |
| 郭嘉队 **低位加** | **抄底** |
| 融资低位跟随且分偏正 | **加仓** |

融资规则（用户口径）：

- 高位持续增加 → 风险升，警惕离开  
- 低位持续增减 → 跟随（不是高位那套反着做）

实现：`diagnosis.score_margin_regime` / `score_national_team_regime` → `diagnose_position` → `decide_retail_action`。

## 2. 换机最小步骤

```text
PYTHONPATH=.;.\src .venv\Scripts\python scripts\research\update_dp_cycle_db.py --mode weekly --skip-high-low
PYTHONPATH=.;.\src .venv\Scripts\python scripts\research\_audit_dp_path.py
# GUI：gui_main → ★ 大周期 → 从库加载
# 或打开 output/user/dp_cycle/web_snapshot/index.html
```

## 3. 改哪里

| 任务 | 落点 |
|------|------|
| 打分 / 决策 | `diagnosis.py`、`allocation.py` |
| 取数入库 | `src/data_source/dp_cycle/*`、`ingest.py`、`store.py` |
| 交叉验证 | `cross_check.py`、`margin.py` |
| GUI / 网页 | `dp_cycle_page.py`、`web/dp_cycle/` |
| 核查文档 | `research/DP/散户决策_推演核查.md` |
| CLI | `scripts/research/update_dp_cycle_db.py` |

## 4. 已知缺口（勿假装已解决）

| # | 现状 | 缺口 |
|---|------|------|
| 01 | 主力东财≈1 日 | PDF 级月净额 / 长历史 |
| 02 | ETF 份额代理 | ≠ 汇金/证金；分位仅 ~5 年可能稀释「高库存」 |
| 03 | 融资分位×方向已落地 | 75/40、300 亿阈未事件回测 |
| 04 | 偏离稀 | 乐咕源限制 |
| 05 | 广度湖扫未剔 ST | ≠ 作者样本 |
| 06 | 解禁已含未来半年 | 明细对账另表 |
| 北向 | 有效净买常至 2024-08 | 其后字段空 |

精度与国内外对照详见 [`散户决策_推演核查.md`](../../../research/DP/散户决策_推演核查.md)。

## 5. 硬规则

1. **决策层以散户动作为准**；阶段表仓位与决策冲突时跟 `decide_retail_action`。  
2. 郭嘉队ETF **最高优先**；融资看 **分位×方向**，禁止退回「只看加减多少亿」。  
3. 缺项 **不记 0**；不把大单资金流当成股东身份。  
4. 不绑死单一数据商；交叉验证见评估文档。  
5. 勿为柱子好看关掉时间轴联动。  
6. 未要求不要 commit。

## 6. 验收

- [ ] `_audit_dp_path.py` 无 FAIL  
- [ ] 首屏决策 / 仓位 / 分数一致  
- [ ] 周线联动副图；叠线开关可用  
- [ ] web_snapshot 含最新 app.js  
- [ ] 核查文档缺口与实现一致  

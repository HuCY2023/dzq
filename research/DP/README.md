# 大周期五项指标（DP Cycle）

> 换机续开发入口。Skill：`.cursor/skills/dp-cycle-five-indicators/`  
> 口径细节：[`五项指标_数据源评估.md`](./五项指标_数据源评估.md) · 进度缺口：[`大周期五项指标_今日进度.md`](./大周期五项指标_今日进度.md)

## 是什么

**散户决策系统**：盯其他几路资金重大改变 → 打分 → **离场 / 减仓 / 持有观望 / 加仓 / 抄底**（仓位带）。  
不是用财报解释每一根 K 线。口径见 [`大周期/框架.md`](./大周期/框架.md) §6；推演与精度见 [`散户决策_推演核查.md`](./散户决策_推演核查.md)。

落到 ftqtz 的序列：

1. 主力资金（东财大盘净流入 → 月/周）  
2. 国家队代理（宽基 ETF 份额，≠ 汇金/证金）  
3. 两融融资余额（沪+深，多源交叉验证）  
4. 成长–价值偏离度（创业板50 vs 上证50 PE/PB）  
5. 新高−新低广度（主图：K 线湖约 252 日，未剔 ST；乐咕 120 日另存、不与湖扫拼接）  
6. 解禁金额（月/季；全市场柱、科创板线；实际解禁市值，亿元）  

另：顶图指数周线；辅图北向周净买（长历史对齐周 K）。

## 换机怎么开

```text
# 1) 拉代码（含 .cursor/skills/dp-cycle-five-indicators）
# 2) 可选：拷贝 output/user/dp_cycle/ 整目录（库+缓存+离线页）
# 3) 无库则入库：
PYTHONPATH=.;.\src .venv\Scripts\python scripts\research\update_dp_cycle_db.py --mode weekly --skip-high-low
# 4) GUI：gui_main.py → ★ 大周期
#    或打开 output/user/dp_cycle/web_snapshot/index.html
```

## 代码地图

| 层 | 路径 |
|----|------|
| 数据 | `src/data_source/dp_cycle/` |
| GUI | `src/gui/pages/dp_cycle_page.py` |
| 网页 | `src/gui/web/dp_cycle/` |
| CLI | `scripts/research/update_dp_cycle_db.py` |
| 持久化 | `output/user/dp_cycle/dp_cycle.duckdb`（2015+） |

## 网页行为（已实现，勿回退）

- LWC 多面板；quality 交叉验证徽章；loading 遮罩  
- **时间轴缩放/拖拽全图联动**（副图对齐周 K 锚点 + logical 同步）  
- 十字线时间同步；价格用各图自身数据点  

## 原料

| 文件 | 用途 |
|------|------|
| `五项指标复现.pdf` | 图2 量化对照 |
| `微信图片_*.jpg` | 图1 黑买红卖叙事（非时间序列） |
| `inbox/`（可建） | 晚上补的月表/周表 CSV |

## 明确未完成

- 01 PDF 级两年月净额（需手工表）  
- 02 汇金/证金分列  
- 05 湖扫已作主图，但仍未剔 ST，样本范围还不是作者口径  
- 图1 阶段标注层  

作者对照线（仅叠线）：月 −4000 · 周 −2000 · 偏离 17。

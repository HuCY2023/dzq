# DP Cycle · 实现参考

## 决策链（先看这里）

```
diagnosis.score_national_team_regime  # 郭嘉队：分位×月变
diagnosis.score_margin_regime         # 融资：分位×方向
diagnosis.diagnose_position           # 合计分 + state + layers
allocation.choose_phase               # 落到资金行为图阶段
allocation.decide_retail_action       # 硬信号 → 五档决策
gui_bundle.bundle_to_web_payload      # diagnosis → 网页
```

复核：`scripts/research/_audit_dp_path.py`  
口径/精度：`research/DP/散户决策_推演核查.md`

## 目录树

```
src/data_source/dp_cycle/
  diagnosis.py      # 打分项 + 合计 + 位置语言
  allocation.py     # PHASES / DECISIONS / choose_phase / decide_retail_action
  store.py          # DuckDB schema / load_from_db / HISTORY_START
  ingest.py         # update_dp_cycle_db 编排
  gui_bundle.py     # GUI/网页 payload；quality=validate_tables
  cross_check.py    # 读库快检；refresh 时两融多源+北向拆分
  margin.py         # 03 沪深融资 + SSE/东财交叉验证
  main_force_em.py  # 01 东财 fflow daykline
  main_force_ths.py # 01 THS 截面兜底
  northbound.py     # 北向辅源
  national_team_etf.py  # 02 SSE ETF 份额
  growth_value.py   # 04 乐咕 PE/PB
  high_low_legu.py  # 05 乐咕 120 日直连
  high_low_lake.py  # 05 可选 52 周湖扫
  unlock.py         # 06 解禁
  pipeline.py / report.py / long_term_pdf.py

src/gui/pages/dp_cycle_page.py
src/gui/web/dp_cycle/{index.html,app.js,styles.css,lightweight-charts.js}

scripts/research/update_dp_cycle_db.py
scripts/research/_audit_dp_path.py

research/DP/          # 口径与原料
output/user/dp_cycle/ # 库、缓存、离线页、web_snapshot
```

## DuckDB（`output/user/dp_cycle/dp_cycle.duckdb`）

| 表 | 内容 |
|----|------|
| `index_weekly` | 周线 OHLCV（按 symbol） |
| `main_force_daily` | 主力净额等 |
| `northbound_daily` | 北向净买 |
| `etf_shares_daily` | ETF 份额 |
| `margin_daily` | 融资/两融 + 分位 |
| `growth_value_daily` | PE/PB/偏离度 |
| `high_low_daily` | new_high/new_low/diff；主键 `(date, source)`：`lake_52w` 与 `legu_120d` 共存，网页只画一种 |
| `unlock_daily` | 实际/计划解禁市值（亿元）；主键 `(date, universe)`：`all` 全市场、`star` 科创板 |

仅保留 `HISTORY_START`（2015-01-01）起。路径：`output_paths.dp_cycle_store_dir()`。

## 网页时间联动（`app.js`）

1. `padToAnchors(points, weeklyTimes)`：副图无点位置写入 `{time}` whitespace  
2. `applyLogicalToAll`：任意面板 `subscribeVisibleLogicalRangeChange` → 全图同 logical  
3. 十字线：`setCrosshairPosition(本图缓存值, time, series)`；无点则 `clearCrosshairPosition`  
4. 价格轴：`autoscaleInfoProvider` 按**可见 logical** 重算，避免全历史高点压扁近端周 K  

稀疏序列联动后柱变细、中间空白 = 预期行为。

## 交叉验证摘要

| 项 | 读库 | 更新入库（live） |
|----|------|------------------|
| 周 K | OHLC 自洽 | — |
| 01 | 样本数 | — |
| 北向 | 覆盖/陈旧提示 | 北向≡沪+深 |
| 02 | 份额>0 | — |
| 03 | 融资≤两融 | macro_sh≡SSE；合计≈东财≈0.3% |
| 04 | (PE+PB)/2 复算 | — |
| 05 | high−low=diff | — |

## 常用命令

```text
# Windows（仓库根）
set PYTHONPATH=.;.\src
.venv\Scripts\python scripts\research\update_dp_cycle_db.py --mode weekly --skip-high-low

# 推演路径 / 精度机检
.venv\Scripts\python scripts\research\_audit_dp_path.py

# 含广度（慢）
.venv\Scripts\python scripts\research\update_dp_cycle_db.py --mode weekly

# 仅重建带 payload 的快照：GUI「浏览器打开」或脚本内 load_dp_cycle_gui_bundle + 写 web_snapshot
```

## 换机拷贝清单

| 必带 | 可选 |
|------|------|
| git 代码（含 `.cursor/skills/dp-cycle-five-indicators/`） | `output/user/dp_cycle/dp_cycle.duckdb` |
| `research/DP/*` 文档与 PDF/图 | `_em_fflow_raw.json`、`_legu_hl_*.json` |
| | `web_snapshot/`、`大周期五项指标_离线.html` |

无库时在新机器跑一遍 `update_dp_cycle_db` 即可 regenerated（东财窗口仍只有约半年主力）。

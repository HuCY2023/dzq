# dzq · 大周期散户决策

从 ftqtz `research/DP` + `data_source/dp_cycle` 抽出的**独立工程**（无 Qt GUI）。

目标：盯其他几路资金重大改变 → **打分** → **离场 / 减仓 / 持有观望 / 加仓 / 抄底**；可入库、可出离线网页报告、可出 PDF。

## 目录

```
dzq/
  research/DP/          # 框架、核查、数据源评估、资金行为图、关键帧、口播稿
  .cursor/skills/       # Cursor Skill（换机续开发）
  src/
    data_source/dp_cycle/   # 核心源码
    output_paths.py         # 路径垫片
    core/plotting.py        # 中文字体（PDF）
  web/dp_cycle/         # LWC 网页模板（浏览器打开，非 Qt）
  scripts/
    update_db.py        # 入库
    build_web_report.py # 网页报告 / PDF
    audit_path.py       # 推演路径机检
  output/               # 运行产物（gitignore）
```

## 环境

```bash
cd dzq
python -m venv .venv
# Windows:
.venv\Scripts\pip install -r requirements.txt
# Linux/macOS:
# .venv/bin/pip install -r requirements.txt
```

## 常用命令

```bash
# 入库（默认跳过湖扫广度；乐咕 120 日仍可拉）
set PYTHONPATH=src
.venv\Scripts\python scripts\update_db.py --mode weekly

# 出离线网页（output/user/dp_cycle/web_snapshot/index.html）
.venv\Scripts\python scripts\build_web_report.py

# 先更新再出报告 + PDF
.venv\Scripts\python scripts\build_web_report.py --refresh --pdf

# 推演/精度机检
.venv\Scripts\python scripts\audit_path.py
```

用浏览器打开 `output/user/dp_cycle/web_snapshot/index.html` 即可协同查看（或起本地 http.server）。

## 权威文档

| 文档 | 内容 |
|------|------|
| `research/DP/大周期/框架.md` | 口径（§6 散户决策） |
| `research/DP/散户决策_推演核查.md` | 推演路径、精度、国内外对照 |
| `research/DP/五项指标_数据源评估.md` | 主备源 |
| `research/DP/README.md` | 模块总览 |
| `.cursor/skills/dp-cycle-five-indicators/SKILL.md` | Agent Skill |

## 与 ftqtz 的差异

- **无 GUI / Qt WebEngine**；网页为静态 LWC + payload。
- **无 K 线湖广度**：`high_low_lake` 为 stub，默认用乐咕 120 日；需要 52 周湖扫时自行接入 OHLC 库。
- 口播 `audio.wav`（约 90MB）未打包，保留 `transcript.txt` / `transcript.srt` / keyframes。
- 包名仍为 `data_source.dp_cycle`，便于与 ftqtz 对照合入。

## 协同建议

1. 改口径先改 `research/DP/大周期/框架.md` §6，再改 `diagnosis.py` / `allocation.py`。  
2. 改完跑 `scripts/audit_path.py`。  
3. 大文件（DuckDB、缓存）放 `output/`，不要提交。  
4. 未要求不要 force-push。

## License / 免责

研究学习用途，不构成投资建议。

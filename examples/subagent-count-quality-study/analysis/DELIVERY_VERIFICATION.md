# R Markdown 交付自检

| 检查项 | 状态 | 证据 |
|---|---|---|
| 混合架构 | 通过 | `quality_analysis.R` 生成完整 RDS，`quality_analysis.Rmd` 负责展示与解读，专用函数位于 `quality_analysis_functions.R`。 |
| 数据筛选分离 | 通过 | R 数据脚本保留全部 3 个 episode，不剔除 timed-out 或 protocol deviation，并保留运行位置。 |
| 文件命名一致 | 通过 | `quality_analysis.R`、`quality_analysis.Rmd`、`quality_analysis.html` 同名。 |
| luckyBase 与包入口 | 通过 | `00.Environment.R` 强制加载 luckyBase，并用 `luckyBase::Plus.library()` 加载 ggplot2 与 DT。 |
| 跨平台路径 | 通过 | 相对路径与 `file.path()`；`validate_paths.R` 无问题。 |
| 图表/表格解读覆盖 | 通过 | `check_figure_table_interpretation.py --strict`：2 个输出块，0 个未通过。 |
| 解读质量 | 通过 | `check_interpretation_quality.py --strict --check-title-style --check-actionability`：inline R=11，当前数据观察=3。 |
| htmlwidget 可见性 | 通过 | `check_htmlwidget_visibility.py` 退出码 0。 |
| 数字可追溯 | 通过 | 关键分数、顺序、斜率、Spearman、耗时和协议计数均由 inline R 引用 RDS 变量。 |
| 图表 PDF | 通过 | `tmp/quality_analysis/figures/quality-score.pdf` 与 `wall-time.pdf`。 |
| JPG 视觉复核 | 通过 | 两图均无裁切/溢出，字体和线点清晰；成本图经复核改为从 0 起轴，避免夸大差异。 |
| HTML 渲染 | 通过 | `knit-rmd-html` 成功生成 `quality_analysis.html`。 |
| 讨论与分析 | 通过 | 报告末尾包含主要发现、局限、可执行后续和数字准确性验证。 |

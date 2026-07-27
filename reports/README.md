# 研发报告目录

按日期组织研发过程与结论报告，用于系统架构调优决策。

## 目录结构

```
reports/
├── README.md                       # 本说明文件
├── 2026-07-27/                     # 按日期组织（YYYY-MM-DD）
│   ├── supervision_report.md       # 监督报告
│   ├── architecture_tuning.md      # 架构调优建议
│   └── validation_results.md       # 验证结果报告
└── ...
```

## 命名约定

- 目录名：`YYYY-MM-DD`（如 `2026-07-27`），对应报告生成日期
- 文件名：`{类型}_{简短描述}.md`，如 `supervision_htr_run.md`
- 同一日期多个报告按类型前缀排序：`analysis_*`、`supervision_*`、`validation_*`、`architecture_*`

## 报告类型

| 前缀 | 用途 |
|------|------|
| `supervision_*` | HTR 研发循环监督报告（合规性、判据、产出） |
| `validation_*` | 策略验证结果（双季度、OOS、walk-forward 等） |
| `analysis_*` | 数据/性能/根因分析报告 |
| `architecture_*` | 架构调优建议与设计文档 |

## 与数据目录 reports 的区别

- `reports/`（本目录，项目源码内）：**研发过程报告**，随代码版本管理，供团队评审
- `$LONG_EARN_DATA_DIR/reports/`（数据目录）：**运行时产出报告**，不进入版本控制，仅作本地存档

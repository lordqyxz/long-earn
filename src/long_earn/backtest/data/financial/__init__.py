"""财务数据细表 schema + 连接器财务概念实现 — ADR-014 阶段 B。

替代旧 ``financial_quarterly`` 单一宽表（22 列，4 表合并），改为按 xtquant 8 张
源表各自独立 schema，由 ``FinancialSchemaRegistry`` 统一管理。

设计要点：
- 标量表（6 张）→ 宽表，主键 ``(symbol, report_date)``，季度一行
- 长表（2 张 Top10）→ 主键 ``(symbol, report_date, rank)``，每季 10 行
- 字段映射单一事实源：xtquant 原始字段 → 标准字段名，候选顺序兜底
- 衍生指标声明（``roe = net_profit / total_equity 年化``），连接器计算
- DDL 从 schema 反射生成，``cache.py`` 与 ``miniqmt_provider.py`` 共用此源
"""

from long_earn.backtest.data.financial.schemas import (
    FinancialColumn,
    FinancialSchemaRegistry,
    FinancialTableSchema,
)

__all__ = [
    "FinancialColumn",
    "FinancialSchemaRegistry",
    "FinancialTableSchema",
]

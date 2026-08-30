"""财务数据细表 schema + 面板组装 + 缓存同步 — ADR-014 阶段 B。

分层：
- ``schemas``：表结构 / DDL 单一事实源
- ``sync``：缓存缺失/过期检测，委托数据源写回 PostgreSQL
- ``panel``：从缓存季频组装引擎消费的日频 PIT 视图（与 miniqmt 解耦）

子模块按需 import，避免 ``cache`` ↔ ``financial.panel`` 循环依赖。
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

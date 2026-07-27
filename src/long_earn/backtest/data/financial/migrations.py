"""财务数据迁移 — ADR-014 阶段 B。

把旧 ``financial_quarterly`` 宽表（22 列，4 表合并）拆到 4 张新标量细表
（income_stmt / balance_sheet / cashflow_stmt / pershareindex）。

Capital / Holdernum / Top10 两表无旧数据，需 ``download_data.py`` 重下
（脚本改造后自动覆盖）。

迁移后旧表保留，重命名为 ``financial_quarterly_v1_deprecated``，不删除
（遵守 AGENTS.md 缓存保护约定：不得主动修改缓存，除非明确必要理由）。
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import pandas as pd
from loguru import logger

from long_earn.backtest.data.financial.schemas import FinancialSchemaRegistry

# ── 旧宽表 22 列 → 新细表字段归属 ─────────────────────────────────────
# 主键列 symbol/report_date/announce_date 每张表都要带
# 衍生指标（net_profit_yoy/revenue_yoy/roe/gross_margin）归 pershareindex
#   （旧实现里它们来自 Pershareindex 预计算值或手算兜底，与 pershareindex 同表）

_INCOME_FIELDS = (
    "revenue",
    "net_profit",
    "eps",
    "research_expenses",
    "total_operating_cost",
)
# 注意：旧宽表没有 total_operating_cost 列（旧 _FINANCIAL_QUARTERLY_COLUMNS 未含），
# 迁移时该列在 income_stmt 中为 NULL，后续重下或连接器手算毛利率时补充。
# 旧宽表实际有的 Income 字段：
_INCOME_FIELDS_LEGACY = ("revenue", "net_profit", "eps", "research_expenses")

_BALANCE_FIELDS = ("total_equity", "total_assets", "total_liabilities")

_CASHFLOW_FIELDS = ("ocf", "capex")

_PERSHAREINDEX_FIELDS = (
    "bps",
    "ocf_per_share",
    "debt_to_assets",
    "net_profit_margin",
    "roe_weighted",
    "net_profit_yoy",
    "revenue_yoy",
    "roe",
    "gross_margin",
)


@dataclass
class MigrationReport:
    """迁移结果摘要。"""

    migrated_rows: int = 0
    tables_written: dict[str, int] = None  # type: ignore[assignment]
    deprecated_table: str = ""
    skipped: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if self.tables_written is None:
            self.tables_written = {}


def _table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """检查表是否存在。"""
    result = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [table_name],
    ).fetchone()
    return result is not None and result[0] > 0


def _table_row_count(conn: duckdb.DuckDBPyConnection, table_name: str) -> int:
    """安全获取表行数（表不存在返回 0）。"""
    if not _table_exists(conn, table_name):
        return 0
    return conn.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0]


def needs_migration(conn: duckdb.DuckDBPyConnection) -> bool:
    """判断是否需要迁移：旧表存在 + 新标量表为空。

    迁移幂等：若新表已有数据，说明已迁移过，跳过。
    """
    if not _table_exists(conn, "financial_quarterly"):
        return False
    # 检查任一新标量表是否已有数据
    for schema in FinancialSchemaRegistry.scalar_tables()[:4]:  # 前 4 张是旧表对应
        if _table_row_count(conn, schema.table_name) > 0:
            return False  # 已迁移
    return True


def migrate_financial_quarterly(conn: duckdb.DuckDBPyConnection) -> MigrationReport:
    """把旧 financial_quarterly 拆到 4 张新标量表。

    Args:
        conn: DuckDB 连接（已初始化 8 张新表）

    Returns:
        MigrationReport 摘要
    """
    if not needs_migration(conn):
        return MigrationReport(
            skipped=True, reason="无需迁移（旧表不存在或新表已有数据）"
        )

    logger.info("开始迁移 financial_quarterly → 4 张新标量细表")

    # 读取旧表全量
    old_df: pd.DataFrame = conn.execute("SELECT * FROM financial_quarterly").fetchdf()
    if old_df.empty:
        return MigrationReport(skipped=True, reason="旧表为空")

    total_rows = len(old_df)
    report = MigrationReport(migrated_rows=total_rows)

    # 按字段归属拆分并写入 4 张新表
    table_field_map = {
        "income_stmt": _INCOME_FIELDS_LEGACY,
        "balance_sheet": _BALANCE_FIELDS,
        "cashflow_stmt": _CASHFLOW_FIELDS,
        "pershareindex": _PERSHAREINDEX_FIELDS,
    }

    for table_name, fields in table_field_map.items():
        schema = FinancialSchemaRegistry.get_table(table_name)
        # 主键 + announce_date + 该表字段
        needed_cols = ["symbol", "report_date", "announce_date", *fields]
        # 只取旧表中存在的列（total_operating_cost 旧表没有，跳过）
        available_cols = [c for c in needed_cols if c in old_df.columns]
        sub_df = old_df[available_cols].copy()

        if sub_df.empty:
            report.tables_written[table_name] = 0
            continue

        # 补齐 schema 定义但旧表缺失的列为 NULL（如 income_stmt 的 total_operating_cost）
        for col in schema.columns:
            if col.name not in sub_df.columns:
                sub_df[col.name] = None

        # 按 schema 列顺序对齐
        schema_cols = [c.name for c in schema.columns]
        sub_df = sub_df[schema_cols]

        # 写入新表（INSERT OR REPLACE，主键幂等）
        col_list = ", ".join(schema_cols)
        conn.execute(
            f"INSERT OR REPLACE INTO {table_name} ({col_list}) "
            f"SELECT {col_list} FROM sub_df"
        )
        report.tables_written[table_name] = len(sub_df)
        logger.info(f"迁移 {table_name}: {len(sub_df)} 行")

    # 旧表重命名保留（不删除）
    deprecated_name = "financial_quarterly_v1_deprecated"
    # 若 deprecated 表已存在，先删它（保留最新迁移的旧表快照）
    conn.execute(f"DROP TABLE IF EXISTS {deprecated_name}")
    conn.execute(f"ALTER TABLE financial_quarterly RENAME TO {deprecated_name}")
    report.deprecated_table = deprecated_name

    # 更新 schema_meta 版本
    conn.execute(
        "INSERT OR REPLACE INTO _schema_meta VALUES ('financial_quarterly', ?)",
        [FinancialSchemaRegistry.SCHEMA_VERSION],
    )

    logger.info(
        f"迁移完成: {total_rows} 行拆到 4 表 {report.tables_written}，"
        f"旧表保留为 {deprecated_name}"
    )
    return report

"""财务数据迁移测试 — ADR-014 阶段 B。

验证旧 financial_quarterly 22 列拆到 4 张新标量表，字段归属不丢不重。
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from long_earn.backtest.data.financial.migrations import (
    migrate_financial_quarterly,
    needs_migration,
)
from long_earn.backtest.data.financial.schemas import FinancialSchemaRegistry


@pytest.fixture()
def mock_conn_with_old_table() -> duckdb.DuckDBPyConnection:
    """构造内存 DuckDB，含旧 financial_quarterly + 8 张新空表。"""
    conn = duckdb.connect(":memory:")
    # 建旧宽表（22 列，与旧 _FINANCIAL_QUARTERLY_COLUMNS 对齐）
    conn.execute("""
        CREATE TABLE financial_quarterly (
            symbol VARCHAR NOT NULL,
            report_date DATE NOT NULL,
            announce_date DATE NOT NULL,
            revenue DOUBLE, net_profit DOUBLE, eps DOUBLE, research_expenses DOUBLE,
            total_equity DOUBLE, total_assets DOUBLE, total_liabilities DOUBLE,
            ocf DOUBLE, capex DOUBLE,
            bps DOUBLE, ocf_per_share DOUBLE, debt_to_assets DOUBLE,
            net_profit_margin DOUBLE, roe_weighted DOUBLE,
            net_profit_yoy DOUBLE, revenue_yoy DOUBLE, roe DOUBLE, gross_margin DOUBLE,
            PRIMARY KEY (symbol, report_date)
        )
    """)
    # 建 8 张新表（从 schema 反射）
    for schema in FinancialSchemaRegistry.TABLES:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {schema.table_name} ({schema.column_ddl()})"
        )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _schema_meta (
            table_name VARCHAR PRIMARY KEY, version INTEGER NOT NULL
        )
    """)
    # 插入 3 行旧数据（变量名须保留：DuckDB 按名引用 Python DataFrame）
    old_rows = pd.DataFrame(  # noqa: F841
        [
            {
                "symbol": "600519.SH",
                "report_date": "2024-09-30",
                "announce_date": "2024-10-30",
                "revenue": 1e10,
                "net_profit": 5e9,
                "eps": 39.8,
                "research_expenses": 1e8,
                "total_equity": 2e10,
                "total_assets": 3e10,
                "total_liabilities": 1e10,
                "ocf": 4e9,
                "capex": 5e8,
                "bps": 159.0,
                "ocf_per_share": 31.8,
                "debt_to_assets": 0.33,
                "net_profit_margin": 0.5,
                "roe_weighted": 0.25,
                "net_profit_yoy": 0.15,
                "revenue_yoy": 0.10,
                "roe": 0.25,
                "gross_margin": 0.91,
            },
            {
                "symbol": "000001.SZ",
                "report_date": "2024-09-30",
                "announce_date": "2024-10-25",
                "revenue": 2e10,
                "net_profit": 8e9,
                "eps": 1.5,
                "research_expenses": 2e8,
                "total_equity": 4e10,
                "total_assets": 1e11,
                "total_liabilities": 6e10,
                "ocf": 9e9,
                "capex": 1e9,
                "bps": 20.0,
                "ocf_per_share": 2.5,
                "debt_to_assets": 0.6,
                "net_profit_margin": 0.4,
                "roe_weighted": 0.20,
                "net_profit_yoy": 0.08,
                "revenue_yoy": 0.05,
                "roe": 0.20,
                "gross_margin": 0.50,
            },
            {
                "symbol": "600519.SH",
                "report_date": "2024-06-30",
                "announce_date": "2024-08-28",
                "revenue": 8e9,
                "net_profit": 4e9,
                "eps": 31.8,
                "research_expenses": 8e7,
                "total_equity": 1.9e10,
                "total_assets": 2.8e10,
                "total_liabilities": 9e9,
                "ocf": 3.5e9,
                "capex": 4e8,
                "bps": 151.0,
                "ocf_per_share": 28.0,
                "debt_to_assets": 0.32,
                "net_profit_margin": 0.5,
                "roe_weighted": 0.24,
                "net_profit_yoy": 0.14,
                "revenue_yoy": 0.09,
                "roe": 0.24,
                "gross_margin": 0.90,
            },
        ]
    )
    conn.execute("INSERT INTO financial_quarterly SELECT * FROM old_rows")
    return conn


class TestFinancialMigration:
    """旧宽表 → 4 张新细表迁移测试。"""

    def test_needs_migration_true_when_new_tables_empty(
        self,
        mock_conn_with_old_table: duckdb.DuckDBPyConnection,
    ) -> None:
        """旧表存在 + 新表空 → 需要迁移。"""
        assert needs_migration(mock_conn_with_old_table) is True

    def test_needs_migration_false_when_no_old_table(self) -> None:
        """无旧表 → 不需要迁移。"""
        conn = duckdb.connect(":memory:")
        for schema in FinancialSchemaRegistry.TABLES:
            conn.execute(f"CREATE TABLE {schema.table_name} ({schema.column_ddl()})")
        assert needs_migration(conn) is False

    def test_migration_splits_to_four_tables(
        self,
        mock_conn_with_old_table: duckdb.DuckDBPyConnection,
    ) -> None:
        """迁移后 4 张新表各有数据。"""
        conn = mock_conn_with_old_table
        report = migrate_financial_quarterly(conn)
        assert not report.skipped
        assert report.migrated_rows == 3
        # 4 张表都写入 3 行
        assert set(report.tables_written.keys()) == {
            "income_stmt",
            "balance_sheet",
            "cashflow_stmt",
            "pershareindex",
        }
        for count in report.tables_written.values():
            assert count == 3

    def test_migration_preserves_income_fields(
        self,
        mock_conn_with_old_table: duckdb.DuckDBPyConnection,
    ) -> None:
        """迁移后 income_stmt 含 revenue/net_profit/eps/research_expenses。"""
        conn = mock_conn_with_old_table
        migrate_financial_quarterly(conn)
        df = conn.execute(
            "SELECT symbol, report_date, announce_date, revenue, net_profit, eps, "
            "research_expenses FROM income_stmt WHERE symbol = '600519.SH' "
            "ORDER BY report_date"
        ).fetchdf()
        assert len(df) == 2
        # 验证最新一期的值
        latest = df.iloc[-1]
        assert latest["revenue"] == pytest.approx(1e10)
        assert latest["net_profit"] == pytest.approx(5e9)
        assert latest["eps"] == pytest.approx(39.8)
        # total_operating_cost 旧表没有，应为 NULL
        tc_df = conn.execute(
            "SELECT total_operating_cost FROM income_stmt LIMIT 1"
        ).fetchdf()
        assert pd.isna(tc_df.iloc[0]["total_operating_cost"])

    def test_migration_preserves_balance_fields(
        self,
        mock_conn_with_old_table: duckdb.DuckDBPyConnection,
    ) -> None:
        """迁移后 balance_sheet 含 total_equity/total_assets/total_liabilities。"""
        conn = mock_conn_with_old_table
        migrate_financial_quarterly(conn)
        df = conn.execute(
            "SELECT total_equity, total_assets, total_liabilities FROM balance_sheet "
            "WHERE symbol = '600519.SH' AND report_date = '2024-09-30'"
        ).fetchdf()
        assert len(df) == 1
        row = df.iloc[0]
        assert row["total_equity"] == pytest.approx(2e10)
        assert row["total_assets"] == pytest.approx(3e10)
        assert row["total_liabilities"] == pytest.approx(1e10)

    def test_migration_preserves_pershareindex_derived_fields(
        self,
        mock_conn_with_old_table: duckdb.DuckDBPyConnection,
    ) -> None:
        """迁移后 pershareindex 含衍生指标（net_profit_yoy/roe/gross_margin）。"""
        conn = mock_conn_with_old_table
        migrate_financial_quarterly(conn)
        df = conn.execute(
            "SELECT roe, gross_margin, net_profit_yoy, revenue_yoy, roe_weighted, bps "
            "FROM pershareindex WHERE symbol = '600519.SH' AND report_date = '2024-09-30'"
        ).fetchdf()
        assert len(df) == 1
        row = df.iloc[0]
        assert row["roe"] == pytest.approx(0.25)
        assert row["gross_margin"] == pytest.approx(0.91)
        assert row["net_profit_yoy"] == pytest.approx(0.15)
        assert row["roe_weighted"] == pytest.approx(0.25)

    def test_migration_renames_old_table_to_deprecated(
        self,
        mock_conn_with_old_table: duckdb.DuckDBPyConnection,
    ) -> None:
        """迁移后旧表重命名为 financial_quarterly_v1_deprecated（不删除）。"""
        conn = mock_conn_with_old_table
        report = migrate_financial_quarterly(conn)
        assert report.deprecated_table == "financial_quarterly_v1_deprecated"
        # 旧表已不存在
        result = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'financial_quarterly'"
        ).fetchone()
        assert result[0] == 0
        # deprecated 表存在且含原数据
        dep_count = conn.execute(
            "SELECT count(*) FROM financial_quarterly_v1_deprecated"
        ).fetchone()[0]
        assert dep_count == 3

    def test_migration_is_idempotent(
        self,
        mock_conn_with_old_table: duckdb.DuckDBPyConnection,
    ) -> None:
        """迁移幂等：第二次调用跳过（新表已有数据）。"""
        conn = mock_conn_with_old_table
        migrate_financial_quarterly(conn)
        report2 = migrate_financial_quarterly(conn)
        assert report2.skipped
        assert "无需迁移" in report2.reason

    def test_migration_updates_schema_version(
        self,
        mock_conn_with_old_table: duckdb.DuckDBPyConnection,
    ) -> None:
        """迁移后 _schema_meta 记录新版本号。"""
        conn = mock_conn_with_old_table
        migrate_financial_quarterly(conn)
        version = conn.execute(
            "SELECT version FROM _schema_meta WHERE table_name = 'financial_quarterly'"
        ).fetchone()
        assert version is not None
        assert version[0] == FinancialSchemaRegistry.SCHEMA_VERSION

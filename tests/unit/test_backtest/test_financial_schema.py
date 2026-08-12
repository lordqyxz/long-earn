"""财务 schema 注册表测试 — ADR-014 阶段 B。

验证 8 表 schema 定义正确性、DDL 反射、字段反查逻辑。
"""

from __future__ import annotations

import pytest

from long_earn.backtest.data.financial.schemas import (
    DERIVED_METRICS,
    FinancialSchemaRegistry,
)


class TestFinancialSchemaRegistry:
    """8 表 schema 注册表测试。"""

    def test_eight_tables_registered(self) -> None:
        """8 张表全部注册。"""
        assert len(FinancialSchemaRegistry.TABLES) == 8

    def test_six_scalar_two_long(self) -> None:
        """6 张标量宽表 + 2 张长表。"""
        assert len(FinancialSchemaRegistry.scalar_tables()) == 6
        assert len(FinancialSchemaRegistry.long_tables()) == 2

    def test_xt_table_names_match(self) -> None:
        """xtquant 表名与文档一致。"""
        xt_names = {s.xt_table for s in FinancialSchemaRegistry.TABLES}
        assert xt_names == {
            "Income",
            "Balance",
            "CashFlow",
            "Pershareindex",
            "Capital",
            "Holdernum",
            "Top10holder",
            "Top10flowholder",
        }

    def test_get_table_by_name(self) -> None:
        """按 DuckDB 表名查 schema。"""
        schema = FinancialSchemaRegistry.get_table("income_stmt")
        assert schema.xt_table == "Income"
        with pytest.raises(KeyError):
            FinancialSchemaRegistry.get_table("not_exist")

    def test_get_by_xt_table(self) -> None:
        """按 xtquant 表名查 schema。"""
        schema = FinancialSchemaRegistry.get_by_xt_table("Holdernum")
        assert schema.table_name == "holdernum"
        # 验证股东户数字段存在
        field_names = schema.standard_field_names
        assert "shareholder_total" in field_names
        assert "shareholder_a" in field_names
        assert "shareholder_float" in field_names

    def test_top10_long_table_has_rank_pk(self) -> None:
        """Top10 长表主键含 rank。"""
        schema = FinancialSchemaRegistry.get_table("top10_holders")
        assert not schema.is_scalar
        assert "rank" in schema.primary_key
        assert schema.primary_key == ("symbol", "report_date", "rank")

    def test_column_ddl_generates_primary_key(self) -> None:
        """DDL 反射生成主键约束。"""
        schema = FinancialSchemaRegistry.get_table("income_stmt")
        ddl = schema.column_ddl()
        assert "PRIMARY KEY (symbol, report_date)" in ddl
        assert "symbol VARCHAR NOT NULL" in ddl
        assert "report_date DATE NOT NULL" in ddl
        assert "announce_date DATE NOT NULL" in ddl
        assert "revenue DOUBLE" in ddl

    def test_top10_ddl_has_rank_pk(self) -> None:
        """Top10 DDL 主键含 rank。"""
        schema = FinancialSchemaRegistry.get_table("top10_holders")
        ddl = schema.column_ddl()
        assert "PRIMARY KEY (symbol, report_date, rank)" in ddl
        assert "rank INTEGER NOT NULL" in ddl

    def test_field_to_table_resolution(self) -> None:
        """按字段反查涉及哪些表（连接器按需 join 用）。"""
        # roe 只在 pershareindex
        tables = FinancialSchemaRegistry.tables_for_fields({"roe"})
        assert {t.table_name for t in tables} == {"pershareindex"}

        # revenue 在 income_stmt，gross_margin 在 pershareindex
        tables = FinancialSchemaRegistry.tables_for_fields({"revenue", "gross_margin"})
        assert {t.table_name for t in tables} == {"income_stmt", "pershareindex"}

        # total_equity 在 balance_sheet，ocf 在 cashflow_stmt
        tables = FinancialSchemaRegistry.tables_for_fields({"total_equity", "ocf"})
        assert {t.table_name for t in tables} == {"balance_sheet", "cashflow_stmt"}

    def test_standard_field_names_covers_all_scalar(self) -> None:
        """standard_field_names 覆盖所有标量表字段。"""
        names = FinancialSchemaRegistry.standard_field_names()
        # 来自 6 张标量表
        assert "revenue" in names  # income
        assert "total_equity" in names  # balance
        assert "ocf" in names  # cashflow
        assert "roe" in names  # pershareindex
        assert "total_shares" in names  # capital
        assert "shareholder_total" in names  # holdernum
        # Top10 长表字段不应在标量字段集里
        assert "holder_name" not in names

    def test_derived_metrics_declared(self) -> None:
        """4 个衍生指标已声明。"""
        metric_names = FinancialSchemaRegistry.derived_metric_names()
        assert metric_names == {"gross_margin", "net_profit_yoy", "revenue_yoy", "roe"}
        # 衍生指标依赖检查
        roe_metric = next(m for m in DERIVED_METRICS if m.name == "roe")
        assert set(roe_metric.depends_on) == {"net_profit", "total_equity"}

    def test_balance_multi_candidate_fields(self) -> None:
        """Balance total_equity 有多候选兜底字段。"""
        schema = FinancialSchemaRegistry.get_table("balance_sheet")
        total_equity = next(c for c in schema.columns if c.name == "total_equity")
        # 至少 3 个候选（与旧 _extract_balance_fields 对齐）
        assert len(total_equity.xt_fields) >= 3
        assert "total_equity" in total_equity.xt_fields
        assert "tot_shrhldr_eqy_excl_min_int" in total_equity.xt_fields

    def test_schema_version_is_3(self) -> None:
        """schema 版本 = 3（v1=旧宽表, v2=8表拆分, v3=CashFlow扩展）。"""
        assert FinancialSchemaRegistry.SCHEMA_VERSION == 3

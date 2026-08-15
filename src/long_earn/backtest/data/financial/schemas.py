"""8 张 xtquant 财务表的 schema 注册表 — ADR-014 阶段 B。

单一事实源：``cache.py`` 建表 DDL、``miniqmt_provider.py`` 取数字段映射、
``Connector`` 衍生指标计算，全部从此模块读取，消灭旧 ``cache.py`` 与
``miniqmt_provider.py`` 两处硬编码字段清单手动对齐的脆弱性。

覆盖 xtquant ``get_financial_data`` 全部 8 张表（旧实现只接 4 张，遗漏
Capital/Holdernum/Top10holder/Top10flowholder）：

| xtquant 表 | PG 表 | 形态 | 主键 |
|------------|-------|------|------|
| Income | income_stmt | 标量宽表 | (symbol, report_date) |
| Balance | balance_sheet | 标量宽表 | (symbol, report_date) |
| CashFlow | cashflow_stmt | 标量宽表 | (symbol, report_date) |
| Pershareindex | pershareindex | 标量宽表 | (symbol, report_date) |
| Capital | capital | 标量宽表 | (symbol, report_date) |
| Holdernum | holdernum | 标量宽表 | (symbol, report_date) |
| Top10holder | top10_holders | 长表 | (symbol, report_date, rank) |
| Top10flowholder | top10_flow_holders | 长表 | (symbol, report_date, rank) |

**实施数据可用性**（ADR-014 阶段 B 验证结果）：

- 前 5 张表（Income/Balance/CashFlow/Pershareindex/Capital）通过
  ``xtdata.get_financial_data`` 可正常取数，缓存表已填充。
- 后 3 张表（Holdernum/Top10holder/Top10flowholder）在标准 miniQMT 终端
  配置下 ``get_financial_data`` 返回空。可能原因：
  1. miniQMT 终端未开启这 3 张表的下载权限
  2. 这 3 张表需要不同的 xtquant API（如 ``get_holder_data``）
  3. 数据量过大，终端默认不下载

  这 3 张表的 schema 保留供未来扩展，但 ``Connector`` 当前不查询它们，
  避免误导。待 miniQMT 终端配置或找到正确 API 后再启用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# 列类型别名（PostgreSQL 方言；DOUBLE 在 column_ddl 时映射为 DOUBLE PRECISION）
PgType = Literal["VARCHAR", "DOUBLE", "DATE", "INTEGER", "BIGINT"]

# DuckDB 时代类型 → PostgreSQL 类型映射（DDL 生成时应用）
_PG_TYPE_MAP: dict[str, str] = {
    "VARCHAR": "VARCHAR",
    "DOUBLE": "DOUBLE PRECISION",
    "DATE": "DATE",
    "INTEGER": "INTEGER",
    "BIGINT": "BIGINT",
}


@dataclass(frozen=True)
class FinancialColumn:
    """单列定义：标准列名 + 类型 + xtquant 原始字段候选 + 可空性。

    ``xt_fields`` 按优先级排序，``_extract_by_schema`` 取第一个存在的原始列。
    主键列（symbol/report_date/rank）的 ``xt_fields`` 为空——它们由
    ``MiniQmtClient.get_financial`` 统一从 ``m_timetag`` / ``m_anntime`` 提取。
    """

    name: str
    dtype: PgType
    xt_fields: tuple[str, ...] = field(default_factory=tuple)
    nullable: bool = True

    @property
    def is_primary_key(self) -> bool:
        """主键列无 xt_fields（由 client 统一提取）。"""
        return not self.xt_fields


@dataclass(frozen=True)
class FinancialTableSchema:
    """单张财务表的 schema：xtquant 表名 + PG 表名 + 列定义 + 主键 + 形态。

    ``is_scalar=True`` 表示标量宽表（季度一行），``False`` 表示长表（每季多行，
    如 Top10 每季 10 行，主键含 rank）。
    """

    xt_table: str  # xtquant 原始表名（如 "Income"）
    table_name: str  # PG 表名（如 "income_stmt"）
    columns: tuple[FinancialColumn, ...]
    primary_key: tuple[str, ...]  # 主键列名
    is_scalar: bool = True  # 标量宽表 vs 长表

    @property
    def pk_columns(self) -> tuple[FinancialColumn, ...]:
        """主键列对象。"""
        pk_set = set(self.primary_key)
        return tuple(c for c in self.columns if c.name in pk_set)

    @property
    def data_columns(self) -> tuple[FinancialColumn, ...]:
        """非主键列对象（含 announce_date，它是 NOT NULL 但非主键）。"""
        pk_set = set(self.primary_key)
        return tuple(c for c in self.columns if c.name not in pk_set)

    @property
    def standard_field_names(self) -> tuple[str, ...]:
        """该表标准字段名（含主键 + 非主键），供连接器按需取数判断。"""
        return tuple(c.name for c in self.columns)

    def column_ddl(self) -> str:
        """生成 PostgreSQL ``CREATE TABLE`` 的列定义 + 主键 DDL（从 schema 反射）。"""
        col_defs: list[str] = []
        for col in self.columns:
            null_spec = "" if col.nullable else " NOT NULL"
            pg_type = _PG_TYPE_MAP.get(col.dtype, col.dtype)
            col_defs.append(f"{col.name} {pg_type}{null_spec}")
        pk = ", ".join(self.primary_key)
        col_defs.append(f"PRIMARY KEY ({pk})")
        return ",\n    ".join(col_defs)


# ── 主键列常量（8 表共用）──────────────────────────────────────────────

_SYMBOL_COL = FinancialColumn("symbol", "VARCHAR", nullable=False)
_REPORT_DATE_COL = FinancialColumn("report_date", "DATE", nullable=False)
_ANNOUNCE_DATE_COL = FinancialColumn("announce_date", "DATE", nullable=False)
_RANK_COL = FinancialColumn("rank", "INTEGER", nullable=False)


# ── 8 张表 schema 定义 ────────────────────────────────────────────────

INCOME_SCHEMA = FinancialTableSchema(
    xt_table="Income",
    table_name="income_stmt",
    primary_key=("symbol", "report_date"),
    is_scalar=True,
    columns=(
        _SYMBOL_COL,
        _REPORT_DATE_COL,
        _ANNOUNCE_DATE_COL,
        FinancialColumn("revenue", "DOUBLE", ("revenue_inc", "revenue")),
        FinancialColumn("net_profit", "DOUBLE", ("net_profit_incl_min_int_inc",)),
        FinancialColumn("eps", "DOUBLE", ("s_fa_eps_basic",)),
        FinancialColumn("research_expenses", "DOUBLE", ("research_expenses",)),
        # 手算毛利率用：(revenue - total_operating_cost) / revenue
        FinancialColumn("total_operating_cost", "DOUBLE", ("total_operating_cost",)),
    ),
)

BALANCE_SCHEMA = FinancialTableSchema(
    xt_table="Balance",
    table_name="balance_sheet",
    primary_key=("symbol", "report_date"),
    is_scalar=True,
    columns=(
        _SYMBOL_COL,
        _REPORT_DATE_COL,
        _ANNOUNCE_DATE_COL,
        # 多候选兜底（与旧 _extract_balance_fields 对齐）
        FinancialColumn(
            "total_equity",
            "DOUBLE",
            (
                "total_equity",
                "tot_shrhldr_eqy_excl_min_int",
                "total_hldr_eqy_exc_min_int",
                "total_hldr_eqy_incl_min_int",
                "s_fa_total_hldr_eqy_exc_min_int",
            ),
        ),
        FinancialColumn("total_assets", "DOUBLE", ("tot_assets",)),
        FinancialColumn("total_liabilities", "DOUBLE", ("tot_liab",)),
    ),
)

CASHFLOW_SCHEMA = FinancialTableSchema(
    xt_table="CashFlow",
    table_name="cashflow_stmt",
    primary_key=("symbol", "report_date"),
    is_scalar=True,
    columns=(
        _SYMBOL_COL,
        _REPORT_DATE_COL,
        _ANNOUNCE_DATE_COL,
        FinancialColumn("ocf", "DOUBLE", ("net_cash_flows_oper_act",)),
        FinancialColumn("capex", "DOUBLE", ("cash_pay_acq_const_fiolta",)),
        # 阶段 C 扩展：三大活动现金流 + 净增加额 + 销售收现
        # 字段名来自 xtquant CashFlow 表实测（2026-08-12 验证）
        FinancialColumn("investing_cf", "DOUBLE", ("net_cash_flows_inv_act",)),
        FinancialColumn("financing_cf", "DOUBLE", ("net_cash_flows_fnc_act",)),
        FinancialColumn("net_cash_change", "DOUBLE", ("net_incr_cash_cash_equ",)),
        FinancialColumn("cash_from_sales", "DOUBLE", ("goods_sale_and_service_render_cash", "m_cashSellingProvidingServices")),
    ),
)

PERSHAREINDEX_SCHEMA = FinancialTableSchema(
    xt_table="Pershareindex",
    table_name="pershareindex",
    primary_key=("symbol", "report_date"),
    is_scalar=True,
    columns=(
        _SYMBOL_COL,
        _REPORT_DATE_COL,
        _ANNOUNCE_DATE_COL,
        FinancialColumn("bps", "DOUBLE", ("s_fa_bps",)),
        FinancialColumn("ocf_per_share", "DOUBLE", ("s_fa_ocfps",)),
        FinancialColumn("debt_to_assets", "DOUBLE", ("gear_ratio",)),
        # 注意：Pershareindex 的 net_profit 列实为净利率（旧代码注释已确认）
        FinancialColumn("net_profit_margin", "DOUBLE", ("net_profit",)),
        FinancialColumn("roe_weighted", "DOUBLE", ("equity_roe",)),
        # 预计算衍生指标（优先值，手算仅兜底）
        FinancialColumn("roe", "DOUBLE", ("du_return_on_equity",)),
        FinancialColumn("gross_margin", "DOUBLE", ("gross_profit",)),
        FinancialColumn("net_profit_yoy", "DOUBLE", ("du_profit_rate",)),
        FinancialColumn("revenue_yoy", "DOUBLE", ("inc_revenue_rate",)),
    ),
)

CAPITAL_SCHEMA = FinancialTableSchema(
    xt_table="Capital",
    table_name="capital",
    primary_key=("symbol", "report_date"),
    is_scalar=True,
    columns=(
        _SYMBOL_COL,
        _REPORT_DATE_COL,
        _ANNOUNCE_DATE_COL,
        # xtquant Capital 表实际字段（经 _debug_capital.py 验证 2026-07-19）：
        # total_capital=总股本, circulating_capital=流通股本,
        # restrict_circulating_capital=限售流通股本, freeFloatCapital=自由流通股本
        FinancialColumn(
            "total_shares",
            "DOUBLE",
            ("total_capital", "total_share", "total_shr"),
        ),
        FinancialColumn(
            "float_shares",
            "DOUBLE",
            (
                "circulating_capital",
                "float_share",
                "float_shr",
                "freeFloatCapital",
            ),
        ),
        FinancialColumn("change_reason", "VARCHAR", ("change_reason",)),
    ),
)

HOLDERNUM_SCHEMA = FinancialTableSchema(
    xt_table="Holdernum",
    table_name="holdernum",
    primary_key=("symbol", "report_date"),
    is_scalar=True,
    columns=(
        _SYMBOL_COL,
        _REPORT_DATE_COL,
        _ANNOUNCE_DATE_COL,
        # xtquant Holdernum/SHAREHOLDER 字段
        FinancialColumn("shareholder_total", "DOUBLE", ("shareholder",)),
        FinancialColumn("shareholder_a", "DOUBLE", ("shareholderA",)),
        FinancialColumn("shareholder_b", "DOUBLE", ("shareholderB",)),
        FinancialColumn("shareholder_h", "DOUBLE", ("shareholderH",)),
        FinancialColumn("shareholder_float", "DOUBLE", ("shareholderFloat",)),
        FinancialColumn("shareholder_other", "DOUBLE", ("shareholderOther",)),
    ),
)

TOP10_HOLDER_SCHEMA = FinancialTableSchema(
    xt_table="Top10holder",
    table_name="top10_holders",
    primary_key=("symbol", "report_date", "rank"),
    is_scalar=False,  # 长表：每季 10 行
    columns=(
        _SYMBOL_COL,
        _REPORT_DATE_COL,
        _ANNOUNCE_DATE_COL,
        _RANK_COL,
        FinancialColumn("holder_name", "VARCHAR", ("holder_name", "holderName")),
        FinancialColumn("hold_amount", "DOUBLE", ("hold_amount", "holdAmount")),
        FinancialColumn("hold_ratio", "DOUBLE", ("hold_ratio", "holdRatio")),
        FinancialColumn("holder_nature", "VARCHAR", ("holder_nature", "holderNature")),
    ),
)

TOP10_FLOWHOLDER_SCHEMA = FinancialTableSchema(
    xt_table="Top10flowholder",
    table_name="top10_flow_holders",
    primary_key=("symbol", "report_date", "rank"),
    is_scalar=False,
    columns=(
        _SYMBOL_COL,
        _REPORT_DATE_COL,
        _ANNOUNCE_DATE_COL,
        _RANK_COL,
        FinancialColumn("holder_name", "VARCHAR", ("holder_name", "holderName")),
        FinancialColumn("hold_amount", "DOUBLE", ("hold_amount", "holdAmount")),
        FinancialColumn("hold_ratio", "DOUBLE", ("hold_ratio", "holdRatio")),
        FinancialColumn("holder_nature", "VARCHAR", ("holder_nature", "holderNature")),
    ),
)


# ── 衍生指标声明（跨表计算，不落 xt_fields）──────────────────────────


@dataclass(frozen=True)
class DerivedMetric:
    """衍生指标：标准名 + 依赖字段 + 计算方式标识。

    衍生指标在 ``Connector._compute_derived`` 中基于已 join 的标量表数据计算，
    Pershareindex 预计算值优先（如 ``roe_weighted`` 优于手算 ``roe``），
    仅在预计算值缺失时手算兜底。计算逻辑从旧 ``_compute_derived_financials``
    搬迁，保持 PIT 与年化系数不变。
    """

    name: str  # 标准字段名（如 "roe"）
    depends_on: tuple[str, ...]  # 依赖的标准字段（如 ("net_profit","total_equity")）
    method: Literal["ratio", "yoy", "gross_margin"]


DERIVED_METRICS: tuple[DerivedMetric, ...] = (
    DerivedMetric("gross_margin", ("revenue", "total_operating_cost"), "gross_margin"),
    DerivedMetric("net_profit_yoy", ("net_profit",), "yoy"),
    DerivedMetric("revenue_yoy", ("revenue",), "yoy"),
    DerivedMetric("roe", ("net_profit", "total_equity"), "ratio"),
)

# 季度年化系数（与旧 _fill_roe 一致）：Q1×4, Q2×2, Q3×4/3, Q4×1
QUARTER_ANNUALIZATION: dict[int, float] = {1: 4.0, 2: 2.0, 3: 4.0 / 3.0, 4: 1.0}


# ── Schema 注册表 ──────────────────────────────────────────────────────


class FinancialSchemaRegistry:
    """8 张表 schema 的注册表 — 单一事实源。

    ``cache.py`` 建表：``for schema in FinancialSchemaRegistry.TABLES: DDL``
    ``miniqmt_provider.py`` 取数：``schema.xt_table`` + ``col.xt_fields``
    ``Connector`` 衍生：``FinancialSchemaRegistry.DERIVED_METRICS``
    """

    SCHEMA_VERSION = 3  # v1=financial_quarterly, v2=8表拆分, v3=CashFlow扩展
    TABLES: tuple[FinancialTableSchema, ...] = (
        INCOME_SCHEMA,
        BALANCE_SCHEMA,
        CASHFLOW_SCHEMA,
        PERSHAREINDEX_SCHEMA,
        CAPITAL_SCHEMA,
        HOLDERNUM_SCHEMA,
        TOP10_HOLDER_SCHEMA,
        TOP10_FLOWHOLDER_SCHEMA,
    )

    @classmethod
    def get_table(cls, table_name: str) -> FinancialTableSchema:
        """按 DuckDB 表名查 schema。"""
        for schema in cls.TABLES:
            if schema.table_name == table_name:
                return schema
        raise KeyError(f"未知财务表: {table_name}")

    @classmethod
    def get_by_xt_table(cls, xt_table: str) -> FinancialTableSchema:
        """按 xtquant 表名查 schema。"""
        for schema in cls.TABLES:
            if schema.xt_table == xt_table:
                return schema
        raise KeyError(f"未知 xtquant 表: {xt_table}")

    @classmethod
    def scalar_tables(cls) -> tuple[FinancialTableSchema, ...]:
        """所有标量宽表（6 张）。"""
        return tuple(s for s in cls.TABLES if s.is_scalar)

    @classmethod
    def long_tables(cls) -> tuple[FinancialTableSchema, ...]:
        """所有长表（2 张 Top10）。"""
        return tuple(s for s in cls.TABLES if not s.is_scalar)

    @classmethod
    def standard_field_names(cls) -> frozenset[str]:
        """所有标量表的标准字段名并集（含主键 + announce_date）。

        供 ``Connector`` 判断某个请求字段属于哪些标量表。
        """
        names: set[str] = set()
        for schema in cls.scalar_tables():
            names.update(schema.standard_field_names)
        return frozenset(names)

    @classmethod
    def tables_for_fields(cls, fields: set[str]) -> tuple[FinancialTableSchema, ...]:
        """按需求字段集反查涉及的标量表（供连接器按需 join）。

        Args:
            fields: 请求的标准字段名集合（如 {"roe", "revenue"}）

        Returns:
            需要 join 的标量表 schema 元组（按 TABLES 顺序）
        """
        involved: list[FinancialTableSchema] = []
        for schema in cls.scalar_tables():
            if fields & set(schema.standard_field_names):
                involved.append(schema)
        return tuple(involved)

    @classmethod
    def derived_metric_names(cls) -> frozenset[str]:
        """衍生指标名集合（用于判断请求字段是否需衍生计算）。"""
        return frozenset(m.name for m in DERIVED_METRICS)

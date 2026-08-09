"""数据缓存模块

使用 DuckDB 作为本地缓存数据库，支持高效的向量化查询。
路径由 ``core.storage.backtest_cache_path`` 统一裁决（LONG_EARN_DATA_DIR）。

ADR-014 阶段 B：``financial_quarterly`` 单一宽表废弃，改为 8 张细表
（6 标量 + 2 长表 Top10），schema 从 ``FinancialSchemaRegistry`` 反射建表。
启动时检测旧宽表存在则自动迁移（``migrate_financial_quarterly``）。
"""

import contextlib
import threading
import time
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
from loguru import logger

from long_earn.backtest.data.financial.migrations import (
    migrate_financial_quarterly,
    needs_migration,
)
from long_earn.backtest.data.financial.schemas import FinancialSchemaRegistry
from long_earn.core.storage import backtest_cache_path

# 缓存大查询 info 日志阈值（避免小查询刷屏）
_CACHE_SLOW_QUERY_SYMBOLS = 500
_CACHE_SLOW_QUERY_SECONDS = 1.0


class DataCache:
    """DuckDB 数据缓存管理器"""

    def __init__(self, db_path: str | Path = ""):
        """初始化缓存

        Args:
            db_path: 数据库文件路径，空字符串默认取 core.storage.backtest_cache_path()
        """
        self.db_path = Path(db_path) if db_path else backtest_cache_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path_str = str(self.db_path)
        self._local = threading.local()
        self._init_tables()

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        """获取当前线程的数据库连接（线程安全）。

        每个线程持有独立 DuckDB 连接，避免多线程并发访问同一连接
        导致的 access violation 崩溃。
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = duckdb.connect(self._db_path_str)
        return self._local.conn

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """将日期字符串标准化为 YYYY-MM-DD 格式。

        空字符串原样返回（由调用方决定语义，如 ``get_universe`` 把空当作"最新可用日期"）。
        避免 ``pd.to_datetime("")`` 返回 NaT 后调 ``.strftime`` 抛 ``ValueError``。
        """
        date_str = str(date_str).strip()
        if not date_str:
            return ""
        # 已经是 YYYY-MM-DD 格式
        _yyyy_mm_dd_len = 10
        _yyyymmdd_len = 8
        if len(date_str) == _yyyy_mm_dd_len and "-" in date_str:
            return date_str
        # YYYYMMDD 格式
        if len(date_str) == _yyyymmdd_len:
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        # 其他格式，尝试 pandas 解析
        parsed = pd.to_datetime(date_str, errors="coerce")
        if pd.isna(parsed):
            return ""
        return str(parsed.strftime("%Y-%m-%d"))

    def _init_tables(self) -> None:
        """初始化数据表"""
        conn = self._get_conn()

        # 日行情数据
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_daily (
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                is_tradable BOOLEAN DEFAULT TRUE,
                PRIMARY KEY (symbol, date)
            )
        """)
        # P1-09 迁移：为旧表添加 is_tradable 列（IF NOT EXISTS 语法）
        with contextlib.suppress(Exception):
            conn.execute("""
                ALTER TABLE price_daily
                ADD COLUMN IF NOT EXISTS is_tradable BOOLEAN DEFAULT TRUE
            """)

        # 财务数据 schema 元表（版本管理）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _schema_meta (
                table_name VARCHAR PRIMARY KEY,
                version INTEGER NOT NULL
            )
        """)

        # ADR-014 阶段 B：8 张财务细表（替代旧 financial_quarterly 单一宽表）
        # DDL 从 FinancialSchemaRegistry 反射生成，不再手写字段清单。
        for schema in FinancialSchemaRegistry.TABLES:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {schema.table_name} ({schema.column_ddl()})"
            )
            # 记录每张表的 schema 版本（幂等，不破坏已缓存数据）
            conn.execute(
                "INSERT OR REPLACE INTO _schema_meta VALUES (?, ?)",
                [schema.table_name, FinancialSchemaRegistry.SCHEMA_VERSION],
            )

        # ADR-014 阶段 B：检测旧 financial_quarterly 宽表，自动迁移到 4 张新标量表
        # 迁移幂等（新表已有数据则跳过），旧表重命名为 _v1_deprecated 保留不删。
        try:
            if needs_migration(conn):
                report = migrate_financial_quarterly(conn)
                if not report.skipped:
                    logger.info(
                        f"财务数据自动迁移完成: {report.migrated_rows} 行 → "
                        f"{report.tables_written}，旧表保留为 {report.deprecated_table}"
                    )
        except Exception as e:
            logger.warning(f"财务数据迁移检查失败（非致命，继续启动）: {e}")

        # 指数成分股
        conn.execute("""
            CREATE TABLE IF NOT EXISTS universe_constituents (
                index_code VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                PRIMARY KEY (index_code, symbol, date)
            )
        """)

        logger.info(f"缓存数据库初始化完成: {self.db_path}")

    def get_price_range(self, symbol: str) -> tuple[str, str] | None:
        """获取某只股票缓存的日期范围"""
        conn = self._get_conn()
        result = conn.execute(
            """
            SELECT MIN(date) as start_date, MAX(date) as end_date
            FROM price_daily
            WHERE symbol = ?
            """,
            [symbol],
        ).fetchone()
        if result and result[0]:
            return str(result[0]), str(result[1])
        return None

    def get_price_latest_dates(self, symbols: list[str]) -> dict[str, str]:
        """批量获取多只股票缓存的最新日期。

        用于行情增量判定：比逐股查 get_price_range 快得多（一次 GROUP BY 扫描）。
        缓存中不存在的 symbol 不会出现在返回 dict 中。

        Returns:
            {symbol: latest_date_str} ，latest_date_str 格式 YYYY-MM-DD
        """
        if not symbols:
            return {}
        n = len(symbols)
        start = time.perf_counter()
        conn = self._get_conn()
        placeholders = ", ".join(["?"] * len(symbols))
        rows = conn.execute(
            f"""
            SELECT symbol, MAX(date) as latest
            FROM price_daily
            WHERE symbol IN ({placeholders})
            GROUP BY symbol
            """,
            symbols,
        ).fetchall()
        result = {r[0]: str(r[1]) for r in rows if r[1] is not None}
        elapsed = time.perf_counter() - start
        # 大批量或慢查询才打 info，避免日志洪水
        if n >= _CACHE_SLOW_QUERY_SYMBOLS or elapsed > _CACHE_SLOW_QUERY_SECONDS:
            logger.info(
                f"缓存新鲜度 prices latest: {n} 只, 命中 {len(result)}, "
                f"耗时 {elapsed:.1f}s"
            )
        return result

    def get_prices(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame | None:
        """从缓存获取行情数据"""
        conn = self._get_conn()
        select_fields = ", ".join(fields) if fields else "*"
        placeholders = ", ".join(["?"] * len(symbols))

        query = f"""
            SELECT symbol, date, {select_fields}
            FROM price_daily
            WHERE symbol IN ({placeholders})
              AND date >= ? AND date <= ?
            ORDER BY date, symbol
        """
        params = [*symbols, start_date, end_date]
        n = len(symbols)
        start = time.perf_counter()

        try:
            df = conn.execute(query, params).fetchdf()
            elapsed = time.perf_counter() - start
            if df.empty:
                logger.debug(
                    f"缓存未命中 prices: {len(symbols)} 只股票, {start_date}~{end_date}"
                )
                if (
                    n >= _CACHE_SLOW_QUERY_SYMBOLS
                    or elapsed > _CACHE_SLOW_QUERY_SECONDS
                ):
                    logger.info(
                        f"缓存查询 prices: {n} 只, 返回 0 行, 耗时 {elapsed:.1f}s"
                    )
                return None
            df["date"] = pd.to_datetime(df["date"])
            logger.debug(
                f"缓存命中 prices: {len(df)} 行, {df['symbol'].nunique()} 只股票"
            )
            if n >= _CACHE_SLOW_QUERY_SYMBOLS or elapsed > _CACHE_SLOW_QUERY_SECONDS:
                logger.info(
                    f"缓存查询 prices: {n} 只, 返回 {len(df)} 行, 耗时 {elapsed:.1f}s"
                )
            return df
        except Exception as e:
            elapsed = time.perf_counter() - start
            if n >= _CACHE_SLOW_QUERY_SYMBOLS or elapsed > _CACHE_SLOW_QUERY_SECONDS:
                logger.info(f"缓存查询 prices: {n} 只, 返回 0 行, 耗时 {elapsed:.1f}s")
            logger.warning(f"缓存查询失败: {e}")
            return None

    def save_prices(self, df: pd.DataFrame) -> None:
        """保存行情数据到缓存"""
        if df.empty:
            return

        conn = self._get_conn()
        required_cols = {"symbol", "date", "close"}
        if not required_cols.issubset(df.columns):
            logger.warning(f"行情数据缺少必要列: {required_cols - set(df.columns)}")
            return

        df = df.copy()
        if df["date"].dtype == "object":
            df["date"] = pd.to_datetime(df["date"])

        conn.execute("""
            CREATE OR REPLACE TEMP TABLE temp_price AS SELECT * FROM df
        """)
        # 动态构建 INSERT 列清单：兼容新旧 schema（is_tradable 列可选）
        insert_cols = ["symbol", "date", "open", "high", "low", "close", "volume"]
        if "is_tradable" in df.columns:
            insert_cols.append("is_tradable")
        cols_str = ", ".join(insert_cols)
        conn.execute(f"""
            INSERT OR REPLACE INTO price_daily ({cols_str})
            SELECT {cols_str}
            FROM temp_price
        """)
        logger.info(f"缓存行情数据: {len(df)} 条记录, {df['symbol'].nunique()} 只股票")

    def get_financial_range(self, symbol: str) -> tuple[str, str] | None:
        """获取某只股票财务数据的日期范围（查 income_stmt 代表）。"""
        conn = self._get_conn()
        result = conn.execute(
            """
            SELECT MIN(report_date) as start_date, MAX(report_date) as end_date
            FROM income_stmt
            WHERE symbol = ?
            """,
            [symbol],
        ).fetchone()
        if result and result[0]:
            return str(result[0]), str(result[1])
        return None

    def get_financial_latest_announce(self, symbol: str) -> str | None:
        """获取某只股票缓存中最新的 announce_date（公告日，PIT 真实可见日）。

        用于增量下载新鲜度判定：公告日距今超阈值即视为需要补齐。
        查 income_stmt 代表（所有标量表同源同 announce_date）。
        """
        conn = self._get_conn()
        result = conn.execute(
            "SELECT MAX(announce_date) FROM income_stmt WHERE symbol = ?",
            [symbol],
        ).fetchone()
        if result and result[0]:
            return str(result[0])
        return None

    def get_financial_latest_announces(self, symbols: list[str]) -> dict[str, str]:
        """批量获取多只股票缓存的最新公告日。

        用于财务增量判定：比逐股查 get_financial_latest_announce 快得多（一次 GROUP BY）。
        缓存中不存在的 symbol 不会出现在返回 dict 中。
        查 income_stmt 代表（所有标量表同源同 announce_date）。

        Returns:
            {symbol: latest_announce_date_str} ，格式 YYYY-MM-DD
        """
        if not symbols:
            return {}
        n = len(symbols)
        start = time.perf_counter()
        conn = self._get_conn()
        placeholders = ", ".join(["?"] * len(symbols))
        rows = conn.execute(
            f"""
            SELECT symbol, MAX(announce_date) as latest
            FROM income_stmt
            WHERE symbol IN ({placeholders})
            GROUP BY symbol
            """,
            symbols,
        ).fetchall()
        result = {r[0]: str(r[1]) for r in rows if r[1] is not None}
        elapsed = time.perf_counter() - start
        if n >= _CACHE_SLOW_QUERY_SYMBOLS or elapsed > _CACHE_SLOW_QUERY_SECONDS:
            logger.info(
                f"缓存新鲜度 financial latest: {n} 只, 命中 {len(result)}, "
                f"耗时 {elapsed:.1f}s"
            )
        return result

    def get_financial_latest_announce_by_table(
        self,
        table_name: str,
        symbols: list[str],
    ) -> dict[str, str]:
        """按表批量获取最新公告日（每张细表独立增量判定用）。

        ADR-014 阶段 B：不同表可能下载进度不同（如 Top10 较慢），需独立判定。
        """
        if not symbols:
            return {}
        conn = self._get_conn()
        placeholders = ", ".join(["?"] * len(symbols))
        rows = conn.execute(
            f"""
            SELECT symbol, MAX(announce_date) as latest
            FROM {table_name}
            WHERE symbol IN ({placeholders})
            GROUP BY symbol
            """,
            symbols,
        ).fetchall()
        return {r[0]: str(r[1]) for r in rows if r[1] is not None}

    # ── 8 张细表通用 CRUD（ADR-014 阶段 B）────────────────────────────

    def save_financial_table(self, table_name: str, df: pd.DataFrame) -> None:
        """按表写入财务数据（INSERT OR REPLACE，主键幂等）。

        Args:
            table_name: DuckDB 表名（必须在 FinancialSchemaRegistry.TABLES 中）
            df: DataFrame，列需包含 schema 定义的字段（缺失列自动补 NULL）
        """
        if df.empty:
            return
        schema = FinancialSchemaRegistry.get_table(table_name)
        conn = self._get_conn()
        df = df.copy()
        # 日期列转 datetime
        for date_col in ("report_date", "announce_date"):
            if date_col in df.columns and df[date_col].dtype == "object":
                df[date_col] = pd.to_datetime(df[date_col])
        # 补齐 schema 定义但 df 缺失的列为 None
        schema_cols = [c.name for c in schema.columns]
        for col in schema_cols:
            if col not in df.columns:
                df[col] = None
        df = df[schema_cols]
        # 过滤 NOT NULL 列为空的行
        not_null_cols = [c.name for c in schema.columns if not c.nullable]
        df = df.dropna(subset=not_null_cols)
        if df.empty:
            return
        col_list = ", ".join(schema_cols)
        conn.execute(
            f"INSERT OR REPLACE INTO {table_name} ({col_list}) "
            f"SELECT {col_list} FROM df"
        )
        logger.debug(
            f"缓存 {table_name}: {len(df)} 行, {df['symbol'].nunique()} 只股票"
        )

    def get_financial_table(
        self,
        table_name: str,
        symbols: list[str],
        fields: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame | None:
        """按表读取财务数据。

        Args:
            table_name: DuckDB 表名
            symbols: 股票代码列表
            fields: 需要的字段列表；None 表示返回全量字段
            start_date: 报告期起始（YYYY-MM-DD），None 不限
            end_date: 报告期结束（YYYY-MM-DD），None 不限
        """
        if not symbols:
            return None
        schema = FinancialSchemaRegistry.get_table(table_name)
        conn = self._get_conn()
        # 默认全量；指定 fields 时附加主键 + announce_date
        if fields is None:
            select_clause = "*"
        else:
            required = ["symbol", *schema.primary_key[1:], "announce_date", *fields]
            # 去重保序
            seen: set[str] = set()
            unique_required: list[str] = []
            for c in required:
                if c not in seen:
                    seen.add(c)
                    unique_required.append(c)
            select_clause = ", ".join(unique_required)
        placeholders = ", ".join(["?"] * len(symbols))
        where_parts = [f"symbol IN ({placeholders})"]
        params: list[str] = list(symbols)
        if start_date:
            where_parts.append("report_date >= ?")
            params.append(start_date)
        if end_date:
            where_parts.append("report_date <= ?")
            params.append(end_date)
        where_clause = " AND ".join(where_parts)
        query = (
            f"SELECT {select_clause} FROM {table_name} "
            f"WHERE {where_clause} ORDER BY report_date, symbol"
        )
        try:
            df = conn.execute(query, params).fetchdf()
            if df.empty:
                return None
            df["report_date"] = pd.to_datetime(df["report_date"])
            if "announce_date" in df.columns:
                df["announce_date"] = pd.to_datetime(df["announce_date"])
            return df
        except Exception as e:
            logger.warning(f"缓存查询 {table_name} 失败: {e}")
            return None

    def get_visible_financials(
        self,
        table_name: str,
        symbols: list[str],
        as_of: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame | None:
        """PIT 点查询：announce_date <= as_of 的最新一期。

        ADR-014 阶段 B：供连接器 PIT 裁剪用（某时刻某股可见的最新财报）。
        """
        if not symbols:
            return None
        schema = FinancialSchemaRegistry.get_table(table_name)
        conn = self._get_conn()
        if fields is None:
            select_clause = "*"
        else:
            required = ["symbol", *schema.primary_key[1:], "announce_date", *fields]
            seen: set[str] = set()
            unique_required: list[str] = []
            for c in required:
                if c not in seen:
                    seen.add(c)
                    unique_required.append(c)
            select_clause = ", ".join(unique_required)
        placeholders = ", ".join(["?"] * len(symbols))
        # 子查询取每个 symbol 在 as_of 之前最新的 report_date
        query = f"""
            SELECT {select_clause} FROM {table_name}
            WHERE symbol IN ({placeholders}) AND announce_date <= ?
            AND (symbol, report_date) IN (
                SELECT symbol, MAX(report_date) FROM {table_name}
                WHERE symbol IN ({placeholders}) AND announce_date <= ?
                GROUP BY symbol
            )
            ORDER BY symbol
        """
        params = [*symbols, as_of, *symbols, as_of]
        try:
            df = conn.execute(query, params).fetchdf()
            if df.empty:
                return None
            df["report_date"] = pd.to_datetime(df["report_date"])
            if "announce_date" in df.columns:
                df["announce_date"] = pd.to_datetime(df["announce_date"])
            return df
        except Exception as e:
            logger.warning(f"PIT 查询 {table_name} 失败: {e}")
            return None

    def get_financials(
        self,
        symbols: list[str],
        fields: list[str] | None = None,
    ) -> pd.DataFrame | None:
        """[兼容包装] 从缓存获取财务数据 — union 5 张标量表。

        ADR-014 阶段 B：旧 financial_quarterly 已废弃，此方法改为 union
        income_stmt / balance_sheet / cashflow_stmt / pershareindex / capital
        五张标量表，按 (symbol, report_date) 合并成扁平宽表，供旧消费方
        （provider 的 get_financial_panel）过渡使用。
        新代码应直接用 ``get_financial_table``。

        Args:
            symbols: 股票代码列表
            fields: 需要的财务字段列表；None 表示返回全量字段
        """
        if not symbols:
            return None
        # ADR-014 任务7：union 5 张标量表（含 Capital），让 Connector 查
        # "资本结构" 能拿到 total_shares / float_shares 字段。
        scalar_tables = FinancialSchemaRegistry.scalar_tables()[:5]
        query, params = self._build_union_financials_query(
            scalar_tables, symbols, fields
        )
        n = len(symbols)
        start = time.perf_counter()
        try:
            df = self._get_conn().execute(query, params).fetchdf()
            elapsed = time.perf_counter() - start
            if df.empty:
                logger.debug(f"缓存未命中 financials: {len(symbols)} 只股票")
                if (
                    n >= _CACHE_SLOW_QUERY_SYMBOLS
                    or elapsed > _CACHE_SLOW_QUERY_SECONDS
                ):
                    logger.info(
                        f"缓存查询 financials: {n} 只, 返回 0 行, 耗时 {elapsed:.1f}s"
                    )
                return None
            df["report_date"] = pd.to_datetime(df["report_date"])
            df["announce_date"] = pd.to_datetime(df["announce_date"])
            logger.debug(
                f"缓存命中 financials(union): {len(df)} 行, {df['symbol'].nunique()} 只股票"
            )
            if n >= _CACHE_SLOW_QUERY_SYMBOLS or elapsed > _CACHE_SLOW_QUERY_SECONDS:
                logger.info(
                    f"缓存查询 financials: {n} 只, 返回 {len(df)} 行, "
                    f"耗时 {elapsed:.1f}s"
                )
            return df
        except Exception as e:
            elapsed = time.perf_counter() - start
            if n >= _CACHE_SLOW_QUERY_SYMBOLS or elapsed > _CACHE_SLOW_QUERY_SECONDS:
                logger.info(
                    f"缓存查询 financials: {n} 只, 返回 0 行, 耗时 {elapsed:.1f}s"
                )
            logger.warning(f"缓存查询失败: {e}")
            return None

    @staticmethod
    def _build_union_financials_query(
        scalar_tables: tuple,
        symbols: list[str],
        fields: list[str] | None,
    ) -> tuple[str, list[str]]:
        """构造 4 张标量表的 UNION ALL 合并查询 + 参数。

        每张表 SELECT 其字段，缺失字段补 ``NULL AS fname``（保证 UNION ALL 列名一致），
        外层按 (symbol, report_date) GROUP BY 聚合，MAX(col) 合并多表同字段。
        """
        placeholders = ", ".join(["?"] * len(symbols))
        # 4 张标量表的字段并集（含主键 + announce_date）
        all_fields: list[str] = []
        for schema in scalar_tables:
            for col in schema.data_columns:
                if col.name not in all_fields:
                    all_fields.append(col.name)
        # 若调用方指定 fields，只取那些 + 主键 + announce_date
        if fields is not None:
            select_fields = ["symbol", "report_date", "announce_date", *fields]
        else:
            select_fields = ["symbol", "report_date", "announce_date", *all_fields]

        # 每张表的 SELECT 子句（缺失列补 NULL AS fname）
        sub_selects: list[str] = []
        for schema in scalar_tables:
            schema_field_set = {c.name for c in schema.columns}
            cols: list[str] = []
            for fname in select_fields:
                if fname in schema_field_set:
                    cols.append(f"{fname} AS {fname}")
                else:
                    cols.append(f"NULL AS {fname}")
            sub_selects.append(
                f"SELECT {', '.join(cols)} FROM {schema.table_name} "
                f"WHERE symbol IN ({placeholders})"
            )
        union_query = " UNION ALL ".join(sub_selects)

        # 外层聚合：MAX(col) 合并（同字段只在一表有值）
        agg_cols: list[str] = []
        for fname in select_fields:
            if fname in ("symbol", "report_date", "announce_date"):
                continue
            agg_cols.append(f"MAX({fname}) AS {fname}")
        final_select = ", ".join(
            [
                "symbol",
                "report_date",
                "MAX(announce_date) AS announce_date",
                *agg_cols,
            ]
        )
        query = f"""
            SELECT {final_select} FROM ({union_query})
            GROUP BY symbol, report_date
            ORDER BY report_date, symbol
        """
        params = list(symbols) * len(scalar_tables)
        return query, params

    def save_financials(self, df: pd.DataFrame) -> None:
        """[兼容包装] 保存财务数据 — 拆分写入 5 张标量表。

        ADR-014 阶段 B：旧 financial_quarterly 已废弃。此方法把传入的扁平宽表
        DataFrame 按 schema 字段归属拆到 income_stmt / balance_sheet /
        cashflow_stmt / pershareindex / capital 五表。新代码应直接用
        ``save_financial_table``。
        """
        if df.empty:
            return
        df = df.copy()
        if df["report_date"].dtype == "object":
            df["report_date"] = pd.to_datetime(df["report_date"])
        if "announce_date" in df.columns and df["announce_date"].dtype == "object":
            df["announce_date"] = pd.to_datetime(df["announce_date"])
        # 过滤 NOT NULL 列为空的行
        df = df.dropna(subset=["symbol", "report_date", "announce_date"])
        if df.empty:
            return

        # 5 张标量表的字段归属（与 schema 注册表一致；含 ADR-014 任务7 Capital 表）
        table_field_map = {
            "income_stmt": ["revenue", "net_profit", "eps", "research_expenses"],
            "balance_sheet": ["total_equity", "total_assets", "total_liabilities"],
            "cashflow_stmt": ["ocf", "capex"],
            "pershareindex": [
                "bps",
                "ocf_per_share",
                "debt_to_assets",
                "net_profit_margin",
                "roe_weighted",
                "net_profit_yoy",
                "revenue_yoy",
                "roe",
                "gross_margin",
            ],
            "capital": ["total_shares", "float_shares"],
        }
        total = 0
        for table_name, fields in table_field_map.items():
            available = [
                c
                for c in ["symbol", "report_date", "announce_date", *fields]
                if c in df.columns
            ]
            sub_df = df[available].copy()
            if sub_df.empty:
                continue
            # 删除全 NULL 的行（该表字段在原始 df 中全缺失）
            field_cols = [c for c in fields if c in sub_df.columns]
            if field_cols:
                sub_df = sub_df.dropna(subset=field_cols, how="all")
            if sub_df.empty:
                continue
            self.save_financial_table(table_name, sub_df)
            total += len(sub_df)
        logger.info(f"缓存财务数据(拆分写入 4 表): {len(df)} 行 → {total} 子表行")

    def get_universe(self, index_code: str, date: str) -> list[str]:
        """获取某指数在某日期的成分股列表。

        ``date=""`` 表示取缓存中最新的成分股（无 PIT 约束）。
        """
        conn = self._get_conn()
        # 转换日期格式 YYYYMMDD -> YYYY-MM-DD
        date_fmt = self._normalize_date(date)
        try:
            # 先检查表中是否有该 index_code 的数据
            count = conn.execute(
                "SELECT COUNT(*) FROM universe_constituents WHERE index_code = ?",
                [index_code],
            ).fetchone()
            if not count or count[0] == 0:
                logger.debug(f"缓存未命中 universe: {index_code}（表中无此指数）")
                return []

            if date_fmt:
                result = conn.execute(
                    """
                    SELECT symbol
                    FROM universe_constituents
                    WHERE index_code = ? AND date = (
                        SELECT MAX(date) FROM universe_constituents
                        WHERE index_code = ? AND date <= ?
                    )
                    """,
                    [index_code, index_code, date_fmt],
                ).fetchdf()
            else:
                # 空日期：取该指数最新可用日期的成分股
                result = conn.execute(
                    """
                    SELECT symbol
                    FROM universe_constituents
                    WHERE index_code = ? AND date = (
                        SELECT MAX(date) FROM universe_constituents
                        WHERE index_code = ?
                    )
                    """,
                    [index_code, index_code],
                ).fetchdf()

            if result.empty:
                logger.debug(f"缓存未命中 universe: {index_code}（无匹配日期）")
                return []
            symbols = result["symbol"].tolist()
            logger.debug(f"缓存命中 universe {index_code}: {len(symbols)} 只")
            return symbols
        except Exception as e:
            logger.warning(f"缓存查询成分股失败: {e}")
            return []

    def get_universe_snapshot_date(
        self, index_code: str, target_date: str
    ) -> str | None:
        """获取 PIT 查询实际使用的快照日期。

        返回 <= target_date 的最新快照日期，或 None（无历史快照时）。
        用于判断回测是否使用了 PIT 对齐的股票池（幸存者偏差检测）。
        """
        date_fmt = self._normalize_date(target_date)
        if not date_fmt:
            return None
        try:
            conn = self._get_conn()
            result = conn.execute(
                """
                SELECT MAX(date) FROM universe_constituents
                WHERE index_code = ? AND date <= ?
                """,
                [index_code, date_fmt],
            ).fetchdf()
            if result.empty or result.iloc[0, 0] is None:
                return None
            return str(result.iloc[0, 0])[:10]
        except Exception:
            return None

    def save_universe(self, index_code: str, date: str, symbols: list[str]) -> None:
        """保存指数成分股到缓存。

        ``date=""`` 时使用今天日期作为缓存日期（无 PIT 约束场景）。
        """
        if not symbols:
            return

        conn = self._get_conn()
        # 转换日期格式 YYYYMMDD -> YYYY-MM-DD；空则用今天
        date_fmt = self._normalize_date(date)
        if not date_fmt:
            date_fmt = datetime.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(  # noqa: F841
            {
                "index_code": [index_code] * len(symbols),
                "symbol": symbols,
                "date": [pd.to_datetime(date_fmt)] * len(symbols),
            }
        )

        conn.execute("""
            CREATE OR REPLACE TEMP TABLE temp_univ AS SELECT * FROM df
        """)
        conn.execute("""
            INSERT OR REPLACE INTO universe_constituents
            SELECT index_code, symbol, date FROM temp_univ
        """)
        logger.info(f"缓存成分股: {index_code} @ {date}, {len(symbols)} 只")

    def close(self) -> None:
        """关闭当前线程的数据库连接"""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            with contextlib.suppress(Exception):
                self._local.conn.close()
            self._local.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

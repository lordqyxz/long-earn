"""数据缓存模块

使用 PostgreSQL 作为数据缓存（ADR-014 阶段 B 8 张财务细表保留）。

全量迁移 PostgreSQL 后（原 DuckDB 缓存库废弃），价格行情 / 财务数据 /
股票池 / 标的详情统一存储于 PG，连接参数由 ``core.pg`` 统一裁决
（PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD）。

并发纪律（AGENTS.md 缓存保护约定）：
- 写者：数据下载/增量同步（ingestion writer）唯一落库，进程内按路径
  共享 RLock 串行化写事务；跨进程写由 PG MVCC 保证，无需文件锁。
- 读者：查询全部走独立连接，PG 多读单写天然支持。
"""

import contextlib
import threading
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from long_earn.backtest.data.financial.schemas import (
    _PG_TYPE_MAP,
    FinancialSchemaRegistry,
)
from long_earn.core.pg import pg_connect

# 缓存大查询 info 日志阈值（避免小查询刷屏）
_CACHE_SLOW_QUERY_SYMBOLS = 500
_CACHE_SLOW_QUERY_SECONDS = 1.0
_WRITE_LOCKS_GUARD = threading.Lock()
_WRITE_LOCKS: dict[str, threading.RLock] = {}


def _process_write_lock(db_path: Path) -> threading.RLock:
    """返回同一数据库路径在当前进程内共享的单写者锁。

    PG 迁移后 db_path 仅作锁命名空间保留（兼容旧调用方）。
    """

    key = str(db_path.resolve())
    with _WRITE_LOCKS_GUARD:
        return _WRITE_LOCKS.setdefault(key, threading.RLock())


class DataCache:
    """PostgreSQL 数据缓存管理器"""

    def __init__(self, db_path: str | Path = ""):
        """初始化缓存

        Args:
            db_path: [已废弃] DuckDB 时代路径参数，保留仅为兼容旧调用方；
                PG 时代连接参数由 core.pg 统一裁决（PG_HOST 等环境变量）
        """
        # 锁命名空间：沿用 db_path 保证同进程多 DataCache 实例共享写锁
        self._lock_key = Path(db_path) if db_path else Path("pg_default")
        self._local = threading.local()
        self._write_lock = _process_write_lock(self._lock_key)
        with self._write_lock:
            self._init_tables()

    def _get_conn(self) -> Any:
        """获取当前线程的 PostgreSQL 连接（线程安全）。

        psycopg 连接非线程安全，每个线程独立连接；PG 服务端处理并发。

        ``autocommit=True``：只读/高频查询不持有未提交事务，避免 MVCC
        快照长时间占用而阻塞其它连接的 DDL（如 ``ALTER TABLE`` 需要
        ACCESS EXCLUSIVE 锁）——P0 级并发死锁隐患。写入走
        ``_write_transaction`` 的显式 ``BEGIN``/``COMMIT``，仍保持原子性。

        ``row_factory=None``：全模块查询均以 ``row[N]`` 元组下标访问
        （DuckDB 时代 fetchall 契约），保持一致性。
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = pg_connect(autocommit=True, row_factory=None)
        return self._local.conn

    @contextlib.contextmanager
    def _write_transaction(self) -> Iterator[Any]:
        """串行化当前进程写入，并以事务保证单次公共写操作原子化。

        进程内按锁命名空间共享 RLock（跨 DataCache 实例）；跨进程写由
        PG MVCC 保证原子性（ingestion writer 仍是生产落库的唯一入口）。
        """

        with self._write_lock:
            conn = self._get_conn()
            conn.execute("BEGIN")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    @staticmethod
    def _fetchdf(conn: Any, query: str, params: list[Any] | None = None) -> pd.DataFrame:
        """执行查询并转为 pandas DataFrame（psycopg 无 fetchdf）。"""
        if params is None:
            params = []
        cur = conn.execute(query, params)
        if cur.description is None:
            return pd.DataFrame()
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)

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
        """初始化数据表（PostgreSQL DDL，幂等）"""
        conn = self._get_conn()

        # 日行情数据
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_daily (
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                open DOUBLE PRECISION,
                high DOUBLE PRECISION,
                low DOUBLE PRECISION,
                close DOUBLE PRECISION,
                volume DOUBLE PRECISION,
                is_tradable BOOLEAN DEFAULT TRUE,
                PRIMARY KEY (symbol, date)
            )
        """)
        # P1-09：为旧表添加 is_tradable 列（幂等）
        with contextlib.suppress(Exception):
            conn.execute(
                "ALTER TABLE price_daily "
                "ADD COLUMN IF NOT EXISTS is_tradable BOOLEAN DEFAULT TRUE"
            )

        # 财务数据 schema 元表（版本管理）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _schema_meta (
                table_name VARCHAR PRIMARY KEY,
                version INTEGER NOT NULL
            )
        """)

        # ADR-014 阶段 B：8 张财务细表（替代旧 financial_quarterly 单一宽表）
        # DDL 从 FinancialSchemaRegistry 反射生成（PG 方言）。
        for schema in FinancialSchemaRegistry.TABLES:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {schema.table_name} ({schema.column_ddl()})"
            )
            # 记录每张表的 schema 版本（幂等，不破坏已缓存数据）
            conn.execute(
                "INSERT INTO _schema_meta (table_name, version) VALUES (%s, %s) "
                "ON CONFLICT (table_name) DO UPDATE SET version = EXCLUDED.version",
                [schema.table_name, FinancialSchemaRegistry.SCHEMA_VERSION],
            )

        # SCHEMA_VERSION v3：cashflow_stmt 扩展列迁移（幂等，不破坏已有数据）
        for col_def in (
            "investing_cf DOUBLE PRECISION",
            "financing_cf DOUBLE PRECISION",
            "net_cash_change DOUBLE PRECISION",
            "cash_from_sales DOUBLE PRECISION",
        ):
            with contextlib.suppress(Exception):
                conn.execute(
                    f"ALTER TABLE cashflow_stmt ADD COLUMN IF NOT EXISTS {col_def}"
                )

        # 指数成分股
        conn.execute("""
            CREATE TABLE IF NOT EXISTS universe_constituents (
                index_code VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                PRIMARY KEY (index_code, symbol, date)
            )
        """)

        # 标的详情（公司名称、行业、上市日期等）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS instrument_details (
                symbol VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL DEFAULT '',
                industry VARCHAR DEFAULT '',
                region VARCHAR DEFAULT '',
                listing_date VARCHAR DEFAULT '',
                total_shares DOUBLE PRECISION DEFAULT 0,
                float_shares DOUBLE PRECISION DEFAULT 0,
                market_value DOUBLE PRECISION DEFAULT 0,
                flow_market_value DOUBLE PRECISION DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        logger.info("缓存数据库初始化完成 (PostgreSQL)")

    def get_price_range(self, symbol: str) -> tuple[str, str] | None:
        """获取某只股票缓存的日期范围"""
        conn = self._get_conn()
        result = conn.execute(
            """
            SELECT MIN(date) as start_date, MAX(date) as end_date
            FROM price_daily
            WHERE symbol = %s
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
        placeholders = ", ".join(["%s"] * len(symbols))
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

    def get_trading_dates(self, start_date: str, end_date: str) -> list[str]:
        """获取区间内 XSHG 真实交易日列表（AUDIT-P2-15）。

        从 price_daily 表查询实际有行情数据的日期，替代 ``pd.date_range(freq="B")``
        的 US 工作日历，避免与中国节假日（春节、国庆等）不匹配。

        Returns:
            YYYY-MM-DD 格式的日期字符串列表，按时间升序排列。
        """
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT DISTINCT to_char(date, 'YYYY-MM-DD') AS dt
            FROM price_daily
            WHERE date >= %s::date AND date <= %s::date
            ORDER BY dt
            """,
            [start_date, end_date],
        ).fetchall()
        return [r[0] for r in rows]

    def get_prices(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame | None:
        """从缓存获取行情数据"""
        conn = self._get_conn()
        # fields=None 时选全部列；否则仅选指定字段。
        # 注意避免 `symbol, date, *` 产生重复列名（psycopg/pandas 报
        # "duplicate keys"），因此 symbol/date 始终显式列出且去重。
        extra = [f for f in (fields or []) if f not in ("symbol", "date")]
        select_fields = ", ".join(["symbol", "date", *extra])
        placeholders = ", ".join(["%s"] * len(symbols))

        query = f"""
            SELECT {select_fields}
            FROM price_daily
            WHERE symbol IN ({placeholders})
              AND date >= %s::date AND date <= %s::date
            ORDER BY date, symbol
        """
        params = [*symbols, start_date, end_date]
        n = len(symbols)
        start = time.perf_counter()

        try:
            df = self._fetchdf(conn, query, params)
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
        """保存行情数据到缓存（批量 COPY + ON CONFLICT 幂等合并）。"""
        if df.empty:
            return

        required_cols = {"symbol", "date", "close"}
        if not required_cols.issubset(df.columns):
            logger.warning(f"行情数据缺少必要列: {required_cols - set(df.columns)}")
            return

        df = df.copy()
        if df["date"].dtype == "object":
            df["date"] = pd.to_datetime(df["date"])

        # 动态构建列清单：兼容新旧 schema（is_tradable 列可选）
        insert_cols = ["symbol", "date", "open", "high", "low", "close", "volume"]
        if "is_tradable" in df.columns:
            insert_cols.append("is_tradable")
        cols_str = ", ".join(insert_cols)
        update_cols = ", ".join(f"{c} = EXCLUDED.{c}" for c in insert_cols)

        with self._write_transaction() as conn:
            # 临时表 + COPY 批量载入（1800 万行级 price_daily 用 COPY 最快）。
            # 临时表从目标表继承列类型；COPY 用默认 TEXT 格式 —— psycopg3 的
            # write_row() 输出 tab 分隔文本，配 FORMAT CSV（逗号分隔）会列错位。
            conn.execute("DROP TABLE IF EXISTS temp_price")
            conn.execute(
                f"CREATE TEMP TABLE temp_price AS "
                f"SELECT {cols_str} FROM price_daily WITH NO DATA"
            )
            # NaN → None：psycopg COPY 无法序列化 pandas NaN
            copy_df = df[insert_cols].where(pd.notnull(df[insert_cols]), None)
            with conn.cursor() as cur, cur.copy(
                f"COPY temp_price ({cols_str}) FROM STDIN"
            ) as copy:
                for row in copy_df.itertuples(index=False):
                    copy.write_row(list(row))
            conn.execute(
                f"""
                INSERT INTO price_daily ({cols_str})
                SELECT {cols_str} FROM temp_price
                ON CONFLICT (symbol, date) DO UPDATE SET {update_cols}
                """
            )
        logger.info(f"缓存行情数据: {len(df)} 条记录, {df['symbol'].nunique()} 只股票")

    def get_financial_range(self, symbol: str) -> tuple[str, str] | None:
        """获取某只股票财务数据的日期范围（查 income_stmt 代表）。"""
        conn = self._get_conn()
        result = conn.execute(
            """
            SELECT MIN(report_date) as start_date, MAX(report_date) as end_date
            FROM income_stmt
            WHERE symbol = %s
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
            "SELECT MAX(announce_date) FROM income_stmt WHERE symbol = %s",
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
        placeholders = ", ".join(["%s"] * len(symbols))
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
        placeholders = ", ".join(["%s"] * len(symbols))
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
        """按表写入财务数据（ON CONFLICT 主键幂等 upsert）。

        Args:
            table_name: 表名（必须在 FinancialSchemaRegistry.TABLES 中）
            df: DataFrame，列需包含 schema 定义的字段（缺失列自动补 NULL）
        """
        if df.empty:
            return
        schema = FinancialSchemaRegistry.get_table(table_name)
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
        pk = ", ".join(schema.primary_key)
        update_cols = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in schema_cols if c not in schema.primary_key
        )
        with self._write_transaction() as conn:
            # 临时表继承目标表列类型 + 默认 TEXT COPY（配 psycopg3 write_row）
            conn.execute("DROP TABLE IF EXISTS temp_fin")
            conn.execute(
                f"CREATE TEMP TABLE temp_fin AS "
                f"SELECT {col_list} FROM {table_name} WITH NO DATA"
            )
            # NaN → None：psycopg COPY 无法序列化 pandas NaN
            copy_df = df[schema_cols].where(pd.notnull(df[schema_cols]), None)
            with conn.cursor() as cur, cur.copy(
                f"COPY temp_fin ({col_list}) FROM STDIN"
            ) as copy:
                for row in copy_df.itertuples(index=False):
                    copy.write_row(list(row))
            conn.execute(
                f"""
                INSERT INTO {table_name} ({col_list})
                SELECT {col_list} FROM temp_fin
                ON CONFLICT ({pk}) DO UPDATE SET {update_cols}
                """
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
            table_name: 表名
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
        placeholders = ", ".join(["%s"] * len(symbols))
        where_parts = [f"symbol IN ({placeholders})"]
        params: list[str] = list(symbols)
        if start_date:
            where_parts.append("report_date >= %s::date")
            params.append(start_date)
        if end_date:
            where_parts.append("report_date <= %s::date")
            params.append(end_date)
        where_clause = " AND ".join(where_parts)
        query = (
            f"SELECT {select_clause} FROM {table_name} "
            f"WHERE {where_clause} ORDER BY report_date, symbol"
        )
        try:
            df = self._fetchdf(conn, query, params)
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
        placeholders = ", ".join(["%s"] * len(symbols))
        # 子查询取每个 symbol 在 as_of 之前最新的 report_date
        query = f"""
            SELECT {select_clause} FROM {table_name}
            WHERE symbol IN ({placeholders}) AND announce_date <= %s::date
            AND (symbol, report_date) IN (
                SELECT symbol, MAX(report_date) FROM {table_name}
                WHERE symbol IN ({placeholders}) AND announce_date <= %s::date
                GROUP BY symbol
            )
            ORDER BY symbol
        """
        params = [*symbols, as_of, *symbols, as_of]
        try:
            df = self._fetchdf(conn, query, params)
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
            df = self._fetchdf(self._get_conn(), query, params)
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
    def _union_select_fields(
        scalar_tables: tuple, fields: list[str] | None
    ) -> list[str]:
        """构造 UNION 的列清单（去重保序，含主键 + announce_date）。"""
        all_fields: list[str] = []
        for schema in scalar_tables:
            for col in schema.data_columns:
                if col.name not in all_fields:
                    all_fields.append(col.name)
        base = ["symbol", "report_date", "announce_date"]
        extra = fields if fields is not None else all_fields
        # 去重保序：all_fields 已含 announce_date，避免同列重复出现导致
        # UNION 子查询重名列（外层引用 announce_date 时报 ambiguous）
        return list(dict.fromkeys([*base, *extra]))

    @staticmethod
    def _union_table_select(
        schema: Any,
        select_fields: list[str],
        field_pg_type: dict[str, str],
        placeholders: str,
    ) -> str:
        """构造单张表的 SELECT 子句（缺失列补 ``NULL::type AS fname``）。"""
        schema_field_set = {c.name for c in schema.columns}
        cols: list[str] = []
        for fname in select_fields:
            if fname in schema_field_set:
                cols.append(f"{fname} AS {fname}")
            else:
                pg_t = field_pg_type.get(fname, "DOUBLE PRECISION")
                cols.append(f"NULL::{pg_t} AS {fname}")
        return (
            f"SELECT {', '.join(cols)} FROM {schema.table_name} "
            f"WHERE symbol IN ({placeholders})"
        )

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
        placeholders = ", ".join(["%s"] * len(symbols))
        select_fields = DataCache._union_select_fields(scalar_tables, fields)

        # 字段 → PG 类型映射（补 NULL 时需显式类型，否则 PG 推断为 text，
        # 与真实 DOUBLE PRECISION 列 UNION ALL 时报 "types text and
        # double precision cannot be matched"）
        field_pg_type: dict[str, str] = {}
        for schema in scalar_tables:
            for col in schema.columns:
                if col.name not in field_pg_type:
                    field_pg_type[col.name] = _PG_TYPE_MAP.get(col.dtype, col.dtype)

        # 每张表的 SELECT 子句（缺失列补 NULL AS fname，带类型断言）
        sub_selects = [
            DataCache._union_table_select(schema, select_fields, field_pg_type, placeholders)
            for schema in scalar_tables
        ]
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
            "cashflow_stmt": [
                "ocf",
                "capex",
                "investing_cf",
                "financing_cf",
                "net_cash_change",
                "cash_from_sales",
            ],
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
                "SELECT COUNT(*) FROM universe_constituents WHERE index_code = %s",
                [index_code],
            ).fetchone()
            if not count or count[0] == 0:
                logger.debug(f"缓存未命中 universe: {index_code}（表中无此指数）")
                return []

            if date_fmt:
                result = self._fetchdf(
                    conn,
                    """
                    SELECT symbol
                    FROM universe_constituents
                    WHERE index_code = %s AND date = (
                        SELECT MAX(date) FROM universe_constituents
                        WHERE index_code = %s AND date <= %s::date
                    )
                    """,
                    [index_code, index_code, date_fmt],
                )
            else:
                # 空日期：取该指数最新可用日期的成分股
                result = self._fetchdf(
                    conn,
                    """
                    SELECT symbol
                    FROM universe_constituents
                    WHERE index_code = %s AND date = (
                        SELECT MAX(date) FROM universe_constituents
                        WHERE index_code = %s
                    )
                    """,
                    [index_code, index_code],
                )

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
            result = self._fetchdf(
                conn,
                """
                SELECT MAX(date) FROM universe_constituents
                WHERE index_code = %s AND date <= %s::date
                """,
                [index_code, date_fmt],
            )
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

        # 转换日期格式 YYYYMMDD -> YYYY-MM-DD；空则用今天
        date_fmt = self._normalize_date(date)
        if not date_fmt:
            date_fmt = datetime.now().strftime("%Y-%m-%d")

        with self._write_transaction() as conn:
            conn.executemany(
                """
                INSERT INTO universe_constituents (index_code, symbol, date)
                VALUES (%s, %s, %s::date)
                ON CONFLICT (index_code, symbol, date) DO NOTHING
                """,
                [(index_code, sym, date_fmt) for sym in symbols],
            )
        logger.info(f"缓存成分股: {index_code} @ {date}, {len(symbols)} 只")

    # ── 标的详情 ──────────────────────────────────────────────────

    @staticmethod
    def _parse_xtquant_detail(detail: dict[str, Any]) -> dict[str, Any]:
        """从 xtquant get_instrument_detail 响应中提取标准化字段。

        xtquant 返回字段映射：
        - InstrumentName → name
        - OpenDate (YYYYMMDD) → listing_date (YYYY-MM-DD)
        - TotalVolume → total_shares
        - FloatVolume → float_shares
        - PreClose × TotalVolume → market_value
        - PreClose × FloatVolume → flow_market_value
        - industry / region → get_instrument_detail 不提供，
          通过 THY1/DY1 板块 API 批量回填（见 batch_update_instrument_sectors）
        """
        name = str(
            detail.get(
                "InstrumentName", detail.get("stockName", detail.get("name", ""))
            )
        )

        # OpenDate 格式 YYYYMMDD → YYYY-MM-DD
        _yyyymmdd_len = 8
        open_date = str(detail.get("OpenDate", detail.get("listDate", "")))
        listing_date = ""
        if open_date and len(open_date) == _yyyymmdd_len and open_date.isdigit():
            listing_date = f"{open_date[:4]}-{open_date[4:6]}-{open_date[6:8]}"
        elif open_date:
            listing_date = open_date

        total_shares = float(
            detail.get("TotalVolume", detail.get("totalShare", 0)) or 0
        )
        float_shares = float(
            detail.get("FloatVolume", detail.get("floatShare", 0)) or 0
        )

        # 市值 = 昨收价 × 股本
        pre_close = float(detail.get("PreClose", 0) or 0)
        market_value = pre_close * total_shares if (pre_close and total_shares) else 0.0
        flow_market_value = (
            pre_close * float_shares if (pre_close and float_shares) else 0.0
        )

        return {
            "name": name,
            "industry": str(detail.get("industry", "")),
            "region": str(detail.get("region", "")),
            "listing_date": listing_date,
            "total_shares": total_shares,
            "float_shares": float_shares,
            "market_value": market_value,
            "flow_market_value": flow_market_value,
        }

    def save_instrument_detail(self, symbol: str, detail: dict[str, Any]) -> None:
        """保存单个标的详情到缓存。

        Args:
            symbol: 标的代码
            detail: xtquant get_instrument_detail 返回的字典
        """
        if not symbol or not detail:
            return
        parsed = self._parse_xtquant_detail(detail)
        with self._write_transaction() as conn:
            conn.execute(
                """
                INSERT INTO instrument_details
                    (symbol, name, industry, region, listing_date,
                     total_shares, float_shares, market_value, flow_market_value, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (symbol) DO UPDATE SET
                    name = EXCLUDED.name,
                    industry = EXCLUDED.industry,
                    region = EXCLUDED.region,
                    listing_date = EXCLUDED.listing_date,
                    total_shares = EXCLUDED.total_shares,
                    float_shares = EXCLUDED.float_shares,
                    market_value = EXCLUDED.market_value,
                    flow_market_value = EXCLUDED.flow_market_value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    symbol,
                    parsed["name"],
                    parsed["industry"],
                    parsed["region"],
                    parsed["listing_date"],
                    parsed["total_shares"],
                    parsed["float_shares"],
                    parsed["market_value"],
                    parsed["flow_market_value"],
                ],
            )

    def save_instrument_details_batch(
        self, items: list[tuple[str, dict[str, Any]]]
    ) -> int:
        """批量保存标的详情。

        Args:
            items: [(symbol, detail_dict), ...]
        Returns:
            实际写入的条数
        """
        if not items:
            return 0
        rows: list[list[Any]] = []
        for symbol, detail in items:
            if not symbol or not detail:
                continue
            parsed = self._parse_xtquant_detail(detail)
            rows.append(
                [
                    symbol,
                    parsed["name"],
                    parsed["industry"],
                    parsed["region"],
                    parsed["listing_date"],
                    parsed["total_shares"],
                    parsed["float_shares"],
                    parsed["market_value"],
                    parsed["flow_market_value"],
                ]
            )
        if not rows:
            return 0
        with self._write_transaction() as conn:
            conn.executemany(
                """
                INSERT INTO instrument_details
                    (symbol, name, industry, region, listing_date,
                     total_shares, float_shares, market_value, flow_market_value, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (symbol) DO UPDATE SET
                    name = EXCLUDED.name,
                    industry = EXCLUDED.industry,
                    region = EXCLUDED.region,
                    listing_date = EXCLUDED.listing_date,
                    total_shares = EXCLUDED.total_shares,
                    float_shares = EXCLUDED.float_shares,
                    market_value = EXCLUDED.market_value,
                    flow_market_value = EXCLUDED.flow_market_value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        logger.info(f"缓存标的详情: {len(rows)} 条")
        return len(rows)

    def get_instrument_detail_cached(self, symbol: str) -> dict[str, Any] | None:
        """从缓存读取单个标的详情。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM instrument_details WHERE symbol = %s",
            [symbol],
        ).fetchone()
        if not row:
            return None
        return {
            "symbol": row[0],
            "name": row[1],
            "industry": row[2],
            "region": row[3],
            "listing_date": row[4],
            "total_shares": float(row[5] or 0),
            "float_shares": float(row[6] or 0),
            "market_value": float(row[7] or 0),
            "flow_market_value": float(row[8] or 0),
        }

    def get_instrument_names_batch(self, symbols: list[str]) -> dict[str, str]:
        """批量获取标的名称映射。

        Returns:
            {symbol: name} 映射，缓存中不存在的 symbol 不出现在结果中
        """
        if not symbols:
            return {}
        conn = self._get_conn()
        placeholders = ", ".join(["%s"] * len(symbols))
        rows = conn.execute(
            f"SELECT symbol, name FROM instrument_details WHERE symbol IN ({placeholders})",
            symbols,
        ).fetchall()
        return {r[0]: r[1] for r in rows if r[1]}

    def update_instrument_industry(
        self, symbol: str, industry: str, region: str
    ) -> None:
        """更新标的的行业和地区字段。

        仅在 instrument_details 行已存在时更新，不创建新行。
        """
        if not symbol:
            return
        with self._write_transaction() as conn:
            conn.execute(
                """
                UPDATE instrument_details
                SET industry = %s, region = %s, updated_at = CURRENT_TIMESTAMP
                WHERE symbol = %s
                """,
                [industry, region, symbol],
            )

    def batch_update_instrument_sectors(
        self, mapping: dict[str, str], field: str
    ) -> int:
        """批量更新 instrument_details 的 industry 或 region 字段。

        通过 xtquant THY1（同花顺行业）/ DY1（地域）板块构建映射后批量回填，
        仅更新空值行（不覆盖已有数据）。

        Args:
            mapping: ``{symbol: value}``，如 ``{"000002.SZ": "房地产"}``
            field: 目标字段名，``"industry"`` 或 ``"region"``
        Returns:
            实际更新行数
        """
        if not mapping or field not in ("industry", "region"):
            return 0
        updated = 0
        with self._write_transaction() as conn:
            for symbol, value in mapping.items():
                result = conn.execute(
                    f"""
                    UPDATE instrument_details
                    SET {field} = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = %s AND ({field} = '' OR {field} IS NULL)
                    """,
                    [value, symbol],
                )
                updated += getattr(result, "rowcount", 0) or 0
        return updated

    def get_financial_data(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        """获取标的历年财务数据（用于前端可视化）。

        从 income_stmt + pershareindex + cashflow_stmt 三张标量表 JOIN 读取，
        返回按 report_date 降序的字典列表。
        """
        conn = self._get_conn()
        # 检查表是否存在
        exists = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='income_stmt'"
        ).fetchone()[0]
        if not exists:
            return []

        rows = conn.execute(
            """
            SELECT
                i.report_date,
                i.announce_date,
                i.revenue,
                i.net_profit,
                i.research_expenses,
                i.eps,
                p.bps,
                p.roe,
                p.roe_weighted,
                p.gross_margin,
                p.net_profit_margin,
                p.net_profit_yoy,
                p.revenue_yoy,
                p.debt_to_assets,
                c.ocf,
                c.capex,
                c.investing_cf,
                c.financing_cf,
                c.net_cash_change,
                c.cash_from_sales
            FROM income_stmt i
            LEFT JOIN pershareindex p
                ON i.symbol = p.symbol AND i.report_date = p.report_date
            LEFT JOIN cashflow_stmt c
                ON i.symbol = c.symbol AND i.report_date = c.report_date
            WHERE i.symbol = %s
            ORDER BY i.report_date DESC
            LIMIT %s
            """,
            [symbol, limit],
        ).fetchall()
        if not rows:
            return []
        return [
            {
                "report_date": str(r[0]) if r[0] else "",
                "announce_date": str(r[1]) if r[1] else "",
                "revenue": float(r[2]) if r[2] is not None else 0,
                "net_profit": float(r[3]) if r[3] is not None else 0,
                "research_expenses": float(r[4]) if r[4] is not None else 0,
                "eps": float(r[5]) if r[5] is not None else 0,
                "bps": float(r[6]) if r[6] is not None else 0,
                "roe": float(r[7]) if r[7] is not None else 0,
                "roe_weighted": float(r[8]) if r[8] is not None else 0,
                "gross_margin": float(r[9]) if r[9] is not None else 0,
                "net_profit_margin": float(r[10]) if r[10] is not None else 0,
                "net_profit_yoy": float(r[11]) if r[11] is not None else 0,
                "revenue_yoy": float(r[12]) if r[12] is not None else 0,
                "debt_to_assets": float(r[13]) if r[13] is not None else 0,
                "ocf": float(r[14]) if r[14] is not None else 0,
                "capex": float(r[15]) if r[15] is not None else 0,
                "investing_cf": float(r[16]) if r[16] is not None else 0,
                "financing_cf": float(r[17]) if r[17] is not None else 0,
                "net_cash_change": float(r[18]) if r[18] is not None else 0,
                "cash_from_sales": float(r[19]) if r[19] is not None else 0,
            }
            for r in rows
        ]

    def close(self) -> None:
        """关闭当前线程的数据库连接"""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            with contextlib.suppress(Exception):
                self._local.conn.close()
            self._local.conn = None

    def check_adjustment_consistency(
        self,
        symbols: list[str],
        start_date: str = "",
        end_date: str = "",
        *,
        max_return_pct: float = 50.0,
    ) -> list[dict[str, Any]]:
        """复权一致性检查：检测日间价格跳跃是否暗示复权异常（AUDIT-P2-07）。

        逐股计算日收益率，若任意相邻两日 close 涨跌幅超过 ``max_return_pct``
        （默认 50%），则标记为可疑跳跃。除权除息日若未正确复权，会出现单日
        大幅跳空（如 10 送 10 未复权导致 -50%）。

        Args:
            symbols: 待检查股票列表
            start_date: 起始日期（空字符串 = 不限制）
            end_date: 结束日期（空字符串 = 不限制）
            max_return_pct: 日收益率阈值（百分比），超过此值视为可疑

        Returns:
            可疑跳跃列表，每项包含 symbol / date / prev_close / close / return_pct
        """
        if not symbols:
            return []

        conn = self._get_conn()
        params: list[Any] = []
        where_clauses = [f"symbol IN ({', '.join(['%s'] * len(symbols))})"]
        params.extend(symbols)
        if start_date:
            where_clauses.append("date >= %s::date")
            params.append(start_date)
        if end_date:
            where_clauses.append("date <= %s::date")
            params.append(end_date)

        rows = conn.execute(
            f"""
            SELECT symbol, date, close
            FROM price_daily
            WHERE {" AND ".join(where_clauses)}
            ORDER BY symbol, date
            """,
            params,
        ).fetchall()

        if not rows:
            return []

        # 按 symbol 分组，逐股计算日收益率
        suspicious: list[dict[str, Any]] = []
        current_symbol = rows[0][0]
        prev_close: float | None = None
        prev_date: str = ""

        for symbol, date, close in rows:
            if symbol != current_symbol:
                current_symbol = symbol
                prev_close = None
                prev_date = ""

            if close is None or close <= 0:
                prev_close = None
                prev_date = ""
                continue

            if prev_close is not None and prev_close > 0:
                ret = (float(close) - prev_close) / prev_close * 100
                if abs(ret) > max_return_pct:
                    suspicious.append(
                        {
                            "symbol": symbol,
                            "date": str(date),
                            "prev_date": prev_date,
                            "prev_close": round(prev_close, 4),
                            "close": round(float(close), 4),
                            "return_pct": round(ret, 2),
                        }
                    )

            prev_close = float(close)
            prev_date = str(date)

        return suspicious

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

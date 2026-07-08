"""数据缓存模块

使用 DuckDB 作为本地缓存数据库，支持高效的向量化查询。
"""

import contextlib
from pathlib import Path

import duckdb
import pandas as pd
from loguru import logger

DEFAULT_CACHE_PATH = Path.home() / ".long_earn" / "backtest_cache.duckdb"


class DataCache:
    """DuckDB 数据缓存管理器"""

    def __init__(self, db_path: str | Path = ""):
        """初始化缓存

        Args:
            db_path: 数据库文件路径，默认 ~/.long_earn/backtest_cache.duckdb
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_CACHE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._init_tables()

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        """获取数据库连接（懒加载）"""
        if self._conn is None:
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """将日期字符串标准化为 YYYY-MM-DD 格式。"""
        date_str = str(date_str).strip()
        # 已经是 YYYY-MM-DD 格式
        _yyyy_mm_dd_len = 10
        _yyyymmdd_len = 8
        if len(date_str) == _yyyy_mm_dd_len and "-" in date_str:
            return date_str
        # YYYYMMDD 格式
        if len(date_str) == _yyyymmdd_len:
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        # 其他格式，尝试 pandas 解析
        return str(pd.to_datetime(date_str).strftime("%Y-%m-%d"))

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
                PRIMARY KEY (symbol, date)
            )
        """)

        # 季度财务数据
        # announce_date = 真实财报发布日期（PIT 契约核心，ADR-007）
        # ADR-007 Phase 3：全量字段（Income + Balance + CashFlow + Pershareindex 四表合并）
        # 旧表字段不全时直接 DROP + CREATE（不兼容旧数据，缓存全量重建）
        conn.execute("DROP TABLE IF EXISTS financial_quarterly")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS financial_quarterly (
                symbol VARCHAR NOT NULL,
                report_date DATE NOT NULL,
                announce_date DATE NOT NULL,
                -- Income 表字段
                revenue DOUBLE,
                net_profit DOUBLE,
                eps DOUBLE,
                research_expenses DOUBLE,
                -- Balance 表字段
                total_equity DOUBLE,
                total_assets DOUBLE,
                total_liabilities DOUBLE,
                -- CashFlow 表字段
                ocf DOUBLE,
                capex DOUBLE,
                -- Pershareindex 表预计算字段
                bps DOUBLE,
                ocf_per_share DOUBLE,
                debt_to_assets DOUBLE,
                net_profit_margin DOUBLE,
                roe_weighted DOUBLE,
                -- 衍生指标（Pershareindex 预计算优先，手算兜底）
                net_profit_yoy DOUBLE,
                revenue_yoy DOUBLE,
                roe DOUBLE,
                gross_margin DOUBLE,
                PRIMARY KEY (symbol, report_date)
            )
        """)

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

        try:
            df = conn.execute(query, params).fetchdf()
            if df.empty:
                logger.debug(f"缓存未命中 prices: {len(symbols)} 只股票, {start_date}~{end_date}")
                return None
            df["date"] = pd.to_datetime(df["date"])
            logger.debug(
                f"缓存命中 prices: {len(df)} 行, {df['symbol'].nunique()} 只股票"
            )
            return df
        except Exception as e:
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
        conn.execute("""
            INSERT OR REPLACE INTO price_daily
            SELECT symbol, date, open, high, low, close, volume
            FROM temp_price
        """)
        logger.info(f"缓存行情数据: {len(df)} 条记录, {df['symbol'].nunique()} 只股票")

    def get_financial_range(self, symbol: str) -> tuple[str, str] | None:
        """获取某只股票财务数据的日期范围"""
        conn = self._get_conn()
        result = conn.execute(
            """
            SELECT MIN(report_date) as start_date, MAX(report_date) as end_date
            FROM financial_quarterly
            WHERE symbol = ?
            """,
            [symbol],
        ).fetchone()
        if result and result[0]:
            return str(result[0]), str(result[1])
        return None

    def get_financials(
        self,
        symbols: list[str],
        fields: list[str] | None = None,
    ) -> pd.DataFrame | None:
        """从缓存获取财务数据

        Args:
            symbols: 股票代码列表
            fields: 需要的财务字段列表；None 表示返回全量字段
               （symbol/report_date/announce_date + 18 个财务字段）
        """
        conn = self._get_conn()
        # 默认返回全量字段；指定 fields 时附加必要的主键列
        if fields is None:
            select_clause = "*"
        else:
            select_clause = ", ".join(["symbol", "report_date", "announce_date", *fields])
        placeholders = ", ".join(["?"] * len(symbols))

        query = f"""
            SELECT {select_clause}
            FROM financial_quarterly
            WHERE symbol IN ({placeholders})
            ORDER BY report_date, symbol
        """

        try:
            df = conn.execute(query, symbols).fetchdf()
            if df.empty:
                logger.debug(f"缓存未命中 financials: {len(symbols)} 只股票")
                return None
            df["report_date"] = pd.to_datetime(df["report_date"])
            df["announce_date"] = pd.to_datetime(df["announce_date"])
            logger.debug(
                f"缓存命中 financials: {len(df)} 行, {df['symbol'].nunique()} 只股票"
            )
            return df
        except Exception as e:
            logger.warning(f"缓存查询失败: {e}")
            return None

    def save_financials(self, df: pd.DataFrame) -> None:
        """保存财务数据到缓存"""
        if df.empty:
            return

        conn = self._get_conn()
        df = df.copy()
        if df["report_date"].dtype == "object":
            df["report_date"] = pd.to_datetime(df["report_date"])
        if "announce_date" in df.columns and df["announce_date"].dtype == "object":
            df["announce_date"] = pd.to_datetime(df["announce_date"])

        # 只选择缓存表中存在的列，缺失列用 NULL 填充
        # ADR-007 Phase 3：全量字段（Income + Balance + CashFlow + Pershareindex 四表合并）
        cache_columns = [
            "symbol",
            "report_date",
            "announce_date",
            # Income 表字段
            "revenue",
            "net_profit",
            "eps",
            "research_expenses",
            # Balance 表字段
            "total_equity",
            "total_assets",
            "total_liabilities",
            # CashFlow 表字段
            "ocf",
            "capex",
            # Pershareindex 表预计算字段
            "bps",
            "ocf_per_share",
            "debt_to_assets",
            "net_profit_margin",
            "roe_weighted",
            # 衍生指标（Pershareindex 预计算优先，手算兜底）
            "net_profit_yoy",
            "revenue_yoy",
            "roe",
            "gross_margin",
        ]
        for col in cache_columns:
            if col not in df.columns:
                df[col] = None

        # 过滤掉 NOT NULL 列为空的行（symbol/report_date/announce_date）
        df = df.dropna(subset=["symbol", "report_date", "announce_date"])

        if df.empty:
            return

        conn.execute(f"""
            INSERT OR REPLACE INTO financial_quarterly
            ({", ".join(cache_columns)})
            SELECT {", ".join(cache_columns)} FROM df
        """)
        logger.info(f"缓存财务数据: {len(df)} 条记录, {df['symbol'].nunique()} 只股票")

    def get_universe(self, index_code: str, date: str) -> list[str]:
        """获取某指数在某日期的成分股列表"""
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

            if result.empty:
                logger.debug(f"缓存未命中 universe: {index_code}（无匹配日期）")
                return []
            symbols = result["symbol"].tolist()
            logger.debug(f"缓存命中 universe {index_code}: {len(symbols)} 只")
            return symbols
        except Exception as e:
            logger.warning(f"缓存查询成分股失败: {e}")
            return []

    def save_universe(self, index_code: str, date: str, symbols: list[str]) -> None:
        """保存指数成分股到缓存"""
        if not symbols:
            return

        conn = self._get_conn()
        # 转换日期格式 YYYYMMDD -> YYYY-MM-DD
        date_fmt = self._normalize_date(date)
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
        """关闭数据库连接"""
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

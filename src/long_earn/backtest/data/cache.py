"""数据缓存模块

使用 DuckDB 作为本地缓存数据库，支持高效的向量化查询。
"""

import logging
from pathlib import Path

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "backtest_cache.duckdb"


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS financial_quarterly (
                symbol VARCHAR NOT NULL,
                report_date DATE NOT NULL,
                net_profit_yoy DOUBLE,
                revenue_yoy DOUBLE,
                roe DOUBLE,
                gross_margin DOUBLE,
                eps DOUBLE,
                net_profit DOUBLE,
                revenue DOUBLE,
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
                return None
            df["date"] = pd.to_datetime(df["date"])
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
        """从缓存获取财务数据"""
        conn = self._get_conn()
        select_fields = ", ".join(fields) if fields else "*"
        placeholders = ", ".join(["?"] * len(symbols))

        query = f"""
            SELECT symbol, report_date, {select_fields}
            FROM financial_quarterly
            WHERE symbol IN ({placeholders})
            ORDER BY report_date, symbol
        """

        try:
            df = conn.execute(query, symbols).fetchdf()
            if df.empty:
                return None
            df["report_date"] = pd.to_datetime(df["report_date"])
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

        # 只选择缓存表中存在的列，缺失列用 NULL 填充
        cache_columns = [
            "symbol", "report_date",
            "net_profit_yoy", "revenue_yoy", "roe", "gross_margin",
            "eps", "net_profit", "revenue",
        ]
        for col in cache_columns:
            if col not in df.columns:
                df[col] = None

        # 过滤掉 symbol 或 report_date 为空的行（NOT NULL 约束）
        df = df.dropna(subset=["symbol", "report_date"])

        if df.empty:
            return

        conn.execute(f"""
            INSERT OR REPLACE INTO financial_quarterly
            ({', '.join(cache_columns)})
            SELECT {', '.join(cache_columns)} FROM df
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
                return []
            return result["symbol"].tolist()
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
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

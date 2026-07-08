"""akshare 降级数据提供者。

当 miniqmt 不可用且 DuckDB 缓存无数据时，降级到 akshare 获取行情数据。
akshare 通过 HTTP 请求获取公开市场数据，无需本地客户端。

数据获取后会自动写入 DuckDB 缓存，后续查询直接走缓存。

注：财务数据已统一到 miniqmt（ADR-007 Phase 3），本 provider 仅提供
行情/成分股降级能力，不再实现 get_financial_panel。
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd
import polars as pl
from loguru import logger

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.polars_adapter import to_polars_panel
from long_earn.backtest.data.symbol import ak_to_xt, xt_to_ak

# akshare 中文列名 → 标准英文列名
KLINE_COLUMN_MAP = {
    "日期": "date",
    "股票代码": "symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
}


class AkshareFallbackProvider:
    """akshare 降级数据提供者。

    当 miniqmt 不可用且 DuckDB 缓存无数据时使用。
    数据获取后自动写入 DuckDB 缓存。
    """

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self._ak: Any = None

    @property
    def is_available(self) -> bool:
        """检测 akshare 是否可用。"""
        if self._ak is not None:
            return True
        try:
            import akshare as ak  # noqa: PLC0415

            self._ak = ak
            return True
        except Exception as exc:
            logger.warning(f"akshare 不可用: {exc}")
            return False

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """通过 akshare 获取行情数据面板。

        获取后自动写入 DuckDB 缓存。
        """
        if not symbols or not self.is_available:
            return pd.DataFrame()

        fields = fields or ["open", "high", "low", "close", "volume"]
        all_dfs: list[pd.DataFrame] = []

        for symbol in symbols:
            ak_code = xt_to_ak(symbol)
            try:
                df = self._ak.stock_zh_a_hist(
                    symbol=ak_code,
                    period="daily",
                    start_date=start_date.replace("-", ""),
                    end_date=end_date.replace("-", ""),
                    adjust="qfq",
                )
                if df is None or df.empty:
                    continue

                # 列名标准化
                df = df.rename(columns=KLINE_COLUMN_MAP)
                df = df[list(set(KLINE_COLUMN_MAP.values()) & set(df.columns))]
                df["symbol"] = symbol
                df["date"] = pd.to_datetime(df["date"])
                all_dfs.append(df)
            except Exception as e:
                logger.warning(f"akshare 获取 {symbol} 行情失败: {e}")

        if not all_dfs:
            return pd.DataFrame()

        result = pd.concat(all_dfs, ignore_index=True)

        # 写入 DuckDB 缓存
        if not result.empty:
            self.cache.save_prices(result)
            logger.info(
                f"[akshare 降级] 获取 {len(result)} 条行情，"
                f"{result['symbol'].nunique()} 只股票，已写入缓存"
            )

        # 按要求格式返回
        result = result.set_index(["date", "symbol"]).sort_index()
        available_fields = [f for f in fields if f in result.columns]
        return result[available_fields]

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,  # noqa: ARG002
    ) -> pd.DataFrame:
        """获取合并面板（行情 + 财务）。

        财务数据已统一到 miniqmt（ADR-007 Phase 3），本 provider 仅返回行情面板。
        financial_fields 参数保留用于接口兼容，但不再获取财务数据。
        """
        return self.get_price_panel(symbols, start_date, end_date, price_fields)

    def get_merged_panel_as_polars(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """获取合并面板并转为 polars（实现 DataProvider Protocol）。"""
        df = self.get_merged_panel(symbols, start_date, end_date)
        return to_polars_panel(df)

    # universe_type → akshare 指数代码
    _INDEX_AK_CODE: ClassVar[dict[str, str]] = {
        "csi300": "000300",
        "csi500": "000905",
        "sse50": "000016",
        "csi1000": "000852",
    }

    def get_symbols(self, universe_type: str, date: str = "") -> list[str]:
        """获取股票池（指数成分股），通过 akshare 降级获取。

        支持 csi300 / csi500 / sse50 / csi1000 四个指数；
        其他 universe_type 返回空列表（由上层降级链处理）。
        获取后写入 DuckDB 缓存。
        """
        if not self.is_available:
            return []

        # 支持复合 universe："csi300+csi500"
        if "+" in universe_type:
            parts = universe_type.split("+")
            symbols: set[str] = set()
            for part in parts:
                symbols.update(self.get_symbols(part.strip(), date))
            return sorted(symbols)

        ak_index_code = self._INDEX_AK_CODE.get(universe_type)
        if ak_index_code is None:
            return []

        # 1. 优先从缓存读取
        cached = self.cache.get_universe(universe_type, date)
        if cached:
            return cached

        # 2. 从 akshare 获取指数成分股
        try:
            df = self._ak.index_stock_cons_csindex(symbol=ak_index_code)
            if df is not None and not df.empty:
                # akshare 返回的列名含"成分券代码"，取 6 位数字代码
                code_col = "成分券代码" if "成分券代码" in df.columns else df.columns[0]
                codes = df[code_col].astype(str).str.zfill(6).tolist()
                symbols_xt = [ak_to_xt(c) for c in codes if c.isdigit()]
                if symbols_xt:
                    self.cache.save_universe(universe_type, date, symbols_xt)
                    logger.info(
                        f"[akshare 降级] 获取 {universe_type} 成分股: "
                        f"{len(symbols_xt)} 只，已写入缓存"
                    )
                return symbols_xt
        except Exception as e:
            logger.warning(f"[akshare 降级] 获取 {universe_type} 成分股失败: {e}")
        return []


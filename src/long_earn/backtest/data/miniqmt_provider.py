"""基于 miniqmt (xtquant.xtdata) 的本地数据提供者。

数据获取策略：DuckDB 缓存优先，miniqmt 增量补充。

1. 优先从 DuckDB 缓存读取数据
2. 检测缓存数据的新鲜度（最后日期是否接近今天）
3. 若 miniqmt 可用且数据过期，自动从 miniqmt 增量获取最新数据并更新缓存
4. 若 miniqmt 不可用，静默降级到 DuckDB 缓存数据

xtquant 数据格式说明：
  - get_market_data_ex(): 返回 {symbol: DataFrame}，含 'time' 列（毫秒时间戳）
  - get_financial_data(): 返回 {symbol: {table: DataFrame}}，含 'm_timetag' 列（YYYYMMDD 字符串）
  - get_stock_list_in_sector(): 返回 [symbol, ...]
  - get_instrument_detail(): 返回 {field: value} 字典
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from long_earn.backtest.data.cache import DataCache

logger = logging.getLogger(__name__)

# 缓存数据过期阈值（天）：超过此天数视为过期，需从 miniqmt 更新
STALE_THRESHOLD_DAYS = 5

# 指数代码 -> 板块名称映射
INDEX_SECTOR_MAP = {
    "csi300": "沪深300",
    "csi500": "中证500",
    "sse50": "上证50",
    "csi1000": "中证1000",
}

# 英文板块名 → 中文板块名映射（供 universe_type 使用）
BOARD_NAME_MAP = {
    "main_board": "沪市主板",
    "star_board": "创业板",
    "chinext": "创业板",
    "gem": "创业板",
    "bse": "北交所",
    "szse_main": "深市主板",
    "sse_main": "沪市主板",
}

# 财务字段映射：xtquant 字段 -> 标准字段名
FINANCIAL_FIELD_MAP = {
    "net_profit_yoy": "net_profit_yoy",
    "roe": "roe",
    "revenue_yoy": "revenue_yoy",
    "gross_profit_margin": "gross_margin",
    "net_profit": "net_profit",
    "operating_revenue": "revenue",
    "eps": "eps",
}


class MiniQmtClient:
    """封装 xtquant.xtdata 的本地同步客户端。

    按需延迟加载 xtquant，不可用时优雅降级（返回空数据而非抛异常）。
    所有 xtdata 下载调用均带超时保护，防止 QMT 未连接时阻塞。
    """

    _instance: MiniQmtClient | None = None
    _DOWNLOAD_TIMEOUT = 60  # 下载操作超时（秒）

    def __init__(self) -> None:
        self._xtdata: Any = None
        self._available: bool | None = None
        self._sector_downloaded: bool = False  # 板块数据是否已下载

    @staticmethod
    def _run_with_timeout(fn: Any, timeout: int, *args: Any, **kwargs: Any) -> Any:
        """在子线程中执行 fn，超时则抛出 TimeoutError。"""
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fn, *args, **kwargs)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeoutError:
                logger.warning(f"xtdata 调用超时 ({timeout}s): {fn.__name__}")
                raise TimeoutError(f"xtdata 调用超时 ({timeout}s): {fn.__name__}") from None

    @classmethod
    def get(cls) -> MiniQmtClient:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_available(self) -> bool:
        """检测 xtquant 是否可用。

        优先检查 LONG_EARN_DISABLE_XTQUANT 环境变量：CI / 无 QMT dev 环境
        通常 import xtquant 成功但实际查询会让 C++ 端触发 SIGABRT 杀整个进程
        （Python 层超时无法救，因为 abort 是 process-wide signal）。
        设置 LONG_EARN_DISABLE_XTQUANT=1 强制走 "xtquant 不可用 → DuckDB 缓存"
        分支，避免崩溃。
        """
        if self._available is not None:
            return self._available
        # 1. 显式禁用开关：优先于 import 检测
        disable = os.environ.get("LONG_EARN_DISABLE_XTQUANT", "").strip().lower()
        if disable in ("1", "true", "yes", "on"):
            self._available = False
            logger.info(
                "LONG_EARN_DISABLE_XTQUANT 已设置，强制将 xtquant 标记为不可用"
            )
            return self._available
        # 2. 尝试 import；失败则不可用
        try:
            from xtquant import xtdata  # noqa: PLC0415

            self._xtdata = xtdata
            self._available = True
            logger.info("xtquant.xtdata 可用")
        except Exception as exc:
            self._available = False
            logger.info(f"xtquant.xtdata 不可用，将使用 DuckDB 缓存: {exc}")
        return self._available

    def _ensure_xtdata(self) -> Any:
        """获取 xtdata 模块，不可用时返回 None。"""
        if self._xtdata is not None:
            return self._xtdata
        if self.is_available:
            return self._xtdata
        return None

    # ── 数据下载 ─────────────────────────────────────────────────────────

    def _download_kline(
        self,
        stock_list: list[str],
        start_time: str = "",
        end_time: str = "",
        period: str = "1d",
    ) -> bool:
        """下载 K 线数据到本地缓存。返回是否成功。"""
        xtdata = self._ensure_xtdata()
        if xtdata is None:
            return False
        try:
            self._run_with_timeout(
                xtdata.download_history_data2,
                self._DOWNLOAD_TIMEOUT,
                stock_list=stock_list,
                period=period,
                start_time=start_time,
                end_time=end_time,
            )
            logger.info(f"K线数据下载完成: {len(stock_list)} 只股票")
            return True
        except TimeoutError:
            logger.warning("K线数据下载超时，跳过")
            return False
        except Exception as e:
            logger.warning(f"K线数据下载失败: {e}")
            return False

    def _download_financial(
        self,
        stock_list: list[str],
        table_list: list[str] | None = None,
        start_time: str = "",
        end_time: str = "",
    ) -> bool:
        """下载财务数据到本地缓存。返回是否成功。"""
        xtdata = self._ensure_xtdata()
        if xtdata is None:
            return False
        try:
            self._run_with_timeout(
                xtdata.download_financial_data2,
                self._DOWNLOAD_TIMEOUT,
                stock_list=stock_list,
                table_list=table_list or [],
                start_time=start_time,
                end_time=end_time,
            )
            logger.info(f"财务数据下载完成: {len(stock_list)} 只股票")
            return True
        except TimeoutError:
            logger.warning("财务数据下载超时，跳过")
            return False
        except Exception as e:
            logger.warning(f"财务数据下载失败: {e}")
            return False

    # ── K线 ───────────────────────────────────────────────────────────────

    def get_kline(
        self,
        stock_list: list[str],
        start_time: str = "",
        end_time: str = "",
        period: str = "1d",
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取多只股票的 K 线数据，返回标准化 DataFrame。

        返回列：date, symbol, open, high, low, close, volume
        xtquant 不可用 / 超时 / 异常时返回空 DataFrame（不抛、不卡、不让主进程崩）。
        """
        xtdata = self._ensure_xtdata()
        if xtdata is None:
            return pd.DataFrame()

        # 先下载再查询
        self._download_kline(stock_list, start_time, end_time, period)

        result_fields = fields or ["time", "open", "high", "low", "close", "volume"]
        if "time" not in result_fields:
            result_fields = ["time", *fields] if fields else result_fields

        try:
            raw = self._run_with_timeout(
                xtdata.get_market_data_ex,
                self._DOWNLOAD_TIMEOUT,
                field_list=result_fields,
                stock_list=stock_list,
                period=period,
                start_time=start_time,
                end_time=end_time,
                count=-1,
                dividend_type="front",
                fill_data=False,
            )
        except TimeoutError:
            logger.warning("get_market_data_ex 超时，返回空数据")
            return pd.DataFrame()
        except Exception as e:
            logger.warning(f"get_market_data_ex 异常: {e}")
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for symbol, data in (raw or {}).items():
            if data is None or (hasattr(data, "empty") and data.empty):
                continue
            times = data.get("time")
            if times is None or len(times) == 0:
                continue
            dates = pd.to_datetime(times, unit="ms", utc=True)
            for i in range(len(data)):
                dt = dates.iloc[i].tz_convert("Asia/Shanghai")
                rows.append(
                    {
                        "date": dt.strftime("%Y-%m-%d"),
                        "symbol": symbol,
                        "open": float(data.iloc[i].get("open", 0.0) or 0.0),
                        "high": float(data.iloc[i].get("high", 0.0) or 0.0),
                        "low": float(data.iloc[i].get("low", 0.0) or 0.0),
                        "close": float(data.iloc[i].get("close", 0.0) or 0.0),
                        "volume": float(data.iloc[i].get("volume", 0.0) or 0.0),
                    }
                )

        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        logger.debug(f"get_kline 返回 {len(df)} 行，{len(stock_list)} 只股票")
        return df

    # ── 财务数据 ──────────────────────────────────────────────────────────

    def get_financial(
        self,
        stock_list: list[str],
        start_time: str = "",
        end_time: str = "",
        table: str = "Income",
    ) -> pd.DataFrame:
        """获取财务数据。

        返回列：report_date, symbol, net_profit, net_profit_yoy, revenue, roe, ...
        xtquant 不可用 / 超时 / 异常时返回空 DataFrame。
        """
        xtdata = self._ensure_xtdata()
        if xtdata is None:
            return pd.DataFrame()

        self._download_financial(
            stock_list, table_list=[table], start_time=start_time, end_time=end_time
        )

        try:
            raw = self._run_with_timeout(
                xtdata.get_financial_data,
                self._DOWNLOAD_TIMEOUT,
                stock_list=stock_list,
                table_list=[table],
                start_time=start_time,
                end_time=end_time,
                report_type="report_time",
            )
        except TimeoutError:
            logger.warning("get_financial_data 超时，返回空 DataFrame")
            return pd.DataFrame()
        except Exception as e:
            logger.warning(f"get_financial_data 异常: {e}")
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for symbol, tables in (raw or {}).items():
            for _table_name, df_table in tables.items():
                if df_table is None or (hasattr(df_table, "empty") and df_table.empty):
                    continue
                tmp = (
                    df_table.copy()
                    if hasattr(df_table, "copy")
                    else pd.DataFrame(df_table)
                )
                if isinstance(tmp, pd.DataFrame) and not tmp.empty:
                    tmp["symbol"] = symbol
                    if "m_timetag" in tmp.columns:
                        tmp["report_date"] = pd.to_datetime(
                            tmp["m_timetag"], format="%Y%m%d", errors="coerce"
                        )
                    elif "report_time" in tmp.columns:
                        tmp["report_date"] = pd.to_datetime(
                            tmp["report_time"], unit="s", errors="coerce"
                        )
                    else:
                        tmp["report_date"] = pd.NaT
                    rows.extend(tmp.to_dict("records"))

        result = pd.DataFrame(rows)
        if not result.empty and "report_date" in result.columns:
            result["report_date"] = pd.to_datetime(
                result["report_date"], errors="coerce"
            )
        return result

    # ── 板块/股票池 ──────────────────────────────────────────────────────

    def get_sector_stocks(self, sector_name: str) -> list[str]:
        """获取某个板块/指数的成分股列表。xtquant 不可用 / 超时 / 异常时返回空列表。

        注意：不调用 download_sector_data()，因为该函数在 QMT 未完全连接时
        会永久阻塞。板块数据在 QMT 本地缓存中已存在，直接查询即可；
        但即使是查询，C++ 端崩溃风险仍存在 → 加超时保护。
        """
        xtdata = self._ensure_xtdata()
        if xtdata is None:
            return []
        try:
            result = self._run_with_timeout(
                xtdata.get_stock_list_in_sector,
                self._DOWNLOAD_TIMEOUT,
                sector_name,
            )
            return list(result or [])
        except TimeoutError:
            logger.warning(f"获取板块 {sector_name} 成分股超时")
            return []
        except Exception as e:
            logger.warning(f"获取板块 {sector_name} 成分股失败: {e}")
            return []

    # ── 标的信息 ──────────────────────────────────────────────────────────

    def get_instrument_detail(self, stock_code: str) -> dict[str, Any]:
        """获取标的基础信息。xtquant 不可用 / 超时 / 异常时返回空字典。"""
        xtdata = self._ensure_xtdata()
        if xtdata is None:
            return {}
        try:
            result = self._run_with_timeout(
                xtdata.get_instrument_detail, self._DOWNLOAD_TIMEOUT, stock_code
            )
            return dict(result or {})
        except TimeoutError:
            logger.warning(f"get_instrument_detail({stock_code}) 超时")
            return {}
        except Exception as e:
            logger.warning(f"get_instrument_detail({stock_code}) 异常: {e}")
            return {}

    # ── 实时行情 ──────────────────────────────────────────────────────────

    def get_full_tick(self, code_list: list[str]) -> dict[str, Any]:
        """获取最新逐笔行情。xtquant 不可用 / 超时 / 异常时返回空字典。"""
        xtdata = self._ensure_xtdata()
        if xtdata is None:
            return {}
        try:
            result = self._run_with_timeout(
                xtdata.get_full_tick, self._DOWNLOAD_TIMEOUT, code_list
            )
            return dict(result or {})
        except TimeoutError:
            logger.warning("get_full_tick 超时")
            return {}
        except Exception as e:
            logger.warning(f"get_full_tick 异常: {e}")
            return {}


# ─────────────────────────────────────────────────────────────────────────
# 数据新鲜度检测
# ─────────────────────────────────────────────────────────────────────────


def _is_price_stale(cache: DataCache, symbols: list[str], end_date: str) -> bool:
    """检测行情缓存是否过期。

    如果任一股票的缓存最新日期距 end_date 超过阈值，视为过期。
    """
    end_dt = pd.to_datetime(end_date)
    threshold = timedelta(days=STALE_THRESHOLD_DAYS)
    for sym in symbols:
        rng = cache.get_price_range(sym)
        if rng is None:
            return True
        latest = pd.to_datetime(rng[1])
        if (end_dt - latest) > threshold:
            return True
    return False


def _is_financial_stale(cache: DataCache, symbols: list[str]) -> bool:
    """检测财务缓存是否过期。

    如果任一股票的缓存最新报告期距今超过 120 天，视为过期。
    """
    threshold = timedelta(days=120)
    now = datetime.now()
    for sym in symbols:
        rng = cache.get_financial_range(sym)
        if rng is None:
            return True
        latest = pd.to_datetime(rng[1])
        if (now - latest) > threshold:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────
# 缓存层：DuckDB 优先 + miniqmt 增量更新
# ─────────────────────────────────────────────────────────────────────────


class MiniQmtDataProvider:
    """基于 DuckDB 缓存 + miniqmt 增量更新的数据提供者。

    数据获取策略：
    1. 优先从 DuckDB 缓存读取
    2. 检测缓存数据新鲜度
    3. 若 miniqmt 可用且数据过期，增量获取最新数据并更新缓存
    4. 若 miniqmt 不可用，静默使用缓存数据
    """

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self.client = MiniQmtClient.get()

    @property
    def is_available(self) -> bool:
        """数据源是否可用（miniqmt 可用即视为可用）。"""
        return self.client.is_available

    # ── 行情面板 ─────────────────────────────────────────────────────────

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行情数据面板（DuckDB 优先，miniqmt 增量补充）。"""
        if not symbols:
            return pd.DataFrame()

        fields = fields or ["open", "high", "low", "close", "volume"]

        # 1. 从 DuckDB 缓存读取
        cached_df = self.cache.get_prices(symbols, start_date, end_date)
        cached_symbols: set[str] = set()
        if cached_df is not None and not cached_df.empty:
            cached_symbols = set(cached_df["symbol"].unique())

        # 2. 检测缺失和过期
        missing_symbols = [s for s in symbols if s not in cached_symbols]
        need_refresh = missing_symbols or (
            cached_df is not None
            and not cached_df.empty
            and _is_price_stale(self.cache, symbols, end_date)
        )

        # 3. 若需要刷新且 miniqmt 可用，增量获取
        if need_refresh and self.client.is_available:
            if missing_symbols:
                logger.info(
                    f"行情缓存缺失 {len(missing_symbols)} 只，从 miniqmt 补充"
                )
            else:
                logger.info("行情缓存过期，从 miniqmt 增量更新")

            # 对缺失股票获取完整数据，对已有股票获取增量
            symbols_to_fetch = missing_symbols if missing_symbols else symbols
            fetched = self._fetch_kline(symbols_to_fetch, start_date, end_date)
            if fetched is not None and not fetched.empty:
                self.cache.save_prices(fetched)

        # 4. 从缓存返回最终结果（miniqmt 不可用时直接返回缓存数据）
        df = self.cache.get_prices(symbols, start_date, end_date, fields)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.set_index(["date", "symbol"]).sort_index()
        return df[fields]

    def _fetch_kline(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """从 miniqmt 下载 K 线数据。"""
        try:
            start_fmt = start_date.replace("-", "")
            end_fmt = end_date.replace("-", "")
            df = self.client.get_kline(
                stock_list=symbols,
                start_time=start_fmt,
                end_time=end_fmt,
                period="1d",
            )
            if df.empty:
                return None
            logger.info(
                f"miniqmt 获取 {len(df)} 条行情，{df['symbol'].nunique()} 只股票"
            )
            return df
        except Exception as e:
            logger.warning(f"miniqmt 行情下载失败: {e}")
            return None

    # ── 财务面板 ─────────────────────────────────────────────────────────

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取财务数据面板（DuckDB 优先，miniqmt 增量补充，前向填充到日级）。"""
        if not symbols:
            return pd.DataFrame()

        fields = fields or list(FINANCIAL_FIELD_MAP.values())
        quarters = self._get_quarters_between(start_date, end_date)

        # 1. 从 DuckDB 缓存读取
        cached_df = self.cache.get_financials(symbols, fields)
        missing_quarters = quarters
        if cached_df is not None and not cached_df.empty:
            cached_quarters = set(
                cached_df["report_date"].dt.strftime("%Y%m%d").unique()
            )
            missing_quarters = [q for q in quarters if q not in cached_quarters]

        # 2. 检测是否需要刷新
        need_refresh = bool(missing_quarters) or _is_financial_stale(
            self.cache, symbols
        )

        # 3. 若需要刷新且 miniqmt 可用，增量获取
        if need_refresh and self.client.is_available:
            if missing_quarters:
                logger.info(
                    f"财务缓存缺失 {len(missing_quarters)} 个报告期，从 miniqmt 补充"
                )
            else:
                logger.info("财务缓存过期，从 miniqmt 增量更新")

            fetched = self._fetch_financials(symbols, start_date, end_date)
            if fetched is not None and not fetched.empty:
                self.cache.save_financials(fetched)

        # 4. 从缓存返回最终结果
        df = self.cache.get_financials(symbols, fields)
        if df is None or df.empty:
            return pd.DataFrame()

        trading_dates = pd.date_range(start=start_date, end=end_date, freq="B")
        panel = self._quarterly_to_daily(df, symbols, trading_dates, fields)
        return panel

    def _fetch_financials(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """从 miniqmt 下载财务数据（Income + Balance 表合并，自行计算衍生指标）。"""
        try:
            start_fmt = start_date.replace("-", "")
            end_fmt = end_date.replace("-", "")

            # 获取 Income 表（原始利润表数据）
            income_df = self.client.get_financial(
                stock_list=symbols,
                start_time=start_fmt,
                end_time=end_fmt,
                table="Income",
            )

            # 获取 Balance 表（资产负债表，用于计算 ROE）
            balance_df = self.client.get_financial(
                stock_list=symbols,
                start_time=start_fmt,
                end_time=end_fmt,
                table="Balance",
            )

            if income_df.empty and balance_df.empty:
                return None

            # 以 Income 表为基础构建结果
            if not income_df.empty:
                result_df = pd.DataFrame()
                if "symbol" in income_df.columns:
                    result_df["symbol"] = income_df["symbol"]
                else:
                    result_df["symbol"] = symbols[0] if len(symbols) == 1 else None
                result_df["report_date"] = income_df.get("report_date", pd.NaT)

                # Income 表字段映射
                income_field_map = {
                    "revenue_inc": "revenue",
                    "revenue": "revenue",
                    "net_profit_incl_min_int_inc": "net_profit",
                    "s_fa_eps_basic": "eps",
                    "total_operating_cost": "total_operating_cost",
                }
                for xt_col, std_col in income_field_map.items():
                    if xt_col in income_df.columns and std_col not in result_df.columns:
                        result_df[std_col] = income_df[xt_col]
            else:
                result_df = pd.DataFrame()
                if "symbol" in balance_df.columns:
                    result_df["symbol"] = balance_df["symbol"]
                else:
                    result_df["symbol"] = symbols[0] if len(symbols) == 1 else None
                result_df["report_date"] = balance_df.get("report_date", pd.NaT)

            # 合并 Balance 表的股东权益数据（用于计算 ROE）
            if not balance_df.empty and "symbol" in balance_df.columns:
                balance_cols = ["symbol", "report_date"]
                equity_fields = {
                    "total_equity": "total_equity",
                    "tot_shrhldr_eqy_excl_min_int": "total_equity",
                    "total_hldr_eqy_exc_min_int": "total_equity",
                    "total_hldr_eqy_incl_min_int": "total_equity",
                    "s_fa_total_hldr_eqy_exc_min_int": "total_equity",
                }
                for xt_col, std_col in equity_fields.items():
                    if xt_col in balance_df.columns:
                        balance_cols.append(xt_col)
                        break

                if len(balance_cols) > 2:
                    balance_subset = balance_df[balance_cols].copy()
                    rename_map = {
                        xt_col: std_col
                        for xt_col, std_col in equity_fields.items()
                        if xt_col in balance_subset.columns
                    }
                    balance_subset = balance_subset.rename(columns=rename_map)

                    if not result_df.empty and "report_date" in result_df.columns:
                        result_df["_merge_date"] = pd.to_datetime(
                            result_df["report_date"], errors="coerce"
                        )
                        balance_subset["_merge_date"] = pd.to_datetime(
                            balance_subset["report_date"], errors="coerce"
                        )
                        merge_cols = ["symbol", "_merge_date"]
                        new_cols = [
                            c
                            for c in balance_subset.columns
                            if c not in result_df.columns and c not in merge_cols
                        ]
                        if new_cols:
                            merge_df = balance_subset[merge_cols + new_cols]
                            result_df = result_df.merge(
                                merge_df, on=merge_cols, how="left"
                            )
                        result_df = result_df.drop(
                            columns=["_merge_date"], errors="ignore"
                        )

            # 确保 report_date 为 datetime 并过滤无效行
            if "report_date" in result_df.columns:
                result_df["report_date"] = pd.to_datetime(
                    result_df["report_date"], errors="coerce"
                )
                result_df = result_df.dropna(subset=["report_date"])

            # 计算衍生指标
            result_df = self._compute_derived_financials(result_df)

            logger.info(
                f"miniqmt 获取 {len(result_df)} 条财务数据，"
                f"{result_df['symbol'].nunique()} 只股票"
            )
            return result_df
        except Exception as e:
            logger.warning(f"miniqmt 财务数据下载失败: {e}")
            return None

    def _compute_derived_financials(
        self, df: pd.DataFrame
    ) -> pd.DataFrame:
        """从原始财务数据计算衍生指标（YoY、ROE、毛利率）。

        Args:
            df: 包含 symbol, report_date, revenue, net_profit, total_equity,
                total_operating_cost 等列的 DataFrame

        Returns:
            添加了 net_profit_yoy, revenue_yoy, roe, gross_margin 列的 DataFrame
        """
        if df.empty or "symbol" not in df.columns or "report_date" not in df.columns:
            return df

        # 初始化衍生指标列
        for col in ["net_profit_yoy", "revenue_yoy", "roe", "gross_margin"]:
            if col not in df.columns:
                df[col] = float("nan")

        for symbol in df["symbol"].unique():
            mask = df["symbol"] == symbol
            symbol_data = df[mask].copy().sort_values("report_date")

            if symbol_data.empty:
                continue

            # 计算毛利率 = (revenue - total_operating_cost) / revenue
            if "revenue" in symbol_data.columns and "total_operating_cost" in symbol_data.columns:
                rev = symbol_data["revenue"].astype(float)
                cost = symbol_data["total_operating_cost"].astype(float)
                valid = (rev != 0) & rev.notna() & cost.notna()
                symbol_data.loc[valid, "gross_margin"] = (
                    (rev[valid] - cost[valid]) / rev[valid]
                )

            # 计算 YoY 增长率：与去年同期比较
            symbol_data["_quarter"] = symbol_data["report_date"].dt.quarter
            symbol_data["_year"] = symbol_data["report_date"].dt.year

            for field, yoy_field in [
                ("net_profit", "net_profit_yoy"),
                ("revenue", "revenue_yoy"),
            ]:
                if field not in symbol_data.columns:
                    continue
                for idx, row in symbol_data.iterrows():
                    if pd.isna(row.get(field)) or row[field] == 0:
                        continue
                    # 找去年同期数据
                    last_year_mask = (
                        (symbol_data["_year"] == row["_year"] - 1)
                        & (symbol_data["_quarter"] == row["_quarter"])
                    )
                    last_year_data = symbol_data.loc[last_year_mask, field]
                    if not last_year_data.empty and last_year_data.iloc[0] != 0:
                        last_year_val = float(last_year_data.iloc[0])
                        current_val = float(row[field])
                        symbol_data.loc[idx, yoy_field] = (
                            current_val - last_year_val
                        ) / abs(last_year_val)

            # 计算 ROE = net_profit / total_equity
            if (
                "net_profit" in symbol_data.columns
                and "total_equity" in symbol_data.columns
            ):
                np_val = symbol_data["net_profit"].astype(float)
                eq_val = symbol_data["total_equity"].astype(float)
                valid = eq_val.notna() & (eq_val != 0) & np_val.notna()
                # 年化 ROE：Q1*4, Q2*2, H1*2, Q3*4/3, Q3*2, FY*1
                quarter = symbol_data["_quarter"]
                annualize_factor = quarter.map(
                    {1: 4.0, 2: 2.0, 3: 4.0 / 3.0, 4: 1.0}
                )
                symbol_data.loc[valid, "roe"] = (
                    np_val[valid] / eq_val[valid]
                ) * annualize_factor[valid]

            # 清理临时列
            symbol_data = symbol_data.drop(
                columns=["_quarter", "_year"], errors="ignore"
            )

            # 更新回主 DataFrame
            update_cols = ["net_profit_yoy", "revenue_yoy", "roe", "gross_margin"]
            for col in update_cols:
                if col in symbol_data.columns:
                    df.loc[mask, col] = symbol_data[col].values

        return df

    def _quarterly_to_daily(
        self,
        quarterly_df: pd.DataFrame,
        symbols: list[str],
        trading_dates: pd.DatetimeIndex,
        fields: list[str],
        publication_lag_days: int = 60,
    ) -> pd.DataFrame:
        """将季度财务数据前向填充到日级。

        关键：使用"披露日"而非"报告期截止日"作为可见日期，避免未来函数。
        中国 A 股法定披露窗口：年报次年 4-30 前、Q1 4-30 前、半年报 8-31 前、Q3 10-31 前。
        默认 publication_lag_days=60 天为保守覆盖（覆盖大部分披露场景）。
        若用户已知精确披露日，可在更上游用真实 announce_date 替代 report_date。
        """
        publication_lag = pd.Timedelta(days=publication_lag_days)
        panels: list[pd.DataFrame] = []
        for symbol in symbols:
            symbol_data = quarterly_df[quarterly_df["symbol"] == symbol].copy()
            if symbol_data.empty:
                continue
            symbol_data = symbol_data.sort_values("report_date")
            daily = pd.DataFrame(index=trading_dates)
            daily.index.name = "date"
            for _, row in symbol_data.iterrows():
                report_date = row["report_date"]
                if pd.isna(report_date):
                    continue
                # 用"披露日"作为信息可见的起点：避免在截止日次日就把未公布数据
                # 当作已知信息泄漏给策略，违反 ADR-005 的金融级可信承诺。
                visible_from = pd.to_datetime(report_date) + publication_lag
                mask = daily.index >= visible_from
                for field in fields:
                    if field in row and pd.notna(row[field]):
                        daily.loc[mask, field] = float(row[field])
            daily["symbol"] = symbol
            daily = daily.reset_index().set_index(["date", "symbol"])
            panels.append(daily)
        if not panels:
            return pd.DataFrame()
        result = pd.concat(panels)
        return result

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取合并的数据面板（行情 + 财务）。"""
        price_df = self.get_price_panel(
            symbols, start_date, end_date, price_fields
        )
        fin_df = self.get_financial_panel(
            symbols, start_date, end_date, financial_fields
        )
        if price_df.empty and fin_df.empty:
            return pd.DataFrame()
        if price_df.empty:
            return fin_df
        if fin_df.empty:
            return price_df
        # 检查 fin_df 是否有正确的 MultiIndex
        if not isinstance(fin_df.index, pd.MultiIndex) or fin_df.index.nlevels < 2:  # noqa: PLR2004
            # 财务数据 index 不规范，只返回行情数据
            return price_df
        # 统一 index names，确保一致
        if price_df.index.names != fin_df.index.names:
            fin_df.index.names = price_df.index.names
        # 使用 reset_index + merge + set_index 避免 MultiIndex join 问题
        p = price_df.reset_index()
        f = fin_df.reset_index()
        idx_cols = [c for c in p.columns if c in f.columns][:2]
        if len(idx_cols) < 2:  # noqa: PLR2004
            return price_df
        p[idx_cols[0]] = pd.to_datetime(p[idx_cols[0]])
        f[idx_cols[0]] = pd.to_datetime(f[idx_cols[0]])
        merged = pd.merge(p, f, on=idx_cols, how="outer")
        merged = merged.set_index(idx_cols)
        # 关键：ffill 前必须按 (symbol, date) 升序排序，否则 outer merge 后行序混乱，
        # groupby.ffill 会用"原始行序"填充——可能拿未来值填到过去，构成又一个数据层
        # 未来函数泄漏点（与 _quarterly_to_daily 的截止日 bug 互补）。
        merged = merged.sort_index()
        merged = merged.groupby(level=idx_cols[1]).ffill()
        return merged.sort_index()

    @staticmethod
    def _get_quarters_between(start_date: str, end_date: str) -> list[str]:
        """获取日期范围内的所有季度报告期。"""
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        all_quarters: list[str] = []
        for year in range(start.year - 1, end.year + 1):
            for qe in ["0331", "0630", "0930", "1231"]:
                all_quarters.append(f"{year}{qe}")
        quarters = [
            q
            for q in all_quarters
            if start <= pd.to_datetime(q, format="%Y%m%d") <= end
        ]
        before_start = [
            q
            for q in all_quarters
            if pd.to_datetime(q, format="%Y%m%d") < start
        ]
        if before_start:
            quarters.append(max(before_start))
        return sorted(set(quarters))


# ─────────────────────────────────────────────────────────────────────────
# 股票池
# ─────────────────────────────────────────────────────────────────────────


class MiniQmtUniverseProvider:
    """基于 DuckDB 缓存 + miniqmt 的股票池提供者。

    数据获取策略：
    1. 优先从 DuckDB 缓存读取
    2. 若缓存无数据且 miniqmt 可用，从 miniqmt 获取并缓存
    3. 若 miniqmt 不可用，返回空列表
    """

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self.client = MiniQmtClient.get()

    def get_symbols(self, universe_type: str, date: str = "") -> list[str]:
        """获取指定类型的股票池。"""
        if "+" in universe_type:
            parts = universe_type.split("+")
            symbols: set[str] = set()
            for part in parts:
                symbols.update(self._get_single_universe(part.strip(), date))
            return sorted(symbols)
        return self._get_single_universe(universe_type, date)

    def _get_single_universe(self, universe_type: str, date: str) -> list[str]:
        # 1. 优先从缓存读取
        # 指数：沪深300 / 中证500 / 上证50 / 中证1000
        if universe_type in INDEX_SECTOR_MAP:
            return self._get_index_constituents(
                INDEX_SECTOR_MAP[universe_type], date
            )
        # 英文板块名映射
        sector_name = BOARD_NAME_MAP.get(universe_type, universe_type)
        # 中文板块名
        if sector_name in ("all_a", "全A股"):
            return self._get_all_a_stocks(date)
        # 默认：按板块名查询
        cached = self.cache.get_universe(sector_name, date)
        if cached:
            return cached
        # 2. 缓存无数据，尝试 miniqmt
        if self.client.is_available:
            result = self.client.get_sector_stocks(sector_name)
            if result:
                self.cache.save_universe(sector_name, date, result)
                logger.info(f"获取 {sector_name} 板块: {len(result)} 只")
            return result
        logger.warning(
            f"缓存无数据且 miniqmt 不可用，无法获取板块 {sector_name}"
        )
        return []

    def _get_index_constituents(self, index_name: str, date: str) -> list[str]:
        cached = self.cache.get_universe(index_name, date)
        if cached:
            return cached
        if self.client.is_available:
            result = self.client.get_sector_stocks(index_name)
            if result:
                self.cache.save_universe(index_name, date, result)
                logger.info(f"获取 {index_name} 成分股: {len(result)} 只")
            return result
        logger.warning(
            f"缓存无数据且 miniqmt 不可用，无法获取 {index_name} 成分股"
        )
        return []

    def _get_all_a_stocks(self, date: str) -> list[str]:
        cached = self.cache.get_universe("all_a", date)
        if cached:
            return cached
        if not self.client.is_available:
            logger.warning("缓存无数据且 miniqmt 不可用，无法获取全A股列表")
            return []
        # 尝试从多个板块聚合
        result: list[str] = []
        for idx_name in INDEX_SECTOR_MAP.values():
            try:
                symbols = self.client.get_sector_stocks(idx_name)
                result.extend(symbols)
            except Exception:
                continue
        unique = sorted(set(result))
        if unique:
            self.cache.save_universe("all_a", date, unique)
            logger.info(f"全A股聚合: {len(unique)} 只")
        return unique


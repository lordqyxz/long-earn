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

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from functools import reduce
from typing import Any

import pandas as pd
import polars as pl
from loguru import logger

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.financial.schemas import (
    FinancialSchemaRegistry,
    FinancialTableSchema,
)
from long_earn.backtest.data.polars_adapter import to_polars_panel

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

# 财务字段映射：标准字段名清单（get_financial_panel 默认返回这些列）
# 原始字段提取在 _fetch_financials 中按表（Income/Balance/CashFlow/Pershareindex/Capital）进行
FINANCIAL_FIELD_MAP = {
    # 利润表（Income）原始字段
    "revenue": "revenue",
    "net_profit": "net_profit",
    "eps": "eps",
    "research_expenses": "research_expenses",
    # 资产负债表（Balance）原始字段
    "total_equity": "total_equity",
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
    # 现金流量表（CashFlow）原始字段
    "ocf": "ocf",
    "capex": "capex",
    # 每股指标/主要指标表（Pershareindex）预计算字段
    "bps": "bps",
    "ocf_per_share": "ocf_per_share",
    "debt_to_assets": "debt_to_assets",
    "net_profit_margin": "net_profit_margin",
    "roe_weighted": "roe_weighted",
    # 衍生指标（Pershareindex 预计算优先，_compute_derived_financials 手算兜底）
    "net_profit_yoy": "net_profit_yoy",
    "revenue_yoy": "revenue_yoy",
    "roe": "roe",
    "gross_margin": "gross_margin",
    # 资本变动表（Capital）原始字段（ADR-014 任务7）
    "total_shares": "total_shares",
    "float_shares": "float_shares",
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
                raise TimeoutError(
                    f"xtdata 调用超时 ({timeout}s): {fn.__name__}"
                ) from None

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
            logger.info("LONG_EARN_DISABLE_XTQUANT 已设置，强制将 xtquant 标记为不可用")
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
                    # 真实财报发布日期（ADR-007）：miniqmt 返回 m_anntime 字段
                    if "m_anntime" in tmp.columns:
                        tmp["announce_date"] = pd.to_datetime(
                            tmp["m_anntime"], unit="s", errors="coerce"
                        )
                    else:
                        tmp["announce_date"] = pd.NaT
                    rows.extend(tmp.to_dict("records"))

        result = pd.DataFrame(rows)
        if not result.empty and "report_date" in result.columns:
            result["report_date"] = pd.to_datetime(
                result["report_date"], errors="coerce"
            )
        if not result.empty and "announce_date" in result.columns:
            result["announce_date"] = pd.to_datetime(
                result["announce_date"], errors="coerce"
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

    def get_sector_list(self) -> list[str]:
        """获取所有板块分类名（含行业板块、概念板块、指数板块）。

        xtquant ``xtdata.get_sector_list`` 返回 ``list[str]``，含中文板块名
        （如 "沪深A股"/"创业板"/"白酒"/"半导体" 等）。DataConnector 据此
        发现 miniqmt 支持的全部行业/概念板块，供本体论种子数据扩展用。
        """
        xtdata = self._ensure_xtdata()
        if xtdata is None:
            return []
        try:
            result = self._run_with_timeout(
                xtdata.get_sector_list, self._DOWNLOAD_TIMEOUT
            )
            return list(result or [])
        except TimeoutError:
            logger.warning("get_sector_list 超时")
            return []
        except Exception as e:
            logger.warning(f"get_sector_list 异常: {e}")
            return []

    def get_trading_dates(
        self, start_time: str = "", end_time: str = "", market: str = "SSE"
    ) -> list[str]:
        """获取交易日历。

        xtquant ``xtdata.get_trading_dates_by_market(market, start_time, end_time)``
        返回 ``list[int]``（YYYYMMDD 整数）。本方法统一转为 ``YYYY-MM-DD`` 字符串。

        Args:
            start_time: YYYYMMDD 或 YYYY-MM-DD 起始日
            end_time: YYYYMMDD 或 YYYY-MM-DD 结束日
            market: 市场标识（SSE 上交所 / SZSE 深交所），默认 SSE
        """
        xtdata = self._ensure_xtdata()
        if xtdata is None:
            return []
        try:
            s = start_time.replace("-", "") if start_time else ""
            e = end_time.replace("-", "") if end_time else ""
            raw = self._run_with_timeout(
                xtdata.get_trading_dates_by_market,
                self._DOWNLOAD_TIMEOUT,
                market,
                s,
                e,
            )
            if not raw:
                return []
            return [
                f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:8]}" for d in raw
            ]
        except TimeoutError:
            logger.warning("get_trading_dates_by_market 超时")
            return []
        except Exception as e:
            logger.warning(f"get_trading_dates_by_market 异常: {e}")
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


def _is_financial_stale(
    cache: DataCache,
    symbols: list[str],
    end_date: str = "",
) -> bool:
    """检测财务缓存是否过期。

    判定规则：
    - 若 ``end_date`` 距今超过 120 天，说明用户请求的是历史数据，
      不需要最新财报 → 直接返回 False（不做 staleness 检查）。
    - 否则，任一股票的缓存最新报告期距今超过 120 天 → 视为过期。

    之前版本不带 ``end_date`` 参数，对历史回测查询也会判定过期
    （因为缓存最新报告期永远早于"今天"），导致每次都触发 xtquant 增量下载，
    抹平了缓存 120x 加速效果（ADR-014 修正）。
    """
    threshold = timedelta(days=120)
    now = datetime.now()
    if end_date:
        end_dt = pd.to_datetime(end_date)
        if (now - end_dt) > threshold:
            return False
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
                logger.info(f"行情缓存缺失 {len(missing_symbols)} 只，从 miniqmt 补充")
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
        # _get_quarters_between 会把 start_date 之前最近一季（before_start）
        # 也纳入 quarters，用于 ffill。但该季在用户请求范围外，**不参与**
        # missing 判定——否则缓存里没该季就永远"缺失 1 个"，每次都触发
        # xtquant 下载，抹平缓存加速（ADR-014 修正）。
        start_pd = pd.to_datetime(start_date)
        in_range_quarters = [
            q for q in quarters if pd.to_datetime(q, format="%Y%m%d") >= start_pd
        ]

        # 1. 从 DuckDB 缓存读取
        cached_df = self.cache.get_financials(symbols, fields)
        missing_quarters = in_range_quarters
        if cached_df is not None and not cached_df.empty:
            cached_quarters = set(
                cached_df["report_date"].dt.strftime("%Y%m%d").unique()
            )
            missing_quarters = [
                q for q in in_range_quarters if q not in cached_quarters
            ]

        # 2. 检测是否需要刷新（带 end_date 让历史回测查询跳过 staleness）
        need_refresh = bool(missing_quarters) or _is_financial_stale(
            self.cache, symbols, end_date
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
        """[兼容包装] 从 miniqmt 下载财务数据 — 按表分别取数后合并返回扁平宽表。

        ADR-014 阶段 B：内部调 ``_fetch_financials_by_table`` 按 8 张表 schema 分别
        取数并写入各自细表，再 union 4 张旧表返回扁平宽表（保持旧调用方契约）。
        新代码应直接用 ``_fetch_financials_by_table`` 获取 dict 结果。
        """
        table_dfs = self._fetch_financials_by_table(symbols, start_date, end_date)
        if not table_dfs:
            return None
        # union 5 张标量表（含 Capital）为扁平宽表
        # ADR-014 任务7：把 Capital 表纳入 union，让 Connector 查"资本结构"
        # 能返回 total_shares/float_shares 字段
        scalar_old = FinancialSchemaRegistry.scalar_tables()[:5]
        parts: list[pd.DataFrame] = []
        for schema in scalar_old:
            df = table_dfs.get(schema.table_name)
            if df is not None and not df.empty:
                parts.append(df)
        if not parts:
            return None
        # 按 (symbol, report_date) outer merge 4 表
        # 注意：4 张表都含 announce_date 列，每次 merge 都会产生 announce_date_dup。
        # 必须在每次 merge 后立即合并 announce_date_dup → announce_date 并 drop，
        # 否则下一次 merge 会因已有 announce_date_dup 列而报 MergeError。
        def _merge_two(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
            merged = pd.merge(
                a, b, on=["symbol", "report_date"], how="outer", suffixes=("", "_dup")
            )
            if "announce_date_dup" in merged.columns:
                merged["announce_date"] = merged["announce_date"].fillna(
                    merged["announce_date_dup"]
                )
                merged = merged.drop(columns=["announce_date_dup"])
            return merged

        merged = reduce(_merge_two, parts)
        # dropna NOT NULL
        merged = merged.dropna(subset=["symbol", "report_date", "announce_date"])
        # 衍生指标手算兜底（Pershareindex 预计算值缺失时才计算）
        merged = self._compute_derived_financials(merged)
        logger.info(
            f"miniqmt 获取 {len(merged)} 条财务数据（union 4 表），"
            f"{merged['symbol'].nunique()} 只股票"
        )
        return merged

    def _fetch_financials_by_table(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> dict[str, pd.DataFrame]:
        """ADR-014 阶段 B：按 8 张表 schema 分别从 miniqmt 取数。

        每张表用 ``_extract_by_schema`` 从 xtquant 原始字段按 schema.xt_fields
        候选顺序提取标准字段名，写入各自 DuckDB 细表。

        Returns:
            {table_name: DataFrame}，只含成功取数的表
        """
        try:
            start_fmt = start_date.replace("-", "")
            end_fmt = end_date.replace("-", "")
            result: dict[str, pd.DataFrame] = {}

            for schema in FinancialSchemaRegistry.TABLES:
                df = self._fetch_financial_table(schema, symbols, start_fmt, end_fmt)
                if df is not None and not df.empty:
                    # 写入对应细表
                    self.cache.save_financial_table(schema.table_name, df)
                    result[schema.table_name] = df

            if result:
                total_rows = sum(len(df) for df in result.values())
                logger.info(
                    f"miniqmt 按表取数完成: {len(result)}/{len(FinancialSchemaRegistry.TABLES)} 表，"
                    f"共 {total_rows} 行"
                )
            return result
        except Exception as e:
            logger.warning(f"miniqmt 按表财务数据下载失败: {e}")
            return {}

    def _fetch_financial_table(
        self,
        schema: FinancialTableSchema,
        symbols: list[str],
        start_fmt: str,
        end_fmt: str,
    ) -> pd.DataFrame | None:
        """按单张表 schema 从 miniqmt 取数 + 字段提取。

        Args:
            schema: FinancialTableSchema（含 xt_table + columns + xt_fields 候选）
            symbols: 股票代码列表
            start_fmt / end_fmt: YYYYMMDD 格式时间
        """
        try:
            raw = self.client.get_financial(
                stock_list=symbols,
                start_time=start_fmt,
                end_time=end_fmt,
                table=schema.xt_table,
            )
            if raw.empty:
                return None
            return self._extract_by_schema(schema, raw, symbols)
        except Exception as e:
            logger.warning(f"miniqmt 取 {schema.xt_table} 失败: {e}")
            return None

    def _extract_by_schema(
        self,
        schema: FinancialTableSchema,
        raw: pd.DataFrame,
        symbols: list[str],
    ) -> pd.DataFrame:
        """通用字段提取：按 schema.columns 的 xt_fields 候选顺序提取标准字段名。

        替代旧 _extract_income_fields / _extract_balance_fields / _extract_table_fields
        三个硬编码提取器，改为 schema 驱动的通用逻辑。
        """
        result = pd.DataFrame()
        # 主键 + announce_date 从 raw 统一取（client 已提取 m_timetag/m_anntime）
        result["symbol"] = raw.get("symbol")
        if len(symbols) == 1 and (result["symbol"].isna().all() or result.empty):
            result["symbol"] = symbols[0]
        result["report_date"] = raw.get("report_date", pd.NaT)
        if "announce_date" in raw.columns:
            result["announce_date"] = raw["announce_date"]
        else:
            result["announce_date"] = pd.NaT

        # Top10 长表额外取 rank（xtquant Top10holder 有 rank 列）
        if not schema.is_scalar and "rank" in raw.columns:
            result["rank"] = raw["rank"]

        # 按 schema.columns 的 xt_fields 候选顺序提取
        for col in schema.data_columns:
            if col.name in ("announce_date", "rank"):
                continue  # 已处理
            if not col.xt_fields:
                continue
            # 按候选顺序找第一个存在的原始列
            for xt_col in col.xt_fields:
                if xt_col in raw.columns:
                    result[col.name] = raw[xt_col].values
                    break
            else:
                result[col.name] = None

        # 日期列转 datetime + 过滤无效行
        result["report_date"] = pd.to_datetime(result["report_date"], errors="coerce")
        result["announce_date"] = pd.to_datetime(
            result["announce_date"], errors="coerce"
        )
        result = result.dropna(subset=["symbol", "report_date", "announce_date"])
        return result

    @staticmethod
    def _merge_by_symbol_date(
        base_df: pd.DataFrame, extra_df: pd.DataFrame, xt_col: str
    ) -> pd.Series:
        """按 (symbol, report_date) 对齐，从 extra_df 取一列回 base_df 行顺序。

        用于把 Balance/CashFlow/Pershareindex 的字段对齐到 Income 表的行顺序。
        """
        if xt_col not in extra_df.columns:
            return pd.Series([float("nan")] * len(base_df))
        key_cols = ["symbol", "report_date"]
        if not all(c in extra_df.columns for c in key_cols):
            return pd.Series([float("nan")] * len(base_df))
        extra = extra_df[[*key_cols, xt_col]].copy()
        extra["report_date"] = pd.to_datetime(extra["report_date"], errors="coerce")
        base = base_df[["symbol", "report_date"]].copy()
        base["report_date"] = pd.to_datetime(base["report_date"], errors="coerce")
        merged = base.merge(extra, on=key_cols, how="left")
        return merged[xt_col]

    def _extract_income_fields(
        self, result_df: pd.DataFrame, income_df: pd.DataFrame
    ) -> None:
        """从 Income 表提取字段（行对齐，直接取值）。"""
        if income_df.empty:
            return
        income_field_map = {
            "revenue_inc": "revenue",
            "revenue": "revenue",
            "net_profit_incl_min_int_inc": "net_profit",
            "s_fa_eps_basic": "eps",
            "research_expenses": "research_expenses",
            "total_operating_cost": "total_operating_cost",
        }
        for xt_col, std_col in income_field_map.items():
            if xt_col in income_df.columns and std_col not in result_df.columns:
                result_df[std_col] = income_df[xt_col].values

    def _extract_balance_fields(
        self, result_df: pd.DataFrame, balance_df: pd.DataFrame
    ) -> None:
        """从 Balance 表提取字段（含多字段兜底映射，按 symbol+date 对齐）。"""
        if balance_df.empty or "symbol" not in balance_df.columns:
            return
        balance_extract = {
            "total_equity": [
                "total_equity",
                "tot_shrhldr_eqy_excl_min_int",
                "total_hldr_eqy_exc_min_int",
                "total_hldr_eqy_incl_min_int",
                "s_fa_total_hldr_eqy_exc_min_int",
            ],
            "total_assets": ["tot_assets"],
            "total_liabilities": ["tot_liab"],
        }
        for std_col, xt_candidates in balance_extract.items():
            if std_col in result_df.columns:
                continue
            for xt_col in xt_candidates:
                if xt_col in balance_df.columns:
                    result_df[std_col] = self._merge_by_symbol_date(
                        result_df, balance_df, xt_col
                    )
                    break

    def _extract_table_fields(
        self,
        result_df: pd.DataFrame,
        table_df: pd.DataFrame,
        field_map: dict[str, str],
    ) -> None:
        """通用表字段提取（按 symbol+date 对齐，xt_col → std_col 一对一映射）。"""
        if table_df.empty or "symbol" not in table_df.columns:
            return
        for xt_col, std_col in field_map.items():
            if xt_col in table_df.columns and std_col not in result_df.columns:
                result_df[std_col] = self._merge_by_symbol_date(
                    result_df, table_df, xt_col
                )

    def _compute_derived_financials(self, df: pd.DataFrame) -> pd.DataFrame:
        """衍生指标手算兜底（Pershareindex 预计算值缺失时才计算）。

        ADR-007 Phase 3：优先使用 Pershareindex 表的预计算值（roe/gross_margin/
        net_profit_yoy/revenue_yoy），仅在预计算值缺失（NaN）时用手算兜底。

        Args:
            df: 包含 symbol, report_date, revenue, net_profit, total_equity,
                total_operating_cost 等列的 DataFrame；可能已含 Pershareindex
                预计算的 roe/gross_margin/yoy 列

        Returns:
            补全了 net_profit_yoy, revenue_yoy, roe, gross_margin 列的 DataFrame
        """
        if df.empty or "symbol" not in df.columns or "report_date" not in df.columns:
            return df

        # 确保衍生指标列存在（Pershareindex 预计算值可能已填充部分）
        for col in ["net_profit_yoy", "revenue_yoy", "roe", "gross_margin"]:
            if col not in df.columns:
                df[col] = float("nan")

        for symbol in df["symbol"].unique():
            mask = df["symbol"] == symbol
            symbol_data = df[mask].copy().sort_values("report_date")
            if symbol_data.empty:
                continue
            self._compute_symbol_derived(symbol_data)
            # 更新回主 DataFrame
            for col in ["net_profit_yoy", "revenue_yoy", "roe", "gross_margin"]:
                if col in symbol_data.columns:
                    df.loc[mask, col] = symbol_data[col].values

        return df

    def _compute_symbol_derived(self, symbol_data: pd.DataFrame) -> None:
        """单只股票的衍生指标手算兜底（原地修改 symbol_data）。

        仅对 Pershareindex 预计算值为 NaN 的行计算，不覆盖预计算值。
        """
        symbol_data["_quarter"] = symbol_data["report_date"].dt.quarter
        symbol_data["_year"] = symbol_data["report_date"].dt.year

        self._fill_gross_margin(symbol_data)
        self._fill_yoy_growth(symbol_data, "net_profit", "net_profit_yoy")
        self._fill_yoy_growth(symbol_data, "revenue", "revenue_yoy")
        self._fill_roe(symbol_data)

        symbol_data.drop(columns=["_quarter", "_year"], errors="ignore", inplace=True)

    @staticmethod
    def _fill_gross_margin(symbol_data: pd.DataFrame) -> None:
        """毛利率手算兜底：(revenue - total_operating_cost) / revenue。"""
        if "gross_margin" not in symbol_data.columns:
            return
        need = symbol_data["gross_margin"].isna()
        if (
            not need.any()
            or "revenue" not in symbol_data.columns
            or "total_operating_cost" not in symbol_data.columns
        ):
            return
        rev = symbol_data.loc[need, "revenue"].astype(float)
        cost = symbol_data.loc[need, "total_operating_cost"].astype(float)
        valid = (rev != 0) & rev.notna() & cost.notna()
        symbol_data.loc[need & valid, "gross_margin"] = (
            rev[valid] - cost[valid]
        ) / rev[valid]

    @staticmethod
    def _fill_yoy_growth(symbol_data: pd.DataFrame, field: str, yoy_field: str) -> None:
        """YoY 增长率手算兜底：(本期 - 上年同期) / |上年同期|。"""
        if field not in symbol_data.columns or yoy_field not in symbol_data.columns:
            return
        need = symbol_data[yoy_field].isna()
        if not need.any():
            return
        for idx in symbol_data.index[need]:
            row = symbol_data.loc[idx]
            if pd.isna(row.get(field)) or row[field] == 0:
                continue
            last_year_mask = (symbol_data["_year"] == row["_year"] - 1) & (
                symbol_data["_quarter"] == row["_quarter"]
            )
            last_year_data = symbol_data.loc[last_year_mask, field]
            if not last_year_data.empty and last_year_data.iloc[0] != 0:
                last_year_val = float(last_year_data.iloc[0])
                current_val = float(row[field])
                symbol_data.loc[idx, yoy_field] = (current_val - last_year_val) / abs(
                    last_year_val
                )

    @staticmethod
    def _fill_roe(symbol_data: pd.DataFrame) -> None:
        """ROE 手算兜底：(net_profit / total_equity) × 季度年化系数。"""
        if "roe" not in symbol_data.columns:
            return
        need = symbol_data["roe"].isna()
        if (
            not need.any()
            or "net_profit" not in symbol_data.columns
            or "total_equity" not in symbol_data.columns
        ):
            return
        np_val = symbol_data.loc[need, "net_profit"].astype(float)
        eq_val = symbol_data.loc[need, "total_equity"].astype(float)
        valid = eq_val.notna() & (eq_val != 0) & np_val.notna()
        quarter = symbol_data.loc[need, "_quarter"]
        annualize_factor = quarter.map({1: 4.0, 2: 2.0, 3: 4.0 / 3.0, 4: 1.0})
        symbol_data.loc[need & valid, "roe"] = (
            np_val[valid] / eq_val[valid]
        ) * annualize_factor[valid]

    def _quarterly_to_daily(
        self,
        quarterly_df: pd.DataFrame,
        symbols: list[str],
        trading_dates: pd.DatetimeIndex,
        fields: list[str],
    ) -> pd.DataFrame:
        """将季度财务数据前向填充到日级，基于真实公告日对齐。

        ADR-007：用 announce_date（miniqmt 返回的 m_anntime 字段）作为
        信息可见的起点，不再用 report_date + 固定 lag。

        ADR-014 任务7：保证 ``fields`` 中所有字段都作为列出现在返回 DataFrame 中，
        即使该字段在 quarterly_df 中全缺失（如 Capital 表尚未下载）。
        """
        panels: list[pd.DataFrame] = []
        for symbol in symbols:
            symbol_data = quarterly_df[quarterly_df["symbol"] == symbol].copy()
            if symbol_data.empty:
                continue
            symbol_data = symbol_data.sort_values("announce_date")
            daily = pd.DataFrame(index=trading_dates)
            daily.index.name = "date"
            # 预创建所有请求字段的列（NaN），保证输出 schema 稳定
            for field in fields:
                if field not in daily.columns:
                    daily[field] = pd.NA
            for _, row in symbol_data.iterrows():
                announce_date = row.get("announce_date")
                if pd.isna(announce_date):
                    continue
                visible_from = pd.to_datetime(announce_date)
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
        price_df = self.get_price_panel(symbols, start_date, end_date, price_fields)
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
        if not isinstance(fin_df.index, pd.MultiIndex) or fin_df.index.nlevels < 2:
            # 财务数据 index 不规范，只返回行情数据
            return price_df
        # 统一 index names，确保一致
        if price_df.index.names != fin_df.index.names:
            fin_df.index.names = price_df.index.names
        # 使用 reset_index + merge + set_index 避免 MultiIndex join 问题
        p = price_df.reset_index()
        f = fin_df.reset_index()
        idx_cols = [c for c in p.columns if c in f.columns][:2]
        if len(idx_cols) < 2:
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

    def get_merged_panel_as_polars(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """获取合并面板并转为 polars（实现 DataProvider Protocol）。"""
        df = self.get_merged_panel(symbols, start_date, end_date)
        return to_polars_panel(df)

    def get_symbols(self, universe_type: str, date: str = "") -> list[str]:
        """获取股票池（实现 universe 降级链接口）。

        委托给 :class:`MiniQmtUniverseProvider`，共享同一 DuckDB 缓存。
        """
        return MiniQmtUniverseProvider(self.cache).get_symbols(universe_type, date)

    # ── DataConnector 扩展能力（行业/板块/交易日历/标的信息/实时快照）───

    def get_industry_index_panel(
        self,
        industry: str,
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行业指数 K 线面板。

        miniqmt 通过 ``get_market_data_ex`` 查询行业指数代码（如 "BK0428.SZ"
        半导体 / "BK0475.SH" 白酒）。行业代码通过 :meth:`get_sector_list`
        或 :meth:`get_industry_constituents` 派生，本方法直接用 industry
        参数当 symbol 调用 K 线接口。

        Args:
            industry: 行业指数代码（如 "BK0428.SZ"）或板块名（如 "白酒"）
            start_date: YYYY-MM-DD 起始日
            end_date: YYYY-MM-DD 结束日
            fields: 字段列表，默认 open/high/low/close/volume

        Returns:
            DataFrame，index 为 (date, symbol)，列为 fields；空数据返回空 DataFrame
        """
        if not industry:
            return pd.DataFrame()
        # 板块名 → 行业指数代码的映射由调用方负责（本体论种子数据已含）。
        # 本方法直接拿 industry 当 xtquant symbol 查 K 线（与 get_price_panel
        # 共用 _fetch_kline 逻辑，但走缓存表 industry_index_daily）。
        symbols = [industry]
        # 复用 price_panel 路径（miniqmt 行业指数 K 线格式与股票一致）
        df = self.get_price_panel(symbols, start_date, end_date, fields)
        if df is not None and not df.empty:
            return df
        # 缓存未命中且 miniqmt 不可用时直接返回空
        return pd.DataFrame()

    def get_industry_constituents(self, industry: str) -> list[str]:
        """获取行业成分股列表。

        委托 :meth:`MiniQmtClient.get_sector_stocks` 查询 miniqmt 板块成分股。
        industry 可为中文板块名（"白酒"）/ 申万行业代码（"sw1_bank"）/
        xtquant 行业指数代码（"BK0428.SZ"）。

        Args:
            industry: 行业标识（板块名 / 行业代码）

        Returns:
            成分股 xt_symbol 列表，空数据返回空列表
        """
        if not industry:
            return []
        return self.client.get_sector_stocks(industry)

    def get_sector_classifications(self) -> list[str]:
        """获取 miniqmt 支持的全部板块分类名。

        委托 :meth:`MiniQmtClient.get_sector_list`，返回 xtquant 所有板块名
        （含行业板块、概念板块、指数板块）。供本体论种子数据扩展与 LLM
        推理用（"系统知道哪些行业"）。
        """
        return self.client.get_sector_list()

    def get_trading_dates(
        self, start_date: str = "", end_date: str = "", market: str = "SSE"
    ) -> list[str]:
        """获取交易日历。

        委托 :meth:`MiniQmtClient.get_trading_dates`，返回 YYYY-MM-DD 字符串列表。
        """
        return self.client.get_trading_dates(start_date, end_date, market)

    def get_instrument_detail(self, stock_code: str) -> dict[str, Any]:
        """获取标的基础信息。

        委托 :meth:`MiniQmtClient.get_instrument_detail`，返回原始字段字典
        （含名称/上市日期/总股本/流通股本/行业/板块等）。
        """
        return self.client.get_instrument_detail(stock_code)

    def get_full_tick(self, code_list: list[str]) -> dict[str, Any]:
        """获取最新逐笔行情（实时快照）。

        委托 :meth:`MiniQmtClient.get_full_tick`，返回 ``{symbol: tick_dict}``。
        """
        return self.client.get_full_tick(code_list)

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
            q for q in all_quarters if pd.to_datetime(q, format="%Y%m%d") < start
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
            return self._get_index_constituents(INDEX_SECTOR_MAP[universe_type], date)
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
        logger.warning(f"缓存无数据且 miniqmt 不可用，无法获取板块 {sector_name}")
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
        logger.warning(f"缓存无数据且 miniqmt 不可用，无法获取 {index_name} 成分股")
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

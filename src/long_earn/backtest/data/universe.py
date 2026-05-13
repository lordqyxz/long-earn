"""股票池管理模块

支持多种股票池选择：全A、沪深300、中证500、主板、创业板、科创板等。
"""

import logging
from typing import Protocol

import akshare as ak

from long_earn.backtest.data.cache import DataCache

logger = logging.getLogger(__name__)

# 指数代码映射
INDEX_CODE_MAP = {
    "csi300": "000300",
    "csi500": "000905",
    "csi1000": "000852",
    "sse50": "000016",
}

# 板块代码前缀
BOARD_PREFIXES = {
    "main_board": ["60", "68", "00"],
    "gem": ["30"],
    "star_board": ["68"],
    "bse": ["43", "83", "87"],
}


class UniverseProvider(Protocol):
    """股票池提供者接口"""

    def get_symbols(
        self, universe_type: str, date: str, cache: DataCache
    ) -> list[str]: ...


class AkshareUniverseProvider:
    """基于 akshare 的股票池提供者"""

    def __init__(self, cache: DataCache | None = None):
        self.cache = cache or DataCache()

    def get_symbols(self, universe_type: str, date: str) -> list[str]:
        """获取指定日期和类型的股票池"""
        if "+" in universe_type:
            parts = universe_type.split("+")
            symbols = set()
            for part in parts:
                symbols.update(self._get_single_universe(part.strip(), date))
            return sorted(symbols)
        return self._get_single_universe(universe_type, date)

    def _get_single_universe(self, universe_type: str, date: str) -> list[str]:
        """获取单一股票池"""
        if universe_type in INDEX_CODE_MAP:
            return self._get_index_constituents(INDEX_CODE_MAP[universe_type], date)
        if universe_type in BOARD_PREFIXES:
            return self._get_board_symbols(BOARD_PREFIXES[universe_type], date)
        if universe_type == "all_a":
            return self._get_all_a_symbols(date)
        logger.warning(f"未知的股票池类型: {universe_type}")
        return []

    def _get_index_constituents(self, index_code: str, date: str) -> list[str]:
        """获取指数成分股（带缓存）"""
        cached = self.cache.get_universe(index_code, date)
        if cached:
            logger.debug(f"从缓存获取 {index_code} 成分股: {len(cached)} 只")
            return cached
        try:
            df = ak.index_stock_cons_csindex(symbol=index_code)
            symbols = df["成分券代码"].astype(str).str.strip().tolist()
            self.cache.save_universe(index_code, date, symbols)
            logger.info(f"获取 {index_code} 成分股: {len(symbols)} 只")
            return symbols
        except Exception as e:
            logger.error(f"获取指数 {index_code} 成分股失败: {e}")
            return []

    def _get_board_symbols(self, prefixes: list[str], date: str) -> list[str]:
        """获取板块股票（基于代码前缀过滤）"""
        all_symbols = self._get_all_a_symbols(date)
        filtered = [
            s for s in all_symbols if any(str(s).startswith(p) for p in prefixes)
        ]
        logger.info(f"获取板块 {prefixes} 股票: {len(filtered)} 只")
        return filtered

    def _get_all_a_symbols(self, _date: str) -> list[str]:
        """获取全A股列表"""
        try:
            df = ak.stock_zh_a_spot_em()
            symbols = df["代码"].astype(str).str.strip().tolist()
            logger.info(f"获取全A股: {len(symbols)} 只")
            return symbols
        except Exception as e:
            logger.error(f"获取全A股列表失败: {e}")
            return []


def get_universe_provider(cache: DataCache | None = None) -> UniverseProvider:
    """获取默认的股票池提供者"""
    return AkshareUniverseProvider(cache)

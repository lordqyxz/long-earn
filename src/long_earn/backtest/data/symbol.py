"""统一证券代码符号转换工具。

long-earn 内部统一使用 xtquant 格式 ``600519.SH`` / ``000001.SZ``；
各外部数据源用不同格式，转换逻辑集中在此模块，避免各 provider 重复实现。

支持的目标格式：
  - **ciccwm**: ``(code, market)`` 元组，market 为数值（0=深 / 1=沪 / 2=北 / 31=港 / 74=美股）
  - **akshare**: 纯数字代码 ``600519``（不含市场后缀）
  - **xtquant 规范化**: 确保纯数字代码补齐 ``.SH`` / ``.SZ`` 后缀
"""

from __future__ import annotations

import re

from long_earn.backtest.data import ciccwm_client as _ciccwm_client

# ── 常量 ─────────────────────────────────────────────────────────────────

# xtquant 格式：600519.SH / 000001.SZ / 600519.BJ / 00700.HK
_XT_SYMBOL_RE = re.compile(r"^(\d{4,6})\.([A-Z]+)$")

# 纯数字代码格式：600519 / 000001
_BARE_CODE_RE = re.compile(r"^\d{6}$")

# 后缀 → ciccwm market 数值（引用 ciccwm_client 常量，保持单一真源）
_SUFFIX_TO_CICCWM_MARKET: dict[str, int] = {
    "SZ": _ciccwm_client.MARKET_SHENZHEN,
    "SH": _ciccwm_client.MARKET_SHANGHAI,
    "BJ": _ciccwm_client.MARKET_BSE,
    "HK": _ciccwm_client.MARKET_HK,
    "US": _ciccwm_client.MARKET_US,
}

# ciccwm market 数值 → 后缀（反向转换）
_CICCWM_MARKET_TO_SUFFIX: dict[int, str] = {v: k for k, v in _SUFFIX_TO_CICCWM_MARKET.items()}


# ── xtquant ↔ ciccwm ────────────────────────────────────────────────────


def xt_to_ciccwm(symbol: str) -> tuple[str, int]:
    """将 xtquant 格式代码转为 ciccwm ``(code, market)``。

    Args:
        symbol: xtquant 格式，如 ``600519.SH`` / ``000001.SZ``

    Returns:
        (code, market) 元组，如 ``("600519", 1)``

    Raises:
        ValueError: 无法识别的代码格式或未知市场后缀
    """
    m = _XT_SYMBOL_RE.match(symbol)
    if not m:
        raise ValueError(f"无法解析的代码格式: {symbol}")
    code = m.group(1)
    suffix = m.group(2)
    market = _SUFFIX_TO_CICCWM_MARKET.get(suffix)
    if market is None:
        raise ValueError(f"未知市场后缀: {suffix} (symbol={symbol})")
    return code, market


def ciccwm_to_xt(code: str, market: int) -> str:
    """将 ciccwm ``(code, market)`` 转为 xtquant 格式。

    Raises:
        ValueError: 未知市场代码
    """
    suffix = _CICCWM_MARKET_TO_SUFFIX.get(market)
    if suffix is None:
        raise ValueError(f"未知市场代码: {market}")
    return f"{code}.{suffix}"


# ── xtquant ↔ akshare ───────────────────────────────────────────────────


def xt_to_ak(symbol: str) -> str:
    """将 xtquant 代码转为 akshare 代码（去掉市场后缀）。

    ``600519.SH`` → ``600519``；无法解析时原样返回。
    """
    m = _XT_SYMBOL_RE.match(symbol)
    return m.group(1) if m else symbol


def ak_to_xt(code: str) -> str:
    """将 akshare 纯数字代码转为 xtquant 格式。

    6/9 开头 → ``.SH``，其余 → ``.SZ``。
    """
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


# ── xtquant 规范化 ──────────────────────────────────────────────────────


def normalize_xt(symbol: str) -> str:
    """确保符号为 xtquant 格式（纯数字代码补齐市场后缀）。

    已含后缀的符号原样返回；纯 6 位数字按前缀补 ``.SH`` / ``.SZ``。

    集中此处避免各调用方（backtest_service / polars_adapter 等）重复实现。
    """
    if "." in symbol:
        return symbol
    if _BARE_CODE_RE.match(symbol):
        if symbol.startswith(("60", "68")):
            return f"{symbol}.SH"
        if symbol.startswith(("00", "30")):
            return f"{symbol}.SZ"
    return symbol

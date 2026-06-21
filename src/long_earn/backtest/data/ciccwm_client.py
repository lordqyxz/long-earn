"""ciccwm 财经数据 HTTP 客户端模块。

底层 HTTP 客户端 + 凭证加载，与 ciccwm skill 脚本逻辑等价。
纯标准库 urllib，无第三方依赖。

服务地址：https://skill.ciccwm.com/zzt/ext/fcgi/common.fcgi
鉴权：Cookie: apiKey=<key>，凭证存于 ~/.config/ciccwm/config.json 的 CICCWM_API_KEY 字段
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 服务端点
_CICCWM_BASE_URL = "https://skill.ciccwm.com/zzt/ext/fcgi/common.fcgi"

# 凭证文件路径
_CICCWM_CREDENTIAL_PATH = Path.home() / ".config" / "ciccwm" / "config.json"

# 市场代码映射：xtquant 后缀 → ciccwm market 数值
# 0=深, 1=沪, 2=北, 31=港, 74=美股, 12=美股指数
_SUFFIX_TO_MARKET: dict[str, int] = {
    "SH": 1,
    "SZ": 0,
    "BJ": 2,
    "HK": 31,
}

# 请求超时（秒）
_REQUEST_TIMEOUT = 30

# API 鉴权失败返回码
_AUTH_FAIL_RET_CODE = 5002


class CiccwmCredentialError(Exception):
    """ciccwm 凭证缺失或无效。"""


class CiccwmApiError(Exception):
    """ciccwm API 返回业务错误。"""


def _load_api_key() -> str:
    """从 ~/.config/ciccwm/config.json 加载 CICCWM_API_KEY。

    Returns:
        API key 字符串

    Raises:
        CiccwmCredentialError: 凭证文件不存在、格式错误或 key 为空
    """
    path = _CICCWM_CREDENTIAL_PATH
    if not path.exists():
        raise CiccwmCredentialError(
            f"ciccwm 凭证文件不存在: {path}。"
            "请通过 skills-center 安装 ciccwm skill 或手动创建该文件。"
        )
    try:
        with path.open(encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError:
        raise CiccwmCredentialError(
            f"ciccwm 凭证文件格式错误: {path}。"
            "文件必须是 UTF-8 编码的 JSON（注意 Windows PowerShell 的 BOM）。"
        ) from None

    api_key = (config.get("CICCWM_API_KEY") or "").strip()
    if not api_key:
        raise CiccwmCredentialError(
            f"ciccwm 凭证文件中 CICCWM_API_KEY 为空: {path}"
        )
    return api_key


def _parse_symbol(symbol: str) -> tuple[str, int]:
    """将 xtquant 格式股票代码转为 ciccwm 的 (code, market) 元组。

    Args:
        symbol: xtquant 格式，如 600519.SH / 000001.SZ / 832735.BJ

    Returns:
        (code, market) — code 为 6 位数字字符串，market 为 ciccwm 市场码

    Raises:
        ValueError: 无法解析的代码格式
    """
    if "." in symbol:
        code, suffix = symbol.split(".", 1)
        market = _SUFFIX_TO_MARKET.get(suffix.upper())
        if market is None:
            raise ValueError(f"未知的市场后缀 '{suffix}'，来自 symbol='{symbol}'")
        return code, market
    # 无后缀时尝试根据代码前缀推断
    code = symbol
    if code.startswith(("6", "9")):
        return code, 1  # 沪市
    elif code.startswith(("0", "3")):
        return code, 0  # 深市
    elif code.startswith(("4", "8")):
        return code, 2  # 北交所
    raise ValueError(f"无法推断 market 代码: {symbol}")


def _send_request(
    cmdname: str,
    param: dict[str, Any],
    entry: str = "",
    tdx_param: str = "",
    timeout: int = _REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """发送 ciccwm API 请求。

    Args:
        cmdname: 命令名称
        param: 命令参数 dict
        entry: 可选 entry 参数
        tdx_param: 可选 tdx_param 参数
        timeout: 请求超时秒数

    Returns:
        解析后的 JSON 响应 dict

    Raises:
        CiccwmCredentialError: 凭证无效
        CiccwmApiError: API 返回业务错误
        urllib.error.URLError: 网络错误
    """
    api_key = _load_api_key()

    # 构造请求体
    body: dict[str, Any] = {
        "cmdname": cmdname,
        "param": param,
    }
    if entry:
        body["entry"] = entry
    if tdx_param:
        body["tdx_param"] = tdx_param

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Cookie": f"apiKey={api_key}",
    }

    req = urllib.request.Request(
        _CICCWM_BASE_URL,
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        logger.warning(f"ciccwm HTTP 请求失败 (cmdname={cmdname}): {e}")
        raise

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"ciccwm 响应 JSON 解析失败: {e}, raw={raw[:200]}")
        raise CiccwmApiError(f"响应 JSON 解析失败: {e}") from e

    # 检查业务状态码
    ret = result.get("ret", -1)
    if ret == _AUTH_FAIL_RET_CODE:
        raise CiccwmCredentialError(
            "ciccwm 鉴权失败 (ret=5002)，API key 已失效或已吊销，请重新安装 skill。"
        )
    if ret != 0:
        raise CiccwmApiError(
            f"ciccwm API 返回错误 (cmdname={cmdname}, ret={ret}): "
            f"{result.get('msg', '')}"
        )

    return result


def _extract_list(result: dict[str, Any]) -> list[dict[str, Any]]:
    """从 ciccwm 响应中提取 ListHead/ListItem 格式的记录列表。

    ciccwm 的列表响应格式为：
        { "rsp": { "rsp_json": { "ListHead": {...}, "ListItem": [...] } } }
    或简化为：
        { "rsp": { "rsp_json": { "ListItem": [...] } } }
    """
    try:
        rsp_json = result["rsp"]["rsp_json"]
    except (KeyError, TypeError):
        return []

    list_item = rsp_json.get("ListItem", [])
    if isinstance(list_item, dict):
        # 单条记录时 ListItem 可能为 dict
        return [list_item]
    return list(list_item)


# ── 对外 API 函数 ──────────────────────────────────────────────────────


def fetch_info(code: str, market: int) -> dict[str, Any]:
    """获取证券详情。"""
    result = _send_request(
        cmdname="QUOTE",
        param={"code": code, "market": market, "type": "info"},
    )
    items = _extract_list(result)
    return items[0] if items else {}


def fetch_fund_flow(code: str, market: int) -> list[dict[str, Any]]:
    """获取个股资金流向。"""
    result = _send_request(
        cmdname="QUOTE",
        param={"code": code, "market": market, "type": "fundFlow"},
    )
    return _extract_list(result)


def fetch_ranking(
    market: int,
    sort_type: int = 3,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """获取涨跌幅排行。

    Args:
        market: 市场代码（0=深, 1=沪）
        sort_type: 排序类型（3=涨跌幅, ...）
        limit: 返回条数，最大 80

    Returns:
        排行记录列表
    """
    result = _send_request(
        cmdname="QUOTE",
        param={
            "code": str(market),
            "market": market,
            "type": "ranking",
            "sortType": sort_type,
            "limit": min(limit, 80),
        },
    )
    return _extract_list(result)


def fetch_history(code: str, market: int, days: int = 5) -> list[dict[str, Any]]:
    """获取多日历史行情。默认近 5 日（API 侧硬限制）。"""
    result = _send_request(
        cmdname="QUOTE",
        param={
            "code": code,
            "market": market,
            "type": "history",
            "days": days,
        },
    )
    return _extract_list(result)


def fetch_related_blocks(code: str, market: int) -> list[dict[str, Any]]:
    """获取个股关联板块。"""
    result = _send_request(
        cmdname="QUOTE",
        param={
            "code": code,
            "market": market,
            "type": "relatedBlocks",
        },
    )
    return _extract_list(result)


def query_finance(
    statement: str,
    code: str,
    qtime: str = "",
    gtype: str = "0",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """查询财务数据。

    Args:
        statement: 报表类型 — "indicators"(主要指标) / "income"(利润表)
                   / "cashflow"(现金流量表) / "balance"(资产负债表)
        code: 股票代码（6 位数字）
        qtime: 查询时间基点，格式 YYYYMMDD，默认最近一期
        gtype: "0"=合并报表, "1"=母公司报表
        limit: 返回期数，默认近 5 期

    Returns:
        财务记录列表
    """
    result = _send_request(
        cmdname="FINANCE",
        param={
            "statement": statement,
            "code": code,
            "qtime": qtime,
            "gtype": gtype,
            "limit": str(limit),
        },
    )
    return _extract_list(result)


def query_hot_rank(page_size: int = 10) -> list[dict[str, Any]]:
    """获取今日热榜。"""
    result = _send_request(
        cmdname="HOT",
        param={"pageSize": page_size, "type": "rank"},
    )
    return _extract_list(result)


def query_topic_info(subject_id: int | None = None) -> list[dict[str, Any]]:
    """获取专题资讯列表。"""
    param: dict[str, Any] = {"type": "topicInfo"}
    if subject_id is not None:
        param["subjectId"] = subject_id
    result = _send_request(cmdname="HOT", param=param)
    return _extract_list(result)

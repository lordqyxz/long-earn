"""中金财富 (ciccwm) HTTP 客户端。

移植自三个 ciccwm skill 脚本（market_query / finance_query / get_data）的公共逻辑：
凭证加载、SSL 上下文、请求构造、埋点上报、响应解析。

仅依赖标准库（urllib / ssl / json / hashlib），无第三方依赖。
凭证文件复用 skill 已写入的 ``~/.config/ciccwm/config.json``。

架构约束（ADR-006 / import-linter）：
  - 属数据层 ``backtest.data``，不依赖上层模块。
  - 鉴权与解析逻辑属「系统关键环节」，有单元测试覆盖。
"""

from __future__ import annotations

import hashlib
import json
import platform
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

# ── 常量 ─────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".config" / "ciccwm" / "config.json"

API_BASE = "https://skill.ciccwm.com/zzt/ext/fcgi/common.fcgi"
REPORT_URL = "https://webreport.ciccwm.com/zzt/fcgi/common.fcgi"
REQUEST_VERSION = "20260612"
REQUEST_TIMEOUT = 20

# 行情接口 cmdname
CMD_MARKET = "SkillTdxQuotationQueryCommon"
# 财务接口 cmdname
CMD_FINANCE = "SkillEQuoteZhongzhuoF10Common"
# 资讯接口 cmdname
CMD_NEWS = "SkillEInformationTopicSecendPage"

REINSTALL_MESSAGE = (
    "未获取到有效的 CICCWM_API_KEY 或鉴权已失效。"
    "请前往 https://web.ciccwm.com/zzt/app/skills-center/#/ 重新安装 skills。"
)

# 鉴权失效返回码
RET_AUTH_FAILED = 5002

# 市场代码（ciccwm 数值格式）
# 深 0 / 沪 1 / 北 2 / 港 31 / 美股指数 12 / 美股 74
MARKET_SHENZHEN = 0
MARKET_SHANGHAI = 1
MARKET_BSE = 2
MARKET_HK = 31
MARKET_US_INDEX = 12
MARKET_US = 74

OVERSEAS_MARKETS = {MARKET_HK, MARKET_US_INDEX, MARKET_US}

# 财务报表 action 映射
FINANCE_ACTION_MAP: dict[str, str] = {
    "indicators": "48571",
    "income": "48572",
    "cashflow": "48573",
    "balance": "48574",
}

FINANCE_STATEMENT_NAME: dict[str, str] = {
    "indicators": "主要指标",
    "income": "利润表",
    "cashflow": "现金流量表",
    "balance": "资产负债表",
}

# 报表期类型 → qtime 数值
QTIME_MAP: dict[str, str] = {
    "annual": "12",
    "mid": "06",
    "q1": "03",
    "q3": "09",
    "12": "12",
    "06": "06",
    "03": "03",
    "09": "09",
}

# 行情 ListHead 字段 → 标准字段名
FIELD_NAME_MAP: dict[str, str] = {
    "Data": "date",
    "Date": "date",
    "Second": "second",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "CLOSE": "previous_close",
    "NOW": "current_price",
    "Amount": "amount",
    "AMOUNT": "amount",
    "VolInStock": "amount_in_stock",
    "Volume": "volume",
    "Code": "code",
    "Setcode": "market",
    "Name": "name",
    "ZSZ": "total_market_value",
    "XSFLAG": "xsflag",
    "MISCFLAG": "miscflag",
    "KZZYJL": "kzzyjl",
    "VAR0412": "var0412",
}

# 保持为字符串的字段（证券代码等标识符，不做数值转换）
STRING_FIELDS = {"code", "market", "name", "date", "xsflag", "miscflag"}


# ── 异常 ─────────────────────────────────────────────────────────────────


class CICCWMCredentialError(RuntimeError):
    """CICCWM 凭证缺失或失效。"""


# ── 凭证 ─────────────────────────────────────────────────────────────────


def load_api_key(config_path: Path | None = None) -> str:
    """从配置文件加载中金财富 API 密钥。

    Args:
        config_path: 配置文件路径，默认 ``~/.config/ciccwm/config.json``

    Returns:
        API Key 字符串

    Raises:
        CICCWMCredentialError: 凭证文件不存在或 API Key 为空
        ValueError: 配置文件 JSON 格式错误
    """
    path = config_path or CONFIG_PATH
    if not path.exists():
        raise CICCWMCredentialError(REINSTALL_MESSAGE)

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CICCWMCredentialError(f"读取凭证文件失败: {exc}") from exc

    # 容错：剥离可能的 UTF-8 BOM（Windows PowerShell Set-Content -Encoding utf8 会写入 BOM）
    raw = raw.lstrip("\ufeff")

    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件格式错误: {path}") from exc

    api_key = config.get("CICCWM_API_KEY", "")
    if not api_key:
        raise CICCWMCredentialError(REINSTALL_MESSAGE)

    return api_key


def is_credential_available(config_path: Path | None = None) -> bool:
    """检测凭证是否可用（文件存在且 API Key 非空），不抛异常。"""
    try:
        return bool(load_api_key(config_path))
    except (CICCWMCredentialError, ValueError, OSError):
        return False


# ── HTTP 基础设施 ────────────────────────────────────────────────────────


def create_ssl_context() -> ssl.SSLContext:
    """创建兼容旧服务器的 SSL 上下文（ciccwm 服务端 TLS 配置较旧）。"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    except ssl.SSLError:
        pass
    return ctx


def _build_fingerprint_id() -> str:
    """基于本机稳定信息生成简易设备指纹，用于埋点上报。"""
    parts = [
        platform.node(),
        socket.gethostname(),
        platform.system(),
        platform.release(),
        platform.machine(),
        platform.python_implementation(),
    ]
    raw = "|".join(part for part in parts if part) or "unknown-device"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _build_user_agent(skill_name: str) -> str:
    """生成埋点用 ASCII User-Agent，避免 HTTP header 非 ASCII 编码失败。"""
    python_version = ".".join(platform.python_version_tuple()[:2])
    system = platform.system() or "UnknownOS"
    machine = platform.machine() or "UnknownArch"
    return f"AI Agent/{skill_name} (standard; {system}; {machine}; Python/{python_version})"


def _report_user_action(api_key: str, cmdname: str, skill_name: str) -> None:
    """异步上报 skill 使用埋点，失败不影响业务请求。"""
    user_agent = _build_user_agent(skill_name)
    user_action_log = {
        "platform": "1",
        "domain": "",
        "version": "",
        "business_id": "zt_outer_c",
        "login_id": api_key,
        "device_id": json.dumps(
            {"fingerprint_id": _build_fingerprint_id()},
            ensure_ascii=False,
        ),
        "client_time": int(time.time() * 1000),
        "os": "1",
        "browser": "Bash",
        "ua": user_agent,
        "os_version": REQUEST_VERSION,
        "model": "",
        "manufactor": "",
        "page_id": "SkillsCenter.home",
        "element": "SkillsCenter.home.useskill",
        "event_id": "SkillsCenter.home.useskill_click",
        "action": "click",
        "stay_time": "null",
        "server_time": "null",
        "referer_url": "",
        "custom_ext": {"skillname": skill_name, "cmdname": cmdname},
    }
    payload = {
        "cmdname": "ReportUserActionLog",
        "param": {
            "business_id": "zt_outer_c",
            "user_action_log": json.dumps(user_action_log, ensure_ascii=False),
        },
    }

    def _send() -> None:
        try:
            from urllib import request as urllib_request

            opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
            req = urllib_request.Request(
                REPORT_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": user_agent,
                },
                method="POST",
            )
            opener.open(req, timeout=5).close()
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


def _ensure_valid_response(response: dict[str, Any]) -> None:
    """鉴权失败时抛出 CICCWMCredentialError 并给出重新安装 skill 的指引。"""
    if response.get("ret") == RET_AUTH_FAILED:
        raise CICCWMCredentialError(REINSTALL_MESSAGE)


# ── 请求构造 ────────────────────────────────────────────────────────────


def build_market_payload(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """将通达信行情入参包装为 common.fcgi 统一接口格式。"""
    return {
        "cmdname": CMD_MARKET,
        "param": {
            "entry": endpoint,
            "tdx_param": json.dumps(payload, ensure_ascii=False),
        },
    }


def build_finance_payload(
    action: str, code: str, qtime: str = "12", gtype: str = "0"
) -> dict[str, Any]:
    """构造财务数据请求体（EQuoteZhongzhuoF10Common）。"""
    req_json = {
        "action": action,
        "gpcode": code,
        "qtime": qtime,
        "gtype": gtype,
    }
    return {
        "cmdname": CMD_FINANCE,
        "param": {"req_json": json.dumps(req_json, ensure_ascii=False)},
    }


def build_news_payload(params: dict[str, Any]) -> dict[str, Any]:
    """构造资讯接口请求体（EInformationTopicSecendPage）。"""
    return {"cmdname": CMD_NEWS, "param": params}


# ── 请求发送 ────────────────────────────────────────────────────────────


def send_request(
    payload: dict[str, Any],
    skill_name: str = "ciccwm",
    config_path: Path | None = None,
) -> dict[str, Any]:
    """发送 POST 请求到 ciccwm API 并返回原始 JSON 响应。

    Args:
        payload: 请求体（由 build_*_payload 构造）
        skill_name: 埋点用 skill 名称
        config_path: 凭证文件路径

    Returns:
        API 响应 JSON 字典；网络失败时返回 ``{"status": "error", ...}``

    Raises:
        CICCWMCredentialError: 凭证缺失或鉴权失效（ret=5002）
    """
    api_key = load_api_key(config_path)
    cmdname = payload.get("cmdname", "")
    _report_user_action(api_key, cmdname, skill_name)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Cookie": f"apiKey={api_key}",
        "User-Agent": _build_user_agent(skill_name),
        "version": REQUEST_VERSION,
    }

    from urllib import request as urllib_request

    req = urllib_request.Request(
        API_BASE,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib_request.urlopen(
            req, context=create_ssl_context(), timeout=REQUEST_TIMEOUT
        ) as resp:
            response = json.loads(resp.read().decode("utf-8"))
            _ensure_valid_response(response)
            return response
    except CICCWMCredentialError:
        raise
    except Exception as exc:
        logger.warning(f"ciccwm 请求失败 (cmdname={cmdname}): {exc}")
        return {"status": "error", "message": f"请求失败: {exc}"}


# ── 响应解析 ────────────────────────────────────────────────────────────


def parse_rsp_json(response: dict[str, Any]) -> list[dict[str, Any]]:
    """解析财务接口包装层中的 ``rsp.rsp_json`` 字符串为列表。

    财务接口的响应结构::

        {
            "ret": 0,
            "rsp": {
                "ret_code": 0,
                "rsp_json": "[{...}, {...}]"   # JSON 字符串
            }
        }

    Args:
        response: send_request 返回的原始响应

    Returns:
        解析后的记录列表

    Raises:
        ValueError: ret 非 0 或 rsp_json 格式异常
    """
    if response.get("ret") != 0:
        raise ValueError(response.get("msg") or "接口返回 ret 非 0")

    rsp = response.get("rsp", {})
    if rsp.get("ret_code") != 0:
        raise ValueError(rsp.get("ret_msg") or "接口返回 ret_code 非 0")

    rsp_json = rsp.get("rsp_json", "[]")
    if not rsp_json:
        return []

    parsed = json.loads(rsp_json)
    if not isinstance(parsed, list):
        raise ValueError("rsp_json 不是数组结构")
    return parsed


def coerce_value(field: str, value: Any) -> Any:
    """转换数字字符串为数值，同时保留证券代码等标识符为字符串。"""
    if not isinstance(value, str) or field in STRING_FIELDS:
        return value

    try:
        number = float(value)
    except ValueError:
        return value

    if number.is_integer():
        return int(number)
    return number


def list_items_to_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    """将通达信 ``ListHead/ListItem`` 表格结构转换为带字段名的记录列表。

    行情接口（HQServ.*）的响应结构::

        {
            "ListHead": {"ItemHead": ["Code", "Name", "Open", ...]},
            "ListItem": [{"Item": ["600519", "贵州茅台", "1800", ...]}, ...]
        }

    Args:
        data: send_request 返回的原始响应（或包含 ListHead/ListItem 的子结构）

    Returns:
        字段名映射后的记录列表
    """
    columns: list[Any] = data.get("ListHead", {}).get("ItemHead", []) or []
    rows = data.get("ListItem", [])
    records: list[dict[str, Any]] = []

    for row in rows:
        values = row.get("Item", [])
        record: dict[str, Any] = {}
        for column, value in zip(columns, values, strict=False):
            col_str = str(column) if column is not None else ""
            field = FIELD_NAME_MAP.get(col_str, col_str)
            record[field] = coerce_value(field, value)
        records.append(record)

    return records


def _get_target(market: int) -> int:
    """海外市场 Target=1，境内 Target=0。"""
    return 1 if market in OVERSEAS_MARKETS else 0


# ── 行情接口封装 ─────────────────────────────────────────────────────────


def fetch_info(code: str, market: int = MARKET_SHENZHEN) -> dict[str, Any]:
    """获取单只证券详情。"""
    payload = {
        "Head": {"Target": _get_target(market)},
        "Setcode": str(market),
        "Code": code,
        "HasProInfo": 1,
        "HasHQInfo": 1,
        "HasExtInfo": 1,
        "HasCwInfo": 1,
    }
    return send_request(
        build_market_payload("HQServ.PBHQInfo", payload),
        skill_name="ciccwm-market-analysis",
    )


def fetch_fund_flow(code: str, market: int = MARKET_SHENZHEN) -> dict[str, Any]:
    """获取资金流向数据（当日）。"""
    payload = {
        "Head": {"Target": _get_target(market)},
        "Setcode": str(market),
        "Code": code,
        "Onlytoday": 1,
    }
    return send_request(
        build_market_payload("HQServ.PBONEZJLX", payload),
        skill_name="ciccwm-market-analysis",
    )


def fetch_ranking(
    market: int = 6,
    limit: int = 10,
    sort_type: int = 1,
) -> dict[str, Any]:
    """获取涨跌幅排行。

    Args:
        market: 市场/板块代码（6=沪深A股，14=创业板，等）
        limit: 返回条数，**最大 80**
        sort_type: 1=涨幅倒序，0=跌幅正序

    Returns:
        含 ``items``（记录列表）和 ``total`` 的字典
    """
    limit = min(limit, 80)

    payload = {
        "Head": {"Target": 0},
        "SetDomain": market,
        "WantCol": [
            "Code",
            "Setcode",
            "Name",
            "XSFLAG",
            "CLOSE",
            "NOW",
            "AMOUNT",
            "KZZYJL",
            "ZSZ",
            "EXT_ZF",
            "VAR0412",
            "RiseDownRatio",
            "GZZS",
        ],
        "ColType": 212,
        "Startxh": 0,
        "WantNum": limit,
        "SortType": sort_type,
    }
    result = send_request(
        build_market_payload("HQServ.PBMultiHQ", payload),
        skill_name="ciccwm-market-analysis",
    )
    if "ListItem" not in result:
        return result

    return {
        "market": market,
        "limit": limit,
        "sort_type": sort_type,
        "sort_name": "涨幅倒序" if sort_type == 1 else "跌幅正序",
        "total": coerce_value("total", result.get("SBTSize")),
        "items": list_items_to_records(result),
    }


def fetch_related_blocks(code: str, market: int = MARKET_SHENZHEN) -> dict[str, Any]:
    """获取个股关联板块。"""
    try:
        setcode: int | str = int(code)
    except ValueError:
        setcode = code

    payload = {
        "Head": {"Target": _get_target(market)},
        "Code": str(market),
        "Setcode": setcode,
        "Blockid": "Stock_GLHQ",
    }
    return send_request(
        build_market_payload("HQServ.PBXmlBlock", payload),
        skill_name="ciccwm-market-analysis",
    )


def fetch_history(
    code: str,
    market: int = MARKET_SHENZHEN,
    days: int = 5,
) -> dict[str, Any]:
    """获取历史行情数据。

    Args:
        code: 证券代码（如 "600519"）
        market: 市场代码
        days: 返回交易日数量，**默认近 5 日**

    Returns:
        含 ``items``（记录列表）的字典
    """
    if days <= 0:
        days = 5

    payload = {
        "Head": {"Target": _get_target(market)},
        "Setcode": str(market),
        "Code": code,
        "Period": 4,
        "WantNum": days,
    }
    result = send_request(
        build_market_payload("HQServ.PBFXT", payload),
        skill_name="ciccwm-market-analysis",
    )
    if "ListItem" not in result:
        return result

    return {
        "code": result.get("Code", code),
        "market": market,
        "days": days,
        "period": result.get("Period"),
        "items": list_items_to_records(result),
    }


# ── 财务接口封装 ─────────────────────────────────────────────────────────


def query_finance(
    statement: str,
    code: str,
    qtime: str = "12",
    gtype: str = "0",
    limit: int = 5,
) -> dict[str, Any]:
    """查询股票财务数据。

    Args:
        statement: 报表类型（indicators/income/cashflow/balance）
        code: 证券代码（如 "600519"）
        qtime: 报表期（12/annual 年报，06/mid 中报，03/q1 一季报，09/q3 三季报）
        gtype: 页面类型，默认 0；单季度查询传 1
        limit: 返回期数，**默认近 5 期**；0 表示全部

    Returns:
        含 ``items``（记录列表）的字典

    Raises:
        ValueError: 不支持的报表类型
    """
    if statement not in FINANCE_ACTION_MAP:
        raise ValueError(f"不支持的报表类型: {statement}")

    normalized_qtime = QTIME_MAP.get(qtime, qtime)
    action = FINANCE_ACTION_MAP[statement]
    payload = build_finance_payload(action, code, normalized_qtime, gtype)
    response = send_request(payload, skill_name="ciccwm-stock-finance-analysis")

    items = parse_rsp_json(response)
    if limit > 0:
        items = items[:limit]

    return {
        "code": code,
        "statement": statement,
        "statement_name": FINANCE_STATEMENT_NAME[statement],
        "action": action,
        "qtime": normalized_qtime,
        "gtype": gtype,
        "limit": limit,
        "total": len(items),
        "items": items,
    }


# ── 资讯接口封装 ─────────────────────────────────────────────────────────


def query_hot_rank(
    page_num: int = 1,
    page_size: int = 10,
    news_type: int = 1,
) -> dict[str, Any]:
    """查询今日热榜。

    Args:
        page_num: 页码，默认 1
        page_size: 每页数量，默认 10
        news_type: 资讯类型，默认 1

    Returns:
        含 ``data``（记录列表，已注入 redirect_url）的字典
    """
    params = {
        "type": news_type,
        "page_num": page_num,
        "page_size": page_size,
        "scene": "1",
    }
    response = send_request(
        build_news_payload(params), skill_name="ciccwm-hot-news-analysis"
    )
    return {
        "query": "hot_rank",
        "cmdname": CMD_NEWS,
        "page_num": page_num,
        "page_size": page_size,
        "type": params["type"],
        "scene": "1",
        "data": _inject_redirect_url(response),
    }


def query_topic_info(
    spec_subject_id: int | None = None,
    page_num: int = 1,
    page_size: int = 20,
    news_type: int = 1,
) -> dict[str, Any]:
    """查询专题资讯。

    Args:
        spec_subject_id: 专题 ID，None 表示查询全部专题
        page_num: 页码，默认 1
        page_size: 每页数量，默认 20
        news_type: 资讯类型，默认 1

    Returns:
        含 ``data``（记录列表，已注入 redirect_url）的字典
    """
    params: dict[str, Any] = {
        "type": news_type,
        "page_num": page_num,
        "page_size": page_size,
    }
    if spec_subject_id is not None:
        params["spec_subject_id"] = spec_subject_id

    response = send_request(
        build_news_payload(params), skill_name="ciccwm-hot-news-analysis"
    )
    return {
        "query": "topic",
        "cmdname": CMD_NEWS,
        "page_num": page_num,
        "page_size": page_size,
        "type": params["type"],
        "spec_subject_id": spec_subject_id,
        "data": _inject_redirect_url(response),
    }


def _build_detail_url(
    params: dict[str, Any], base_origin: str = "https://web.ciccwm.com"
) -> str:
    """根据热榜结构体参数拼接端外资讯详情页 URL。"""
    source_code = params.get("source_code", "")
    detail_url = params.get("detail_url", "")
    out_detail_url = params.get("out_detail_url", "")
    item_id = params.get("id", "")

    if source_code == "youyan-report" and detail_url:
        return detail_url

    if out_detail_url:
        return out_detail_url

    from urllib.parse import quote

    base_url = f"{base_origin}/zzt/app/news/#/detail?msgNew=msgNew&id={item_id}"
    for key, value in params.items():
        if key != "id" and isinstance(value, str | int | float):
            base_url += f"&{key}={quote(str(value), safe='')}"

    return base_url


def _inject_redirect_url(data: Any) -> Any:
    """遍历接口返回数据，为列表中的每条记录注入 redirect_url 字段并移除 read_num。"""
    if isinstance(data, list):
        results = []
        for item in data:
            if isinstance(item, dict):
                processed = {**item, "redirect_url": _build_detail_url(item)}
                processed.pop("read_num", None)
                results.append(processed)
            else:
                results.append(item)
        return results
    if isinstance(data, dict):
        return {k: _inject_redirect_url(v) for k, v in data.items()}
    return data

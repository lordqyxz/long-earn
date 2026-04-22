"""回测工具模块

通过 HTTP API（支持 Unix Domain Socket）调用远程回测服务。
使用连接池复用和断路器提升稳定性。
"""

import logging
import os
import time
from enum import Enum
from typing import Any

import httpx

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class _CircuitState(Enum):
    """断路器状态"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _CircuitBreaker:
    """简易断路器（不引入外部依赖）"""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ):
        """初始化断路器

        Args:
            failure_threshold: 触发 OPEN 的连续失败次数阈值
            recovery_timeout: OPEN 后进入 HALF_OPEN 的冷却时间（秒）
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = _CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0

    @property
    def state(self) -> _CircuitState:
        """当前状态（自动处理 OPEN -> HALF_OPEN 转换）"""
        if (
            self._state == _CircuitState.OPEN
            and time.time() - self._last_failure_time >= self.recovery_timeout
        ):
            self._state = _CircuitState.HALF_OPEN
            logger.info("断路器进入 HALF_OPEN 状态")
        return self._state

    def record_success(self) -> None:
        """记录成功"""
        self._failure_count = 0
        if self._state == _CircuitState.HALF_OPEN:
            self._state = _CircuitState.CLOSED
            logger.info("断路器关闭 (CLOSED)")

    def record_failure(self) -> None:
        """记录失败"""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.failure_threshold:
            self._state = _CircuitState.OPEN
            logger.warning(f"断路器打开 (OPEN)：连续失败 {self._failure_count} 次")

    def can_execute(self) -> bool:
        """检查当前是否允许执行请求"""
        return self._state in (_CircuitState.CLOSED, _CircuitState.HALF_OPEN)


# 模块级复用对象（连接池 + 断路器）
_client: httpx.Client | None = None
_circuit = _CircuitBreaker()


def _get_client() -> httpx.Client:
    """获取或创建复用的 httpx Client（连接池级别复用）"""
    global _client
    if _client is None:
        _client = httpx.Client()
    return _client


def close_client() -> None:
    """显式关闭连接池（建议在程序退出时调用）"""
    global _client
    if _client is not None:
        _client.close()
        _client = None


def _service_url(override: str = "") -> str:
    """解析回测服务地址"""
    return override or os.getenv("BACKTEST_SERVICE_URL", "http://localhost:8001")


def run_backtest(
    strategy_path: str = "",
    strategy_code: str = "",
    start_date: str = "2020-01-01",
    end_date: str = "2023-12-31",
    stock_list: list[str] | None = None,
    timeout: float = 30.0,
    service_url: str = "",
) -> dict[str, Any]:
    """
    回测交易策略（通过远程服务）

    Args:
        strategy_path: 策略文件路径（与 strategy_code 二选一）
        strategy_code: 策略代码字符串（与 strategy_path 二选一）
        start_date: 回测开始日期
        end_date: 回测结束日期
        stock_list: 股票池列表，可选
        timeout: HTTP 请求超时时间（秒），默认 30.0
        service_url: 回测服务地址，支持 http+unix://，默认读环境变量

    Returns:
        dict: 回测结果字典。
        成功时包含绩效指标（total_return, sharpe_ratio 等）；
        失败时包含 error（可读错误信息）、error_category（错误分类）和
        error_detail（详细错误原因）。
    """
    if not _circuit.can_execute():
        return {
            "error": "回测服务暂时不可用（断路器打开），请稍后重试",
            "error_category": "service_unavailable",
            "error_detail": "断路器处于 OPEN 状态，已连续失败达到阈值",
        }

    # 读取策略代码
    try:
        if strategy_code:
            code = strategy_code
        elif strategy_path:
            with open(strategy_path, encoding="utf-8") as f:
                code = f.read()
        else:
            return {
                "error": "必须提供 strategy_path 或 strategy_code",
                "error_category": "client_error",
                "error_detail": "调用方未传入策略路径或策略代码字符串",
            }
    except FileNotFoundError as e:
        return {
            "error": f"策略文件不存在：{strategy_path}",
            "error_category": "client_error",
            "error_detail": str(e),
        }
    except Exception as e:
        return {
            "error": f"读取策略文件失败：{e}",
            "error_category": "client_error",
            "error_detail": str(e),
        }

    url = _service_url(service_url)
    logger.info(f"调用回测服务：{url}")

    try:
        client = _get_client()
        response = client.post(
            f"{url}/api/v1/backtest",
            json={
                "strategy_code": code,
                "start_date": start_date,
                "end_date": end_date,
                "stock_list": stock_list,
            },
            timeout=timeout,
        )
    except httpx.ConnectError as e:
        error_msg = f"无法连接到回测服务 ({url}): {e}"
        logger.error(error_msg)
        logger.error(
            "请确保回测服务正在运行：cd backtest_service && uv run python -m long_earn_backtest"
        )
        _circuit.record_failure()
        return {
            "error": error_msg,
            "error_category": "service_unavailable",
            "error_detail": f"连接失败：{e}。请检查回测服务是否已启动，地址 {url} 是否正确。",
        }
    except httpx.TimeoutException as e:
        error_msg = f"回测服务请求超时 ({timeout}s): {e}"
        logger.error(error_msg)
        _circuit.record_failure()
        return {
            "error": error_msg,
            "error_category": "timeout",
            "error_detail": f"请求在 {timeout} 秒内未得到响应，可能策略代码执行时间过长或服务负载过高。",
        }
    except Exception as e:
        error_msg = f"回测请求异常：{e}"
        logger.error(error_msg)
        _circuit.record_failure()
        return {
            "error": error_msg,
            "error_category": "network_error",
            "error_detail": str(e),
        }

    # 处理 HTTP 响应
    if response.status_code == 200:
        try:
            result = response.json()
        except Exception as e:
            _circuit.record_failure()
            return {
                "error": f"回测服务返回非 JSON 响应：{response.text[:200]}",
                "error_category": "service_error",
                "error_detail": f"HTTP 200 但响应体无法解析为 JSON：{e}",
            }

        if result.get("success"):
            _circuit.record_success()
            logger.info("回测成功")
            return {
                "total_return": result.get("total_return"),
                "annual_return": result.get("annual_return"),
                "sharpe_ratio": result.get("sharpe_ratio"),
                "max_drawdown": result.get("max_drawdown"),
                "win_rate": result.get("win_rate"),
                "trading_days": result.get("trading_days"),
            }

        # 服务端明确返回失败（success=False）
        _circuit.record_failure()
        category = result.get("error_category", "")
        detail = result.get("error_detail", "")
        message = result.get("message", "回测失败")

        if category == "code_logic":
            error_msg = f"【代码逻辑错误】{message}"
        elif category == "strategy_logic":
            error_msg = f"【策略逻辑错误】{message}"
        elif category == "service_error":
            error_msg = f"【服务内部错误】{message}"
        else:
            error_msg = f"回测失败：{message}"
        if detail:
            error_msg += f"\n{detail}"

        logger.error(error_msg)
        return {
            "error": error_msg,
            "error_category": category,
            "error_detail": detail,
            "message": message,
        }

    # 非 200 状态码：尝试解析服务端返回的结构化错误
    _circuit.record_failure()
    try:
        body = response.json()
        detail = body.get("detail", response.text)
    except Exception:
        detail = response.text

    error_msg = f"回测服务返回 HTTP {response.status_code}：{detail[:500]}"
    logger.error(error_msg)
    return {
        "error": error_msg,
        "error_category": "service_error",
        "error_detail": f"HTTP {response.status_code}：{detail}",
    }


def check_service_health(service_url: str = "") -> bool:
    """检查回测服务是否可用"""
    try:
        client = _get_client()
        response = client.get(
            f"{_service_url(service_url)}/health",
            timeout=5.0,
        )
        return response.status_code == 200
    except Exception:
        return False

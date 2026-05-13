"""回测工具模块

直接调用向量化回测引擎，无需 HTTP 往返。
"""

import logging
from typing import Any

from long_earn.backtest.engine.core import run_backtest as _run_backtest

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_backtest(  # noqa: PLR0913
    strategy_path: str = "",
    strategy_code: str = "",  # noqa: ARG001
    strategy_yaml: str = "",
    start_date: str = "2020-01-01",
    end_date: str = "2023-12-31",
    stock_list: list[str] | None = None,  # noqa: ARG001
    timeout: float = 30.0,  # noqa: ARG001
    service_url: str = "",  # noqa: ARG001
) -> dict[str, Any]:
    """
    回测交易策略（向量化引擎）

    Args:
        strategy_path: 策略文件路径（YAML 格式，可选）
        strategy_code: 策略代码字符串（Python，已废弃，保留兼容）
        strategy_yaml: 策略 YAML 字符串（推荐）
        start_date: 回测开始日期
        end_date: 回测结束日期
        stock_list: 股票池列表，可选
        timeout: 已废弃，保留兼容
        service_url: 已废弃，保留兼容

    Returns:
        dict: 回测结果字典。
        成功时包含绩效指标（total_return, sharpe_ratio 等）；
        失败时包含 error（可读错误信息）、error_category（错误分类）和
        error_detail（详细错误原因）。
    """
    # 从文件读取策略
    if strategy_path and not strategy_yaml:
        try:
            with open(strategy_path, encoding="utf-8") as f:
                strategy_yaml = f.read()
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

    if not strategy_yaml:
        return {
            "error": "必须提供 strategy_path 或 strategy_yaml",
            "error_category": "client_error",
            "error_detail": "调用方未传入策略路径或策略 YAML 字符串",
        }

    logger.info(f"执行回测：{start_date} ~ {end_date}")

    try:
        result = _run_backtest(strategy_yaml)
    except Exception as e:
        logger.error(f"回测执行异常：{e}")
        return {
            "error": f"回测执行失败：{e}",
            "error_category": "engine_error",
            "error_detail": str(e),
        }

    if result.success:
        logger.info("回测成功")
        return {
            "total_return": result.total_return,
            "annual_return": result.annual_return,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown,
            "win_rate": result.win_rate,
            "trading_days": result.trading_days,
            "volatility": result.volatility,
            "calmar_ratio": result.calmar_ratio,
            "sortino_ratio": result.sortino_ratio,
            "daily_returns": result.daily_returns,
            "positions_history": result.positions_history,
        }

    logger.error(f"回测失败：{result.message}")
    return {
        "error": result.message,
        "error_category": result.error_category or "unknown",
        "error_detail": result.error_detail or "",
    }


def check_service_health(service_url: str = "") -> bool:  # noqa: ARG001
    """检查回测引擎是否可用（始终返回 True，因为引擎内嵌）"""
    return True


def close_client() -> None:
    """释放资源（已废弃，保留兼容）"""
    pass

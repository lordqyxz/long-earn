import logging
import os

import httpx

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# 回测服务地址
BACKTEST_SERVICE_URL = os.getenv("BACKTEST_SERVICE_URL", "http://localhost:8001")


def run_backtest(
    strategy_path: str = "",
    strategy_code: str = "",
    start_date: str = "2020-01-01",
    end_date: str = "2023-12-31",
    stock_list: list | None = None,
    timeout: float = 30.0,
) -> dict | None:
    """
    回测交易策略（通过远程服务）

    Args:
        strategy_path: 策略文件路径（与 strategy_code 二选一）
        strategy_code: 策略代码字符串（与 strategy_path 二选一）
        start_date: 回测开始日期
        end_date: 回测结束日期
        stock_list: 股票池列表，可选
        timeout: HTTP 请求超时时间（秒），默认 30.0

    Returns:
        dict: 回测结果字典，成功时包含绩效指标；
              失败时包含 error_category（"code_logic" 或 "strategy_logic"）和 error_detail
    """
    try:
        # 读取策略代码
        if strategy_code:
            code = strategy_code
        elif strategy_path:
            with open(strategy_path, encoding="utf-8") as f:
                code = f.read()
        else:
            logger.error("必须提供 strategy_path 或 strategy_code")
            return None

        # 调用远程回测服务
        logger.info(f"调用回测服务：{BACKTEST_SERVICE_URL}")

        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"{BACKTEST_SERVICE_URL}/api/v1/backtest",
                json={
                    "strategy_code": code,
                    "start_date": start_date,
                    "end_date": end_date,
                    "stock_list": stock_list,
                },
            )

            if response.status_code == 200:
                result = response.json()
                if result["success"]:
                    logger.info("回测成功")
                    return {
                        "total_return": result["total_return"],
                        "annual_return": result["annual_return"],
                        "sharpe_ratio": result["sharpe_ratio"],
                        "max_drawdown": result["max_drawdown"],
                        "win_rate": result["win_rate"],
                        "trading_days": result["trading_days"],
                    }
                else:
                    # 构建精准错误信息
                    category = result.get("error_category", "")
                    detail = result.get("error_detail", "")
                    if category == "code_logic":
                        error_msg = f"【代码逻辑错误】{result['message']}"
                    elif category == "strategy_logic":
                        error_msg = f"【策略逻辑错误】{result['message']}"
                    else:
                        error_msg = f"回测失败：{result['message']}"
                    if detail:
                        error_msg += f"\n{detail}"
                    logger.error(error_msg)
                    # 包含 error 字段以兼容调用方 `result.get("error")` 检查
                    return {
                        "error": error_msg,
                        "error_category": category,
                        "error_detail": detail,
                        "message": result["message"],
                    }
            else:
                logger.error(
                    f"回测服务返回错误：{response.status_code} - {response.text}"
                )
                return None

    except httpx.ConnectError as e:
        error_msg = f"无法连接到回测服务 ({BACKTEST_SERVICE_URL}): {e}"
        logger.error(error_msg)
        logger.error(
            "请确保回测服务正在运行：cd backtest_service && uv run python -m long_earn_backtest"
        )
        return None
    except Exception as e:
        error_msg = f"回测失败：{e}"
        logger.error(error_msg)
        return None


def check_service_health() -> bool:
    """检查回测服务是否可用"""
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{BACKTEST_SERVICE_URL}/health")
            return response.status_code == 200
    except Exception:
        return False

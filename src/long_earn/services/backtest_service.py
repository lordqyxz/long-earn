"""回测服务实现

直接调用向量化回测引擎，无需 HTTP 远程调用。
"""

from typing import TYPE_CHECKING, Any

from long_earn.backtest.engine.core import run_backtest as _run_backtest
from long_earn.services import BacktestService

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


class BacktestServiceImpl(BacktestService):
    """回测服务实现（直接调用向量化引擎）

    特性：
    - 直接调用 long_earn.backtest 引擎，零网络开销
    - 支持 YAML DSL 策略描述
    - 自动数据缓存（DuckDB）
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化回测服务

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.logger = context.logger
        self.config = context.config

    def run(
        self,
        strategy_yaml: str,
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """运行回测

        Args:
            strategy_yaml: YAML DSL 策略描述
            start_date: 回测起始日期（覆盖策略默认值）
            end_date: 回测结束日期（覆盖策略默认值）

        Returns:
            回测结果字典。成功时包含绩效指标；失败时包含 error、error_category
            和 error_detail 字段。
        """
        start_date = start_date or getattr(
            self.config, "backtest_start_date", "2020-01-01"
        )
        end_date = end_date or getattr(self.config, "backtest_end_date", "2023-12-31")

        if not strategy_yaml:
            return {
                "error": "必须提供 strategy_yaml",
                "error_category": "client_error",
                "error_detail": "调用方未传入策略",
            }

        if self.logger:
            self.logger.info(f"执行回测: {start_date} ~ {end_date}")

        result = _run_backtest(strategy_yaml)

        if self.logger:
            self.logger.info(
                f"回测完成: total_return={result.total_return}, "
                f"sharpe={result.sharpe_ratio}, "
                f"max_drawdown={result.max_drawdown}"
            )

        if result.success:
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

        return {
            "error": result.message,
            "error_category": result.error_category or "unknown",
            "error_detail": result.error_detail or "",
        }

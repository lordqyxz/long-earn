"""回测服务实现

通过 HTTP API 调用远程回测服务（backtest_service），实现依赖隔离。
"""

from typing import TYPE_CHECKING, Any

from long_earn.services import BacktestService
from long_earn.tools.backtest import run_backtest

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


class BacktestServiceImpl(BacktestService):
    """回测服务实现

    通过 HTTP API 远程调用 backtest_service 执行回测，
    避免主项目直接依赖 qlib/protobuf 等重量级包。
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化回测服务

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.logger = context.logger
        self.config = context.config

    def run_backtest(
        self,
        strategy_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        stock_list: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """运行回测（通过远程 HTTP API）

        Args:
            strategy_code: 策略代码
            start_date: 开始日期，默认使用配置中的日期
            end_date: 结束日期，默认使用配置中的日期
            stock_list: 股票池列表，可选

        Returns:
            回测结果字典
        """
        if start_date is None:
            start_date = self.config.backtest_start_date
        if end_date is None:
            end_date = self.config.backtest_end_date

        if self.logger:
            self.logger.info(
                f"调用远程回测服务: {start_date} ~ {end_date}"
            )

        result = run_backtest(
            strategy_code=strategy_code,
            start_date=start_date,
            end_date=end_date,
            stock_list=stock_list,
        )

        if result is not None and self.logger:
            self.logger.info(
                f"回测完成: 总收益率={result.get('total_return', 'N/A')}, "
                f"夏普比率={result.get('sharpe_ratio', 'N/A')}, "
                f"最大回撤={result.get('max_drawdown', 'N/A')}"
            )

        return result
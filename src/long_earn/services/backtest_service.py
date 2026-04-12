"""回测服务实现

封装 pyqlib 回测功能。
"""

import importlib.util
import os
import tempfile

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
import pandas as pd

from long_earn.services import BacktestService

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService

try:
    from qlib import init
    from qlib.data import D

    qlib_data_path = Path.home() / ".qlib_data"
    if qlib_data_path.exists():
        init(provider_uri=str(qlib_data_path), region="cn")
    QLIB_AVAILABLE = True
except Exception as e:
    print(f"警告：qlib 初始化失败，将使用模拟数据：{e}")
    QLIB_AVAILABLE = False


class BacktestServiceImpl(BacktestService):
    """回测服务实现

    参考 LangGraph Runtime 实践：
    1. 依赖通过 context 传递
    2. 配置化回测参数
    3. 统一错误处理
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
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, Any] | None:
        """运行回测

        Args:
            strategy_code: 策略代码
            start_date: 开始日期，默认使用配置中的日期
            end_date: 结束日期，默认使用配置中的日期

        Returns:
            回测结果字典
        """
        if start_date is None:
            start_date = self.config.backtest_start_date
        if end_date is None:
            end_date = self.config.backtest_end_date

        try:
            strategy_name = "dynamic_strategy"
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(strategy_code)
                temp_path = f.name

            try:
                spec = importlib.util.spec_from_file_location(strategy_name, temp_path)
                if spec is None:
                    if self.logger:
                        self.logger.error("无法从文件创建模块规范")
                    return None
                strategy_module = importlib.util.module_from_spec(spec)
                if spec.loader is not None:
                    spec.loader.exec_module(strategy_module)
            finally:
                os.unlink(temp_path)
        except FileNotFoundError:
            if self.logger:
                self.logger.error(f"策略文件未找到")
            return None
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载策略文件失败：{e}")
            return None

        strategy_class = None
        for name, obj in strategy_module.__dict__.items():
            if hasattr(obj, "generate_signals"):
                strategy_class = obj
                break

        if not strategy_class:
            if self.logger:
                self.logger.error("策略类未找到")
            return None

        try:
            strategy = strategy_class()
        except Exception as e:
            if self.logger:
                self.logger.error(f"创建策略实例失败：{e}")
            return None

        dates = self._get_trading_dates(start_date, end_date)
        daily_returns = []

        for date in dates:
            date_str = date.strftime("%Y-%m-%d")
            try:
                signals = strategy.generate_signals(date_str)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"生成信号失败 ({date_str}): {e}")
                continue

            if signals is None or len(signals) == 0:
                continue

            try:
                if QLIB_AVAILABLE:
                    portfolio_return = self._get_portfolio_return_qlib(
                        signals, date_str
                    )
                else:
                    portfolio_return = self._get_portfolio_return_mock(
                        signals, date_str
                    )

                if portfolio_return is not None:
                    daily_returns.append(portfolio_return)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"计算组合收益失败 ({date_str}): {e}")
                continue

        if not daily_returns:
            if self.logger:
                self.logger.warning("没有有效的回测数据")
            return None

        return self._calculate_metrics(daily_returns, start_date, end_date)

    def _get_trading_dates(self, start_date: str, end_date: str) -> list:
        """获取交易日历

        Args:
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            交易日期列表
        """
        if QLIB_AVAILABLE:
            try:
                from qlib.data import D

                dates = D.calendar(start_time=start_date, end_time=end_date, freq="day")
                return pd.to_datetime(dates).tolist()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"获取交易日历失败：{e}，使用工作日代替")

        return pd.date_range(start=start_date, end=end_date, freq="B").tolist()

    def _get_portfolio_return_qlib(
        self, signals: dict | pd.Series, date_str: str
    ) -> float:
        """使用 qlib 获取真实市场收益率

        Args:
            signals: 交易信号
            date_str: 交易日期字符串

        Returns:
            组合收益率
        """
        if signals is None or (hasattr(signals, "__len__") and len(signals) == 0):
            return 0.0

        stock_list = list(signals.keys())
        if len(stock_list) == 0:
            return 0.0

        try:
            from qlib.data import D

            end_date = pd.Timestamp(date_str)
            start_date = end_date - pd.Timedelta(days=10)

            close_data = D.features(
                stock_list, ["$close"], start_time=start_date, end_time=end_date
            )

            portfolio_return = 0.0
            total_weight = 0.0

            for stock, weight in signals.items():
                if weight == 0:
                    continue

                if stock in close_data.columns:
                    stock_close = close_data[stock]["$close"]
                    if len(stock_close) >= 2:
                        latest_close = stock_close.iloc[-1]
                        prev_close = stock_close.iloc[-2]

                        if prev_close > 0:
                            stock_return = (latest_close - prev_close) / prev_close
                            portfolio_return += weight * stock_return
                            total_weight += abs(weight)

            if total_weight > 0:
                portfolio_return /= total_weight

            return portfolio_return

        except Exception as e:
            if self.logger:
                self.logger.warning(f"获取 qlib 数据失败：{e}")
            return 0.0

    def _get_portfolio_return_mock(
        self, signals: dict | pd.Series, date_str: str
    ) -> float:
        """模拟收益率（仅在 qlib 不可用时使用）

        Args:
            signals: 交易信号
            date_str: 交易日期字符串

        Returns:
            模拟组合收益率
        """
        date_hash = hash(date_str) % (2**32)
        np.random.seed(int(date_hash))

        stock_returns = {stock: np.random.normal(0, 0.02) for stock in signals.keys()}

        portfolio_return = sum(
            signals[stock] * stock_returns.get(stock, 0) for stock in signals.keys()
        ) / max(len(signals), 1)

        return portfolio_return

    def _calculate_metrics(
        self, daily_returns: list, start_date: str, end_date: str
    ) -> dict[str, Any]:
        """计算回测指标

        Args:
            daily_returns: 日收益率列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            回测指标字典
        """
        returns_series = pd.Series(daily_returns)

        total_return = (1 + returns_series).cumprod().iloc[-1] - 1
        sharpe_ratio = (
            returns_series.mean() / returns_series.std() * np.sqrt(252)
            if returns_series.std() != 0
            else 0
        )
        cumulative = (1 + returns_series).cumprod()
        max_drawdown = (
            cumulative.cummax() - cumulative
        ).max() / cumulative.cummax().max()

        if self.logger:
            self.logger.info(f"回测期间：{start_date} 至 {end_date}")
            self.logger.info(f"交易日数量：{len(daily_returns)}")
            self.logger.info(f"总收益率：{total_return:.2%}")
            self.logger.info(
                f"年化收益率：{(1 + total_return) ** (252 / len(daily_returns)) - 1:.2%}"
            )
            self.logger.info(f"夏普比率：{sharpe_ratio:.2f}")
            self.logger.info(f"最大回撤：{max_drawdown:.2%}")

        return {
            "total_return": float(total_return),
            "annual_return": (
                float((1 + total_return) ** (252 / len(daily_returns)) - 1)
                if daily_returns
                else 0.0
            ),
            "sharpe_ratio": float(sharpe_ratio),
            "max_drawdown": float(max_drawdown),
            "win_rate": (
                float((returns_series > 0).sum() / len(returns_series))
                if len(returns_series) > 0
                else 0.0
            ),
            "trading_days": len(daily_returns),
        }

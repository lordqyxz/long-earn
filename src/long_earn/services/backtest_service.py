"""回测服务实现

对接事件驱动回测引擎，支持 YAML DSL 策略描述。
"""

from typing import TYPE_CHECKING, Any

import polars as pl

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import (
    TradingCostConfig,
    parse_strategy_yaml,
)
from long_earn.backtest.engine.evaluator import SafeExpressionEvaluator
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.services import BacktestService

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


class DSLStrategy(BaseStrategy):
    """从 YAML DSL 自动生成的状态化策略"""

    def __init__(self, strategy_id: str, dsl_strategy: Any, config: dict | None = None):
        super().__init__(strategy_id, config)
        self.dsl = dsl_strategy

    def _eval(self, expr: str, df) -> Any:
        """使用 SafeExpressionEvaluator 安全求值"""
        evaluator = SafeExpressionEvaluator(df)
        return evaluator.evaluate(expr)

    def on_bar(self, bars: pl.DataFrame, context) -> Any:
        from long_earn.backtest.domain.entities import SignalEvent

        df = bars.to_pandas()
        if "symbol" not in df.index.names:
            df = df.set_index("symbol")

        # 1. 计算因子并加入 DataFrame
        for alias, expr in self.dsl.factors.items():
            try:
                result = self._eval(expr, df)
                if result is not None:
                    df[alias] = result
            except Exception:
                continue

        # 2. 执行信号步骤
        selected = df.index.unique().tolist()
        for step in self.dsl.signals:
            step_type = step.get("type", "")
            try:
                if step_type == "filter":
                    condition = step.get("condition", "")
                    result = self._eval(condition, df)
                    df_filtered = df[result.fillna(False)]
                    selected = df_filtered.index.unique().tolist()
                elif step_type == "rank":
                    by_field = step.get("by", "")
                    top_n = step.get("top", 10)
                    ascending = step.get("ascending", False)
                    if by_field in df.columns:
                        sorted_df = (
                            df[by_field].dropna().sort_values(ascending=ascending)
                        )
                        selected = sorted_df.head(top_n).index.tolist()
                elif step_type == "expression":
                    formula = step.get("formula", "")
                    alias_new = step.get("alias", "")
                    result = self._eval(formula, df)
                    if result is not None:
                        df[alias_new] = result
            except Exception:
                continue

        # 3. 生成权重
        final_weights = {}
        if self.dsl.weights.method == "equal":
            weight = 1.0 / len(selected) if selected else 1.0
            for s in selected:
                final_weights[s] = weight
        elif self.dsl.weights.method == "signal" and self.dsl.weights.signal_field:
            field = self.dsl.weights.signal_field
            if field in df.columns:
                total = df.loc[selected, field].clip(lower=0).sum()
                if total > 0:
                    for s in selected:
                        final_weights[s] = max(0.0, df.loc[s, field]) / total

        if not final_weights:
            return None

        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"dsl_{context.current_timestamp.isoformat()}",
            event_id=f"dsl_{context.current_timestamp.isoformat()}",
            signals=final_weights,
            strategy_id=self.strategy_id,
        )


class PandasToPolarsProvider:
    """将现有的 pandas-based AkshareDataProvider 适配为 Polars 输出"""

    def __init__(self, pandas_provider: Any):
        self._provider = pandas_provider

    def get_merged_panel_as_polars(
        self, symbols: list[str], start_date: str, end_date: str
    ) -> pl.DataFrame:
        df = self._provider.get_merged_panel(symbols, start_date, end_date)
        if df is None or df.empty:
            return pl.DataFrame()

        df = df.reset_index()
        df = df.rename(columns={"date": "timestamp"})

        required_cols = {"timestamp", "symbol", "close"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"数据缺少必要列: {missing}")

        return pl.from_pandas(df)


class BacktestServiceImpl(BacktestService):
    """回测服务实现（直接调用事件驱动引擎）

    特性：
    - 直接调用 EventDrivenBacktestEngine，零网络开销
    - 支持 YAML DSL 策略描述
    - 自动数据缓存（DuckDB）
    """

    def __init__(self, context: "RuntimeContext"):
        self.context = context
        self.logger = context.logger
        self.config = context.config

    def run(
        self,
        strategy_yaml: str,
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
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

        try:
            dsl = parse_strategy_yaml(strategy_yaml)
        except ValueError as e:
            return {
                "error": f"策略解析失败: {e}",
                "error_category": "client_error",
                "error_detail": str(e),
            }

        try:
            from long_earn.backtest.data.provider import DataProvider

            engine = EventDrivenBacktestEngine(
                cost_config=TradingCostConfig(
                    commission_rate=dsl.trading_cost.commission_rate,
                    stamp_duty=dsl.trading_cost.stamp_duty,
                    slippage_bps=dsl.trading_cost.slippage_bps,
                ),
                stop_loss=dsl.risk_control.stop_loss,
                max_drawdown_limit=dsl.risk_control.max_drawdown_limit,
                max_position_pct=dsl.risk_control.max_position_per_stock,
            )

            data_provider = getattr(self.context, "data_provider", None)
            if data_provider is not None and isinstance(data_provider, DataProvider):
                engine.data_provider = PandasToPolarsProvider(data_provider)

            strategy_obj = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)

            universe_symbols = ["000300"]  # 默认沪深300

            result = engine.run(
                strategy_obj,
                start_date,
                end_date,
                universe_symbols,
            )

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
                }

            return {
                "error": result.message,
                "error_category": result.error_category or "unknown",
                "error_detail": result.error_detail or "",
            }

        except Exception as e:
            if self.logger:
                self.logger.exception("回测执行异常")
            return {
                "error": str(e),
                "error_category": "engine_error",
                "error_detail": str(e),
            }

"""回测服务实现

对接事件驱动回测引擎，支持 YAML DSL 策略描述。
"""

from typing import TYPE_CHECKING, Any

import polars as pl

from long_earn.backtest.data.miniqmt_provider import MiniQmtUniverseProvider
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import (
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

        df = self._compute_factors(df)
        selected = self._execute_signal_steps(df)
        final_weights = self._compute_weights(df, selected)

        if not final_weights:
            return None

        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"dsl_{context.current_timestamp.isoformat()}",
            event_id=f"dsl_{context.current_timestamp.isoformat()}",
            signals=final_weights,
            strategy_id=self.strategy_id,
        )

    def _compute_factors(self, df: Any) -> Any:
        """计算 DSL 定义的因子并加入 DataFrame"""
        for alias, expr in self.dsl.factors.items():
            try:
                result = self._eval(expr, df)
                if result is not None:
                    df[alias] = result
            except Exception:
                continue
        return df

    def _execute_signal_steps(self, df: Any) -> list:
        """执行 DSL 信号步骤（filter/rank/expression），返回选中的标的列表"""
        selected = df.index.unique().tolist()
        for step in self.dsl.signals:
            step_type = step.get("type", "")
            try:
                if step_type == "filter":
                    selected = self._apply_filter_step(step, df)
                elif step_type == "rank":
                    selected = self._apply_rank_step(step, df)
                elif step_type == "expression":
                    self._apply_expression_step(step, df)
            except Exception:
                continue
        return selected

    def _apply_filter_step(self, step: dict, df: Any) -> list:
        """执行 filter 信号步骤"""
        condition = step.get("condition", "")
        result = self._eval(condition, df)
        df_filtered = df[result.fillna(False)]
        return df_filtered.index.unique().tolist()

    def _apply_rank_step(self, step: dict, df: Any) -> list:
        """执行 rank 信号步骤"""
        by_field = step.get("by", "")
        top_n = step.get("top", 10)
        ascending = step.get("ascending", False)
        if by_field in df.columns:
            sorted_df = df[by_field].dropna().sort_values(ascending=ascending)
            return sorted_df.head(top_n).index.tolist()
        return []

    def _apply_expression_step(self, step: dict, df: Any) -> None:
        """执行 expression 信号步骤，将结果列加入 DataFrame"""
        formula = step.get("formula", "")
        alias_new = step.get("alias", "")
        result = self._eval(formula, df)
        if result is not None:
            df[alias_new] = result

    def _compute_weights(self, df: Any, selected: list) -> dict[str, float]:
        """根据权重配置计算最终权重"""
        if self.dsl.weights.method == "equal":
            weight = 1.0 / len(selected) if selected else 1.0
            return dict.fromkeys(selected, weight)

        if self.dsl.weights.method == "signal" and self.dsl.weights.signal_field:
            field = self.dsl.weights.signal_field
            if field in df.columns:
                total = df.loc[selected, field].clip(lower=0).sum()
                if total > 0:
                    return {
                        s: max(0.0, df.loc[s, field]) / total for s in selected
                    }
        return {}


class PandasToPolarsProvider:
    """将 miniqmt pandas DataFrame 适配为 Polars 输出"""

    def __init__(self, pandas_provider: Any):
        self._provider = pandas_provider

    def get_merged_panel_as_polars(
        self, symbols: list[str], start_date: str, end_date: str
    ) -> pl.DataFrame:
        # 确保符号格式正确（添加 .SH/.SZ 后缀）
        formatted_symbols = self._format_symbols(symbols)

        # 获取合并的价格和财务数据面板
        df = self._provider.get_merged_panel(
            formatted_symbols,
            start_date,
            end_date,
            price_fields=["open", "high", "low", "close", "volume"],
            financial_fields=[
                "net_profit_yoy",
                "roe",
                "revenue_yoy",
                "gross_margin",
            ],
        )
        if df is None or df.empty:
            return pl.DataFrame()

        df = df.reset_index()
        # 确保 date 列存在并重命名为 timestamp
        if "date" in df.columns:
            df = df.rename(columns={"date": "timestamp"})

        required_cols = {"timestamp", "symbol", "close"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"数据缺少必要列: {missing}")

        return pl.from_pandas(df)

    @staticmethod
    def _format_symbols(symbols: list[str]) -> list[str]:
        """确保符号格式正确（000001 -> 000001.SZ, 600000 -> 600000.SH）"""
        formatted = []
        for s in symbols:
            if "." in s:
                formatted.append(s)
            elif s.startswith(("60", "68")):
                formatted.append(f"{s}.SH")
            elif s.startswith(("00", "30")):
                formatted.append(f"{s}.SZ")
            else:
                formatted.append(s)
        return formatted


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

            engine = EventDrivenBacktestEngine(
                cost_config=dsl.trading_cost.to_broker_config(),
                stop_loss=dsl.risk_control.stop_loss,
                max_drawdown_limit=dsl.risk_control.max_drawdown_limit,
                max_position_pct=dsl.risk_control.max_position_per_stock,
            )

            data_provider = getattr(self.context, "data_provider", None)
            if data_provider is not None and hasattr(data_provider, "get_merged_panel"):
                engine.data_provider = PandasToPolarsProvider(data_provider)

            strategy_obj = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)

            # 根据 DSL 配置获取股票池
            universe_type = dsl.universe.type or "csi300"
            start_date_str = start_date.replace("-", "")
            universe_provider = MiniQmtUniverseProvider()
            universe_symbols = universe_provider.get_symbols(universe_type, start_date_str)

            if not universe_symbols:
                return {
                    "error": f"股票池 '{universe_type}' 为空",
                    "error_category": "engine_error",
                    "error_detail": f"无法获取 {universe_type} 成分股",
                }

            # 格式化股票代码（添加 .SH/.SZ 后缀）
            formatted_symbols = PandasToPolarsProvider._format_symbols(universe_symbols)

            if self.logger:
                self.logger.info(f"股票池: {universe_type}, {len(formatted_symbols)} 只股票")

            result = engine.run(
                strategy_obj,
                start_date,
                end_date,
                formatted_symbols,
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

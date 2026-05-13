"""向量化回测引擎核心

基于 Pandas MultiIndex 的矩阵运算回测，支持 YAML DSL 策略。
"""

import logging
from typing import Any

import numpy as np
import pandas as pd

from long_earn.backtest.data.provider import AkshareDataProvider, get_data_provider
from long_earn.backtest.data.universe import AkshareUniverseProvider
from long_earn.backtest.engine.dsl import (
    StrategyDSL,
    parse_strategy_yaml,
    validate_fields,
)
from long_earn.backtest.engine.evaluator import (
    SafeExpressionError,
    SafeExpressionEvaluator,
)
from long_earn.backtest.models import BacktestResult

logger = logging.getLogger(__name__)

_MULTIINDEX_EXTRA_LEVEL = 2

# 内置可用字段（行情 + 财务）
AVAILABLE_FIELDS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "net_profit_yoy",
    "revenue_yoy",
    "roe",
    "gross_margin",
    "eps",
    "net_profit",
    "revenue",
]


class VectorizedBacktestEngine:
    """向量化回测引擎"""

    def __init__(
        self,
        data_provider: AkshareDataProvider | None = None,
        universe_provider: AkshareUniverseProvider | None = None,
    ):
        self.data_provider = data_provider or get_data_provider()
        self.universe_provider = universe_provider or AkshareUniverseProvider(
            getattr(self.data_provider, "cache", None)
        )

    def run(self, strategy: StrategyDSL) -> BacktestResult:
        """执行回测"""
        try:
            # 1. 校验字段
            missing = validate_fields(strategy, AVAILABLE_FIELDS)
            if missing:
                return BacktestResult(
                    success=False,
                    message=f"策略引用了不存在的字段: {missing}",
                    error_category="strategy_validation",
                    error_detail=f"可用字段: {AVAILABLE_FIELDS}，缺失字段: {missing}",
                )

            start_date = strategy.start_date or "2020-01-01"
            end_date = strategy.end_date or "2023-12-31"

            # 2. 获取股票池
            symbols = self.universe_provider.get_symbols(
                strategy.universe.type, start_date
            )
            if not symbols:
                return BacktestResult(
                    success=False,
                    message="股票池为空",
                    error_category="data_error",
                    error_detail=f"无法获取股票池: {strategy.universe.type}",
                )
            logger.info(f"股票池: {strategy.universe.type}, {len(symbols)} 只股票")

            # 3. 加载数据面板
            data = self._load_data(symbols, start_date, end_date, strategy)
            if data.empty:
                return BacktestResult(
                    success=False,
                    message="数据加载失败",
                    error_category="data_error",
                    error_detail="无法获取回测所需的行情或财务数据",
                )

            # 4. 计算因子
            factors = self._compute_factors(data, strategy.factors)

            # 5. 生成信号
            signals = self._generate_signals(factors, strategy.signals)

            # 6. 计算权重
            weights = self._calculate_weights(signals, strategy.weights)

            # 7. 风控处理
            weights = self._apply_risk_control(weights, strategy.risk_control)

            # 8. 模拟交易
            returns, positions = self._simulate_trades(data["close"], weights)

            # 9. 计算指标
            metrics = self._calculate_metrics(returns)

            # 10. 构建详细数据
            daily_returns = [
                {"date": str(d), "return": float(r)} for d, r in returns.items()
            ]
            positions_history = self._format_positions(positions)

            return BacktestResult(
                success=True,
                message="回测成功",
                total_return=metrics["total_return"],
                annual_return=metrics["annual_return"],
                sharpe_ratio=metrics["sharpe_ratio"],
                max_drawdown=metrics["max_drawdown"],
                win_rate=metrics["win_rate"],
                trading_days=metrics["trading_days"],
                volatility=metrics["volatility"],
                calmar_ratio=metrics["calmar_ratio"],
                sortino_ratio=metrics["sortino_ratio"],
                daily_returns=daily_returns,
                positions_history=positions_history,
            )

        except Exception as e:
            logger.exception("回测执行失败")
            return BacktestResult(
                success=False,
                message="回测引擎内部错误",
                error_category="engine_error",
                error_detail=str(e),
            )

    def _load_data(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        _strategy: StrategyDSL,
    ) -> pd.DataFrame:
        """加载合并的数据面板"""
        price_fields = ["close", "volume"]
        fin_fields = []

        for field in AVAILABLE_FIELDS:
            if field in ["open", "high", "low", "close", "volume"]:
                if field not in price_fields:
                    price_fields.append(field)
            else:
                fin_fields.append(field)

        data = self.data_provider.get_merged_panel(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            price_fields=price_fields,
            financial_fields=fin_fields,
        )
        logger.info(
            f"数据面板: {len(data)} 条记录, "
            f"{data.index.get_level_values('symbol').nunique()} 只股票"
        )
        return data

    def _compute_factors(
        self, data: pd.DataFrame, factor_defs: dict[str, str]
    ) -> pd.DataFrame:
        """计算自定义因子"""
        df = data.copy()
        for alias, expr in factor_defs.items():
            try:
                df[alias] = self._eval_expression(df, expr)
                logger.debug(f"因子计算: {alias} = {expr}")
            except Exception as e:
                logger.warning(f"因子 {alias} 计算失败: {e}")
                df[alias] = np.nan
        return df

    def _generate_signals(
        self, data: pd.DataFrame, signal_steps: list[dict[str, Any]]
    ) -> pd.DataFrame:
        """生成交易信号"""
        df = data.copy()
        mask = pd.Series(True, index=df.index)

        for step in signal_steps:
            step_type = step.get("type", "")

            if step_type == "filter":
                condition = step.get("condition", "")
                try:
                    result = self._eval_expression(df, condition)
                    if isinstance(result, pd.Series):
                        mask = mask & result.fillna(False)
                    logger.debug(f"信号过滤: {condition}, 剩余 {mask.sum()} 条")
                except Exception as e:
                    logger.warning(f"过滤条件执行失败: {condition}, 错误: {e}")

            elif step_type == "rank":
                by_field = step.get("by", "")
                ascending = step.get("ascending", False)
                top_n = step.get("top", 10)
                try:
                    df = self._rank_within_group(df, by_field, ascending, top_n, mask)
                    mask = pd.Series(True, index=df.index)
                except Exception as e:
                    logger.warning(f"排序失败: {e}")

            elif step_type == "expression":
                alias = step.get("alias", "")
                formula = step.get("formula", "")
                try:
                    df[alias] = self._eval_expression(df, formula)
                except Exception as e:
                    logger.warning(f"表达式计算失败: {formula}, 错误: {e}")
                    df[alias] = np.nan

        df = df[mask]
        return df

    def _calculate_weights(
        self, signals: pd.DataFrame, weight_config: Any
    ) -> pd.DataFrame:
        """计算持仓权重"""
        method = weight_config.method

        if method == "equal":
            return self._equal_weights(signals)
        elif method == "signal":
            field = weight_config.signal_field or "signal"
            return self._signal_weights(signals, field)
        elif method == "custom_formula":
            formula = weight_config.formula or "1.0"
            return self._formula_weights(signals, formula)
        else:
            logger.warning(f"未知的权重方法: {method}，使用等权重")
            return self._equal_weights(signals)

    def _equal_weights(self, signals: pd.DataFrame) -> pd.DataFrame:
        """等权重（向量化实现）"""
        df = signals.copy()
        # 使用 transform 避免 apply 返回 DataFrame 的问题
        counts = df.groupby(level="date").size()
        date_idx = df.index.get_level_values("date")
        weights = 1.0 / counts.reindex(date_idx).values
        weights = np.where(np.isfinite(weights), weights, 0.0)
        df["weight"] = weights
        return df

    def _signal_weights(self, signals: pd.DataFrame, field: str) -> pd.DataFrame:
        """基于信号值加权（向量化实现）"""
        df = signals.copy()
        vals = df[field].fillna(0.0)
        # 使用 transform 归一化
        totals = vals.groupby(level="date").transform(lambda x: x.abs().sum())
        weights = np.where(totals > 0, vals / totals, 0.0)
        df["weight"] = weights
        return df

    def _formula_weights(self, signals: pd.DataFrame, formula: str) -> pd.DataFrame:
        """基于公式加权（向量化实现）"""
        df = signals.copy()
        try:
            raw_weights = self._eval_expression(df, formula)
            if isinstance(raw_weights, pd.Series):
                totals = raw_weights.abs().groupby(level="date").transform("sum")
                weights = np.where(totals > 0, raw_weights / totals, 0.0)
                df["weight"] = weights
            else:
                df["weight"] = 1.0 / len(df) if len(df) > 0 else 0.0
        except Exception as e:
            logger.warning(f"公式权重计算失败: {e}")
            df["weight"] = 1.0 / len(df) if len(df) > 0 else 0.0
        return df

    def _apply_risk_control(
        self, weights_df: pd.DataFrame, risk_config: Any
    ) -> pd.DataFrame:
        """应用风控规则"""
        df = weights_df.copy()
        if hasattr(risk_config, "max_position_per_stock"):
            max_pos = risk_config.max_position_per_stock
            df["weight"] = df["weight"].clip(upper=max_pos)
        return df

    def _simulate_trades(
        self,
        close_prices: pd.Series,
        weights_df: pd.DataFrame,
    ) -> tuple[pd.Series, pd.DataFrame]:
        """模拟交易并计算组合收益"""
        dates = close_prices.index.get_level_values("date").unique().sort_values()
        symbols = close_prices.index.get_level_values("symbol").unique()

        # 创建权重面板 (date × symbol)
        weight_panel = pd.DataFrame(0.0, index=dates, columns=symbols)
        for (date, symbol), row in weights_df.iterrows():
            if date in weight_panel.index and symbol in weight_panel.columns:
                weight_panel.loc[date, symbol] = row.get("weight", 0.0)

        # 创建收盘价面板 (date × symbol)
        close_panel = close_prices.unstack(level="symbol")
        # 对齐日期
        common_dates = weight_panel.index.intersection(close_panel.index)
        weight_panel = weight_panel.loc[common_dates]
        close_panel = close_panel.loc[common_dates]

        # 计算每日收益率
        daily_returns_panel = close_panel.pct_change()

        # 组合收益 = 昨日权重 × 今日收益率（T+1 执行）
        shifted_weights = weight_panel.shift(1)
        portfolio_returns = (shifted_weights * daily_returns_panel).sum(axis=1)
        portfolio_returns = portfolio_returns.fillna(0.0)

        # 持仓面板
        positions = shifted_weights.fillna(0.0)

        return portfolio_returns, positions

    def _calculate_metrics(self, returns: pd.Series) -> dict[str, float]:
        """计算回测绩效指标"""
        returns = returns.dropna()
        if len(returns) == 0:
            return {
                "total_return": 0.0,
                "annual_return": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "trading_days": 0,
                "volatility": 0.0,
                "calmar_ratio": 0.0,
                "sortino_ratio": 0.0,
            }

        total_return = (1 + returns).cumprod().iloc[-1] - 1

        n_years = len(returns) / 252
        annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0.0

        volatility = returns.std() * np.sqrt(252)

        risk_free_rate = 0.03
        sharpe = (
            (returns.mean() * 252 - risk_free_rate) / volatility
            if volatility > 0
            else 0.0
        )

        cumulative = (1 + returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (peak - cumulative) / peak
        max_drawdown = drawdown.max()

        win_rate = (returns > 0).sum() / len(returns) if len(returns) > 0 else 0.0

        calmar = annual_return / max_drawdown if max_drawdown > 0 else 0.0

        downside_returns = returns[returns < 0]
        downside_std = (
            downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else 0.0
        )
        sortino = (
            (returns.mean() * 252 - risk_free_rate) / downside_std
            if downside_std > 0
            else 0.0
        )

        return {
            "total_return": float(total_return),
            "annual_return": float(annual_return),
            "sharpe_ratio": float(sharpe),
            "max_drawdown": float(max_drawdown),
            "win_rate": float(win_rate),
            "trading_days": len(returns),
            "volatility": float(volatility),
            "calmar_ratio": float(calmar),
            "sortino_ratio": float(sortino),
        }

    def _eval_expression(self, df: pd.DataFrame, expr: str) -> pd.Series:
        """安全地评估表达式（AST 白名单求值，无 eval）"""
        evaluator = SafeExpressionEvaluator(df)
        try:
            return evaluator.evaluate(expr)
        except SafeExpressionError:
            raise
        except Exception as e:
            raise ValueError(f"表达式执行失败: {expr}, 错误: {e}") from e

    def _rank_within_group(
        self,
        df: pd.DataFrame,
        by_field: str,
        ascending: bool,
        top_n: int,
        mask: pd.Series,
    ) -> pd.DataFrame:
        """在每组内排序并选取前 N"""
        df = df[mask].copy()
        if by_field not in df.columns:
            logger.warning(f"排序字段不存在: {by_field}")
            return df

        def select_top(group):
            sorted_group = group.sort_values(by_field, ascending=ascending)
            return sorted_group.head(top_n)

        result = df.groupby(level="date").apply(select_top)
        if (
            isinstance(result.index, pd.MultiIndex)
            and len(result.index.levels) > _MULTIINDEX_EXTRA_LEVEL
        ):
            result = result.droplevel(0)
        return result

    def _format_positions(self, positions: pd.DataFrame) -> list[dict[str, Any]]:
        """格式化持仓历史为列表"""
        records = []
        for date, row in positions.iterrows():
            holdings = {k: float(v) for k, v in row.items() if v > 0}
            if holdings:
                records.append({"date": str(date), "holdings": holdings})
        return records


def run_backtest(strategy_yaml: str) -> BacktestResult:
    """便捷函数：直接运行 YAML 策略回测

    可以被 LangGraph 节点直接调用
    """

    try:
        strategy = parse_strategy_yaml(strategy_yaml)
    except ValueError as e:
        return BacktestResult(
            success=False,
            message="策略解析失败",
            error_category="dsl_error",
            error_detail=str(e),
        )

    engine = VectorizedBacktestEngine()
    return engine.run(strategy)

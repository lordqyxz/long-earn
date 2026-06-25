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
from long_earn.services import BacktestService, LoggerService

if TYPE_CHECKING:
    from long_earn.backtest.data.provider import DataProvider
    from long_earn.config import AppConfig


class DSLStrategy(BaseStrategy):
    """从 YAML DSL 自动生成的状态化策略"""

    def __init__(self, strategy_id: str, dsl_strategy: Any, config: dict | None = None):
        super().__init__(strategy_id, config)
        self.dsl = dsl_strategy
        # 静默吞异常的诊断窗口：上层可读取这两个列表判断策略是否真在工作，
        # 还是只是退化成"什么都不做"而被错误标记 success=True。
        self.factor_failures: list[dict[str, str]] = []
        self.step_failures: list[dict[str, str]] = []

    def _eval(self, expr: str, df) -> Any:
        """使用 SafeExpressionEvaluator 安全求值"""
        evaluator = SafeExpressionEvaluator(df)
        return evaluator.evaluate(expr)

    def _build_operator_executor(self):
        """惰性构造算子目录执行器（仅当 DSL 含算子步骤时）。

        把算子目录接入策略执行路径：算子因子/信号步骤经此执行器跑在算子目录上，
        绕过旧表达式求值器。解析期已校验过 op/params，这里直接 resolve。
        """
        from long_earn.backtest.engine.operator_executor import (  # noqa: PLC0415
            OperatorStrategyExecutor,
            resolve_factor_step,
            resolve_signal_step,
        )

        factor_specs = [resolve_factor_step(s) for s in self.dsl.operator_factors]
        signal_specs = [
            resolve_signal_step(s)
            for s in self.dsl.signals
            if s.get("type") == "operator"
        ]
        return OperatorStrategyExecutor(factor_specs, signal_specs)

    def _on_bar_operators(self, bars: pl.DataFrame, context) -> Any:  # noqa: ARG002
        """算子目录执行路径：在 polars 历史面板上跑算子链 → 选中标的 → 等权信号。

        与旧路径的区别：因子/信号计算全部走算子目录（polars），无表达式求值、
        无 pandas 转换。因果性由算子目录（每个算子过 prove_causality）+
        VisibilityGuard（history 仅含 timestamp <= 当前时刻）共同保证。
        ``bars`` 参数为 BaseStrategy.on_bar 契约要求，算子路径改用 history 面板。
        """
        from long_earn.backtest.domain.entities import SignalEvent  # noqa: PLC0415

        if not hasattr(self, "_op_executor"):
            self._op_executor = self._build_operator_executor()

        try:
            history_pl = context.get_history_df()
        except Exception as exc:
            self.step_failures.append({
                "type": "history_fetch",
                "step": "on_bar_operators history",
                "error": f"{type(exc).__name__}: {exc}",
            })
            return None

        try:
            selected = self._op_executor.execute(
                history_pl, context.current_timestamp
            )
        except Exception as exc:
            self.step_failures.append({
                "type": "operator_execute",
                "step": "operator_executor",
                "error": f"{type(exc).__name__}: {exc}",
            })
            return None

        final_weights = self._equal_weights(selected)
        if not final_weights:
            return None

        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"op_{context.current_timestamp.isoformat()}",
            event_id=f"op_{context.current_timestamp.isoformat()}",
            signals=final_weights,
            strategy_id=self.strategy_id,
        )

    def on_bar(self, bars: pl.DataFrame, context) -> Any:
        from long_earn.backtest.domain.entities import SignalEvent  # noqa: PLC0415

        # 算子目录路径：DSL 含 operator_factors / operator 信号时，走算子执行器，
        # 在 polars 历史面板上直接跑算子目录（因果性由算子目录 + VisibilityGuard 保证）。
        # 这条路径绕过旧 SafeExpressionEvaluator，是"调整系统架构"后的主执行路径。
        # getattr 兜底：兼容不含 has_operator_steps 的旧 stub DSL（测试用）。
        if getattr(self.dsl, "has_operator_steps", lambda: False)():
            return self._on_bar_operators(bars, context)

        # 因子计算必须基于历史窗口而非当前截面：否则 shift(close, N) 这类
        # 时序因子在每 symbol 单行的 slab 上永远是 NaN，所有动量/反转/波动率
        # 因子全部失效——这是 LLM 生成的量化策略最常见的因子类型。
        # context.get_history_df() 由 VisibilityGuard 保证只含 timestamp <= 当前时刻。
        try:
            history_pl = context.get_history_df()
            history_df = history_pl.to_pandas()
            # 因子层：MultiIndex (timestamp, symbol)，shift(level="symbol") 才能正确按
            # 每只股票的时间序列做位移
            if "timestamp" in history_df.columns and "symbol" in history_df.columns:
                history_df = history_df.sort_values(
                    ["symbol", "timestamp"]
                ).set_index(["timestamp", "symbol"])
            factors_df = self._compute_factors(history_df)
            # 取当前时刻的截面快照（信号和权重在 symbol 单层 index 上工作）
            ct = context.current_timestamp
            if ct in factors_df.index.get_level_values(0):
                df = factors_df.xs(ct, level="timestamp")
            else:
                # 兜底：若历史 df 没有当前 ts（数据缺失等），退回到 bars 截面
                df = bars.to_pandas()
                if "symbol" not in df.index.names:
                    df = df.set_index("symbol")
        except Exception as exc:
            # 历史数据获取失败时回退旧路径，但记录到 step_failures 便于上层观测
            self.step_failures.append({
                "type": "history_fetch",
                "step": "on_bar history",
                "error": f"{type(exc).__name__}: {exc}",
            })
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
        """计算 DSL 定义的因子并加入 DataFrame

        失败的因子写入 self.factor_failures，便于上层判断策略是否真的在工作。
        """
        for alias, expr in self.dsl.factors.items():
            try:
                result = self._eval(expr, df)
                if result is not None:
                    df[alias] = result
            except Exception as exc:
                # 不要让一个坏因子直接挂掉整张图，但要让失败可观测
                self.factor_failures.append(
                    {"alias": alias, "expr": str(expr), "error": str(exc)}
                )
                continue
        return df

    def _execute_signal_steps(self, df: Any) -> list:
        """执行 DSL 信号步骤（filter/rank/expression），返回选中的标的列表

        失败的 step 写入 self.step_failures，便于上层判断策略是否真的在工作。
        """
        selected = df.index.unique().tolist()
        for idx, step in enumerate(self.dsl.signals):
            step_type = step.get("type", "")
            try:
                if step_type == "filter":
                    selected = self._apply_filter_step(step, df)
                elif step_type == "rank":
                    selected = self._apply_rank_step(step, df)
                elif step_type == "expression":
                    self._apply_expression_step(step, df)
            except Exception as exc:
                self.step_failures.append(
                    {
                        "index": str(idx),
                        "type": step_type,
                        "step": str(step),
                        "error": str(exc),
                    }
                )
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
        """根据权重配置计算最终权重

        所有"返回空 {}"的退化路径都必须写入 step_failures，
        让上层（reflection / supervisor）知道是策略层退化而非真业绩 0。
        """
        method = self.dsl.weights.method
        if method == "equal":
            return self._equal_weights(selected)
        if method == "signal":
            return self._signal_weights(df, selected)
        # 未知 method：LLM 写错了配置
        self.step_failures.append({
            "type": "weights",
            "step": f"method={method}",
            "error": f"未知 weights.method '{method}'，仅支持 equal/signal",
        })
        return {}

    def _equal_weights(self, selected: list) -> dict[str, float]:
        if not selected:
            self.step_failures.append({
                "type": "weights",
                "step": "method=equal",
                "error": "selected 为空：信号步骤未选出任何标的",
            })
            return {}
        weight = 1.0 / len(selected)
        return dict.fromkeys(selected, weight)

    def _signal_weights(self, df: Any, selected: list) -> dict[str, float]:
        if not self.dsl.weights.signal_field:
            self.step_failures.append({
                "type": "weights",
                "step": "method=signal",
                "error": "signal_field 未配置",
            })
            return {}
        field = self.dsl.weights.signal_field
        step_label = f"method=signal,field={field}"
        if field not in df.columns:
            self.step_failures.append({
                "type": "weights",
                "step": step_label,
                "error": f"signal_field '{field}' 不在 DataFrame 列中",
            })
            return {}
        if not selected:
            self.step_failures.append({
                "type": "weights",
                "step": step_label,
                "error": "selected 为空：信号步骤未选出任何标的",
            })
            return {}
        total = df.loc[selected, field].clip(lower=0).sum()
        if total <= 0:
            self.step_failures.append({
                "type": "weights",
                "step": step_label,
                "error": "signal_field 在 selected 上的正部和为 0，无法分配权重",
            })
            return {}
        return {s: max(0.0, df.loc[s, field]) / total for s in selected}


class PandasToPolarsProvider:
    """将 miniqmt pandas DataFrame 适配为 Polars 输出"""

    def __init__(self, pandas_provider: Any):
        self._provider = pandas_provider

    def get_merged_panel_as_polars(
        self, symbols: list[str], start_date: str, end_date: str
    ) -> pl.DataFrame:
        # 获取合并的价格和财务数据面板
        # 注意：symbols 已由调用方格式化（含 .SH/.SZ 后缀），无需重复格式化
        df = self._provider.get_merged_panel(
            symbols,
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

    def __init__(
        self,
        config: "AppConfig",
        logger: LoggerService,
        data_provider: "DataProvider | None" = None,
    ):
        self.config = config
        self.logger = logger
        self.data_provider = data_provider


    def _build_strategy_diagnostics(
        self,
        strategy_obj: "DSLStrategy",
        dsl: Any,
        result: Any,
    ) -> dict[str, Any]:
        """收集策略层静默失败信息

        让上层（reflection / supervisor）能识别"策略实际上几乎啥都没干，
        业绩 0 是退化结果而非真实表现"。

        关键：factor_failures / step_failures 跨 bar 累积——每个 bar 都会重新跑
        一遍因子和信号步骤。直接用 len(step_failures) == total_steps 判断"全失败"
        在多 bar 下永远 False（1000 bar × 6 step = 6000 ≠ 6）。
        正确做法：按 step index / factor alias 去重，看"是否每个 step 至少失败过一次"。
        """
        factor_failures = list(strategy_obj.factor_failures)
        step_failures = list(strategy_obj.step_failures)
        trade_count = result.trade_count or 0

        total_factors = len(getattr(dsl, "factors", {}) or {})
        total_steps = len(getattr(dsl, "signals", []) or [])

        # 去重：哪些 alias / index 至少失败过一次
        failed_factor_aliases: set[str] = {
            alias for f in factor_failures if (alias := f.get("alias"))
        }
        failed_step_indices: set[str] = {
            idx for f in step_failures if (idx := f.get("index")) is not None
        }

        all_factors_failed = (
            total_factors > 0 and len(failed_factor_aliases) >= total_factors
        )
        all_steps_failed = (
            total_steps > 0 and len(failed_step_indices) >= total_steps
        )
        degenerate = all_factors_failed or all_steps_failed or trade_count == 0

        if self.logger and degenerate:
            self.logger.warning(
                f"策略疑似退化：trade_count={trade_count}, "
                f"factor_failures={len(failed_factor_aliases)}/{total_factors} "
                f"unique（共 {len(factor_failures)} 次）, "
                f"step_failures={len(failed_step_indices)}/{total_steps} "
                f"unique（共 {len(step_failures)} 次）"
            )

        return {
            "factor_failures": factor_failures,
            "step_failures": step_failures,
            "failed_factor_aliases": sorted(failed_factor_aliases),
            "failed_step_indices": sorted(failed_step_indices),
            "trade_count": trade_count,
            "degenerate": degenerate,
        }

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

            data_provider = self.data_provider
            if data_provider is not None and hasattr(data_provider, "get_merged_panel"):
                engine.data_provider = PandasToPolarsProvider(data_provider)

            strategy_obj = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)

            # 根据 DSL 配置获取股票池
            universe_type = dsl.universe.type or "csi300"
            start_date_str = start_date.replace("-", "")
            universe_provider = MiniQmtUniverseProvider()
            universe_symbols = universe_provider.get_symbols(universe_type, start_date_str)

            # 降级：如果指定股票池为空，尝试 csi300
            if not universe_symbols and universe_type != "csi300":
                if self.logger:
                    self.logger.warning(
                        f"股票池 '{universe_type}' 为空，降级到 csi300"
                    )
                universe_type = "csi300"
                universe_symbols = universe_provider.get_symbols("csi300", start_date_str)

            if not universe_symbols:
                return {
                    "error": f"股票池 '{universe_type}' 为空，数据源不可用",
                    "error_category": "engine_error",
                    "error_detail": f"无法获取 {universe_type} 成分股，请检查数据源",
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

            strategy_diagnostics = self._build_strategy_diagnostics(
                strategy_obj, dsl, result
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
                    "strategy_diagnostics": strategy_diagnostics,
                }

            return {
                "error": result.message,
                "error_category": result.error_category or "unknown",
                "error_detail": result.error_detail or "",
                "strategy_diagnostics": strategy_diagnostics,
            }

        except Exception as e:
            if self.logger:
                self.logger.exception("回测执行异常")
            return {
                "error": str(e),
                "error_category": "engine_error",
                "error_detail": str(e),
            }

    def run_grid(  # noqa: PLR0913
        self,
        strategy_template: str,
        param_grid: Any,
        start_date: str = "",
        end_date: str = "",
        universe_type: str = "csi300",
        benchmark_symbol: str = "",
        allow_large_grid: bool = False,
    ) -> dict[str, Any]:
        """参数网格并行回测。"""
        from long_earn.backtest.engine.parallel import ParallelRunner  # noqa: PLC0415

        start_date = start_date or self.config.backtest_start_date
        end_date = end_date or self.config.backtest_end_date

        universe_provider = MiniQmtUniverseProvider()
        symbols = universe_provider.get_symbols(
            universe_type, end_date.replace("-", "")
        )
        formatted_symbols = PandasToPolarsProvider._format_symbols(symbols)

        if self.logger:
            self.logger.info(
                f"[grid] 股票池: {universe_type}, {len(formatted_symbols)} 只"
            )

        runner = ParallelRunner()
        result = runner.run_grid(
            strategy_template=strategy_template,
            param_grid=param_grid,
            start_date=start_date,
            end_date=end_date,
            symbols=formatted_symbols,
            benchmark_symbol=benchmark_symbol,
            allow_large_grid=allow_large_grid,
        )

        return {
            "total": len(result.outcomes),
            "success_count": result.success_count,
            "failure_count": result.failure_count,
            "best_sharpe": result.best.sharpe_ratio if result.best else None,
            "best_return": result.best_by_return.total_return
            if result.best_by_return
            else None,
            "best_param_desc": result.best.param_desc if result.best else "",
            "outcomes": [
                {
                    "task_id": o.task_id,
                    "success": o.success,
                    "total_return": o.total_return,
                    "sharpe_ratio": o.sharpe_ratio,
                    "max_drawdown": o.max_drawdown,
                    "error": o.error,
                    "param_desc": o.param_desc,
                }
                for o in result.outcomes
            ],
        }

    def run_walk_forward_parallel(  # noqa: PLR0913
        self,
        strategy_yaml: str,
        start_date: str = "",
        end_date: str = "",
        n_splits: int = 3,
        universe_type: str = "csi300",
        benchmark_symbol: str = "",
    ) -> dict[str, Any]:
        """Walk-Forward 并行回测。"""
        from long_earn.backtest.engine.parallel import ParallelRunner  # noqa: PLC0415

        start_date = start_date or self.config.backtest_start_date
        end_date = end_date or self.config.backtest_end_date

        universe_provider = MiniQmtUniverseProvider()
        symbols = universe_provider.get_symbols(
            universe_type, end_date.replace("-", "")
        )
        formatted_symbols = PandasToPolarsProvider._format_symbols(symbols)

        if self.logger:
            self.logger.info(
                f"[walk_forward_parallel] 股票池: {universe_type}, "
                f"{len(formatted_symbols)} 只, n_splits={n_splits}"
            )

        runner = ParallelRunner()
        result = runner.run_walk_forward_parallel(
            strategy_yaml=strategy_yaml,
            start_date=start_date,
            end_date=end_date,
            symbols=formatted_symbols,
            n_splits=n_splits,
            benchmark_symbol=benchmark_symbol,
        )

        return result

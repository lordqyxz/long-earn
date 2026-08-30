"""回测服务实现

对接事件驱动回测引擎，支持 YAML DSL 策略描述。
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from long_earn.backtest import ParamGrid
from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.miniqmt_provider import (
    COMPOSITE_BOARD_MAP,
    INDEX_SECTOR_MAP,
    MiniQmtUniverseProvider,
)
from long_earn.backtest.data.polars_adapter import PandasToPolarsProvider
from long_earn.backtest.engine.audit import PostgresAuditProvider
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import (
    compute_warmup_days,
    parse_strategy_yaml,
)
from long_earn.backtest.engine.dsl_strategy import DSLStrategy
from long_earn.services import BacktestService, LoggerService

# PIT 快照日期偏差容忍天数（超过此值触发幸存者偏差警告）
_UNIVERSE_PIT_MAX_DAYS = 30

if TYPE_CHECKING:
    from long_earn.backtest.data.connector import DataConnector
    from long_earn.config import AppConfig


class BacktestServiceImpl(BacktestService):
    """回测服务实现（直接调用事件驱动引擎）

    特性：
    - 直接调用 EventDrivenBacktestEngine，零网络开销
    - 支持 YAML DSL 策略描述
    - 自动数据缓存（PostgreSQL）
    """

    def __init__(
        self,
        config: "AppConfig",
        logger: LoggerService,
        data_provider: "DataConnector | None" = None,
        max_workers: int = 0,
    ):
        self.config = config
        self.logger = logger
        self.data_provider = data_provider
        # 并行 worker 数：0=自动（os.cpu_count()），1=串行，>1=指定核数
        # 控制 Walk-Forward fold 级并行（run_oos / run_walk_forward_parallel）
        self.max_workers = max_workers or getattr(config, "max_workers", 0)
        self._owned_cache: DataCache | None = None

    def _resolve_cache(self) -> DataCache:
        """复用 context 注入的 DataCache，仅在无注入时惰性创建兜底实例。"""
        if self.data_provider is not None:
            cache = getattr(self.data_provider, "cache", None)
            if cache is not None:
                return cache
        if self._owned_cache is None:
            self._owned_cache = DataCache()
        return self._owned_cache

    def close(self) -> None:
        """关闭本服务创建的兜底 DataCache（注入的 context 缓存不归本服务管理）。"""
        if self._owned_cache is not None:
            self._owned_cache.close()
            self._owned_cache = None

    def _build_strategy_diagnostics(
        self,
        strategy_obj: "DSLStrategy",
        dsl: Any,
        result: Any,
    ) -> dict[str, Any]:
        """收集策略层静默失败信息

        让上层（reflection / supervisor）能识别"策略实际上几乎啥都没干，
        业绩 0 是退化结果而非真实表现"，或"算子执行链失败导致回测指标不可信"。

        ADR-009 收尾：算子路径的 step_failures 用 ``step`` 字段标记失败位置
        （如 ``operator_executor`` / ``on_bar history`` / ``method=equal``），
        跨 bar 累积——每个 bar 都会重新跑算子链。按 step 标签去重，
        看是否曾发生失败（任何一次失败都意味着选股逻辑可能残缺）。
        """
        factor_failures = list(strategy_obj.factor_failures)
        step_failures = list(strategy_obj.step_failures)
        trade_count = result.trade_count or 0
        # 信号步骤总数（仅用于 diagnostics 展示，不参与失败判定）
        total_signals = len(getattr(dsl, "signals", []) or [])

        # 去重：哪些 step 标签 / factor alias 至少失败过一次
        failed_factor_aliases: set[str] = {
            alias for f in factor_failures if (alias := f.get("alias"))
        }
        failed_step_labels: set[str] = {
            label for f in step_failures if (label := f.get("step"))
        }

        # 任何算子执行 step 失败都意味着选股逻辑残缺，回测指标不可信
        any_step_failed = len(failed_step_labels) > 0
        any_factor_failed = len(failed_factor_aliases) > 0
        # degenerate: 策略层退化（无交易 = 啥都没干）；any_step_failed 进一步标记不可信
        degenerate = trade_count == 0
        # metrics_unreliable: 策略层退化/算子失败，或引擎层撮合异常（skip/部分成交）
        engine_unreliable = bool(getattr(result, "metrics_unreliable", False))
        strategy_unreliable = degenerate or any_step_failed or any_factor_failed
        metrics_unreliable = strategy_unreliable or engine_unreliable

        if self.logger and metrics_unreliable:
            engine_note = (
                f", engine_unreliable={engine_unreliable}" if engine_unreliable else ""
            )
            self.logger.warning(
                f"策略指标不可信：trade_count={trade_count}, "
                f"factor_failures={len(failed_factor_aliases)} "
                f"unique（共 {len(factor_failures)} 次）, "
                f"step_failures={len(failed_step_labels)} "
                f"unique（共 {len(step_failures)} 次）, "
                f"degenerate={degenerate}, metrics_unreliable={metrics_unreliable}"
                f"{engine_note}"
            )

        return {
            "factor_failures": factor_failures,
            "step_failures": step_failures,
            "failed_factor_aliases": sorted(failed_factor_aliases),
            "failed_step_labels": sorted(failed_step_labels),
            "total_signals": total_signals,
            "trade_count": trade_count,
            "degenerate": degenerate,
            "engine_metrics_unreliable": engine_unreliable,
            "metrics_unreliable": metrics_unreliable,
        }

    def _create_audit_provider(self) -> Any:
        """创建 PostgreSQL 审计提供者，失败时返回 None（不阻断回测）

        审计为旁路：回测把交易日志（时间/标的/价格/数量/金额）持久化到
        PostgreSQL（backtest_audit.logs），供后续导出与可视化消费。
        初始化失败仅告警，不影响策略计算。
        """
        try:
            return PostgresAuditProvider()
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"审计存储初始化失败，回测将不写 PG: {exc}")
            return None

    def _get_universe_symbols(self, universe_type: str, date: str) -> list[str]:
        """获取股票池（优先走 data_connector 降级链，退回 MiniQmtUniverseProvider）。

        将 universe 获取纳入 DI 容器管理：注入了 ``data_provider``（如
        ``CompositeDataConnector``）时走降级链（DuckDB→miniqmt），
        xtquant 不可用时不再断链；未注入时退回直接构造 ``MiniQmtUniverseProvider``。
        """
        if self.data_provider is not None:
            try:
                fn = getattr(self.data_provider, "get_symbols", None)
                if fn is not None:
                    symbols = list(fn(universe_type, date) or [])
                    if symbols:
                        return symbols
            except Exception as exc:
                if self.logger:
                    self.logger.warning(
                        f"data_provider.get_symbols 失败，退回 MiniQmtUniverseProvider: {exc}"
                    )
        return MiniQmtUniverseProvider().get_symbols(universe_type, date)

    def _check_universe_pit(self, universe_type: str, start_date: str) -> bool:
        """检查股票池快照是否 PIT 对齐。

        返回 True 表示存在幸存者偏差风险（快照日期与回测起始日期偏差 > 30 天）。
        """
        try:
            cache = self._resolve_cache()
            # 复合板类型映射到第一个子板
            if universe_type in COMPOSITE_BOARD_MAP:
                index_code = COMPOSITE_BOARD_MAP[universe_type][0]
            elif universe_type in INDEX_SECTOR_MAP:
                index_code = universe_type
            else:
                index_code = universe_type

            snapshot_date = cache.get_universe_snapshot_date(index_code, start_date)
            if snapshot_date is None:
                return True  # 无历史快照 → 警告

            # 快照日期与回测起始日期偏差 > 阈值 → 警告
            sd = datetime.strptime(snapshot_date, "%Y-%m-%d")
            td = datetime.strptime(start_date, "%Y%m%d")
            return abs((td - sd).days) > _UNIVERSE_PIT_MAX_DAYS
        except Exception:
            return True  # 检查失败 → 保守警告

    def run(  # noqa: PLR0912
        self,
        strategy_yaml: str,
        start_date: str = "",
        end_date: str = "",
        tags: list[str] | None = None,
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
            # 注入 DuckDB 审计提供者：回测执行时把交易日志（FILL/ORDER/SIGNAL/
            # MARKET_DATA 事件，含时间、标的、价格、数量、金额、持仓市值等）
            # 持久化到 DuckDB（backtest_audit.logs），供后续导出与可视化消费。
            # 失败不阻断回测（审计为旁路，不参与策略计算）。
            audit_provider = self._create_audit_provider()

            engine = EventDrivenBacktestEngine(
                cost_config=dsl.trading_cost.to_broker_config(),
                stop_loss=dsl.risk_control.stop_loss,
                max_drawdown_limit=dsl.risk_control.max_drawdown_limit,
                max_position_pct=dsl.risk_control.max_position_per_stock,
                audit_provider=audit_provider,
            )

            data_provider = self.data_provider
            if data_provider is not None:
                # DataConnector Protocol 已定义 get_merged_panel_as_polars；
                # 直接注入让引擎经统一接口消费（CompositeDataConnector 等已实现）。
                engine.data_provider = data_provider

            strategy_obj = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)

            # 根据 DSL 配置获取股票池（优先走 data_provider 降级链）
            # 默认 main_board+gem（沪深除科创板所有标的），与 DSL 默认值保持一致
            universe_type = dsl.universe.type or "main_board+gem"
            start_date_str = start_date.replace("-", "")
            universe_symbols = self._get_universe_symbols(universe_type, start_date_str)

            # 降级：如果指定股票池为空，尝试 main_board+gem（系统默认池）
            default_universe = "main_board+gem"
            if not universe_symbols and universe_type != default_universe:
                if self.logger:
                    self.logger.warning(
                        f"股票池 '{universe_type}' 为空，降级到 {default_universe}"
                    )
                universe_type = default_universe
                universe_symbols = self._get_universe_symbols(
                    default_universe, start_date_str
                )

            if not universe_symbols:
                return {
                    "error": f"股票池 '{universe_type}' 为空，数据源不可用",
                    "error_category": "engine_error",
                    "error_detail": f"无法获取 {universe_type} 成分股，请检查数据源",
                }

            # 格式化股票代码（添加 .SH/.SZ 后缀）
            formatted_symbols = PandasToPolarsProvider.format_symbols(universe_symbols)

            if self.logger:
                self.logger.info(
                    f"股票池: {universe_type}, {len(formatted_symbols)} 只股票"
                )

                self.logger.info(
                    "开始加载数据并回测（大池可能需数分钟，请关注 [回测引擎]/[合并面板] 进度日志）..."
                )

            universe_pit_warning = self._check_universe_pit(
                universe_type, start_date_str
            )

            try:
                result = engine.run(
                    strategy_obj,
                    start_date,
                    end_date,
                    formatted_symbols,
                    warmup_days=compute_warmup_days(dsl),
                    universe_pit_warning=universe_pit_warning,
                    strategy_yaml=strategy_yaml,
                    tags=tags,
                )
            finally:
                # 显式关闭审计连接，确保 WAL 落盘（防止进程退出后数据丢失）
                if audit_provider is not None and hasattr(audit_provider, "close"):
                    audit_provider.close()

            if self.logger:
                self.logger.info(
                    f"回测完成: total_return={result.total_return}, "
                    f"sharpe={result.sharpe_ratio}, "
                    f"max_drawdown={result.max_drawdown}"
                )

            strategy_diagnostics = self._build_strategy_diagnostics(
                strategy_obj, dsl, result
            )
            # 提到顶层：上层（监督器/反思/strategy_optimization）无需深挖 diagnostics
            metrics_unreliable = strategy_diagnostics.get("metrics_unreliable", False)

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
                    "metrics_unreliable": metrics_unreliable,
                    "universe_pit_warning": result.universe_pit_warning,
                }

            return {
                "error": result.message,
                "error_category": result.error_category or "unknown",
                "error_detail": result.error_detail or "",
                "strategy_diagnostics": strategy_diagnostics,
                "metrics_unreliable": metrics_unreliable,
                "universe_pit_warning": result.universe_pit_warning,
            }

        except Exception as e:
            if self.logger:
                self.logger.exception("回测执行异常")
            return {
                "error": str(e),
                "error_category": "engine_error",
                "error_detail": str(e),
                "universe_pit_warning": True,
            }

    @staticmethod
    def _validate_train_window(
        start_date: str,
        end_date: str,
        train_start: str,
        train_end: str,
    ) -> str:
        """验证网格寻优请求严格位于配置的训练集内。"""
        try:
            requested_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            requested_end = datetime.strptime(end_date, "%Y-%m-%d").date()
            allowed_start = datetime.strptime(train_start, "%Y-%m-%d").date()
            allowed_end = datetime.strptime(train_end, "%Y-%m-%d").date()
        except ValueError as exc:
            return f"训练集日期格式无效: {exc}"
        if requested_start >= requested_end:
            return "训练集日期倒序"
        if requested_start < allowed_start or requested_end > allowed_end:
            return (
                f"参数网格回测区间必须位于训练集 "
                f"{train_start}~{train_end} 内"
            )
        return ""

    def run_grid(  # noqa: PLR0913
        self,
        strategy_template: str,
        param_grid: ParamGrid,
        start_date: str = "",
        end_date: str = "",
        universe_type: str = "main_board+gem",
        benchmark_symbol: str = "",
        allow_large_grid: bool = False,
    ) -> dict[str, Any]:
        """参数网格并行回测。"""
        from long_earn.backtest.engine.parallel import ParallelRunner  # noqa: PLC0415

        start_date = start_date or self.config.backtest_start_date
        end_date = end_date or self.config.backtest_end_date

        boundary_error = self._validate_train_window(
            start_date,
            end_date,
            self.config.train_start_date,
            self.config.train_end_date,
        )
        if boundary_error:
            return {
                "error": boundary_error,
                "total": 0,
                "success_count": 0,
                "failure_count": 0,
                "best_sharpe": None,
                "best_return": None,
                "best_param_desc": "",
                "outcomes": [],
            }

        symbols = self._get_universe_symbols(universe_type, end_date.replace("-", ""))
        formatted_symbols = PandasToPolarsProvider.format_symbols(symbols)

        if self.logger:
            self.logger.info(
                f"[grid] 股票池: {universe_type}, {len(formatted_symbols)} 只"
            )

        runner = ParallelRunner(
            max_workers=self.max_workers,
            data_provider=self.data_provider,
        )
        result = runner.run_grid(
            strategy_template=strategy_template,
            param_grid=param_grid,
            start_date=start_date,
            end_date=end_date,
            symbols=formatted_symbols,
            benchmark_symbol=benchmark_symbol,
            allow_large_grid=allow_large_grid,
            write_pg=True,
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
        universe_type: str = "main_board+gem",
        benchmark_symbol: str = "",
        gap: int = 0,
    ) -> dict[str, Any]:
        """Walk-Forward 并行回测。"""
        from long_earn.backtest.engine.parallel import ParallelRunner  # noqa: PLC0415

        start_date = start_date or self.config.backtest_start_date
        end_date = end_date or self.config.backtest_end_date

        symbols = self._get_universe_symbols(universe_type, end_date.replace("-", ""))
        formatted_symbols = PandasToPolarsProvider.format_symbols(symbols)

        if self.logger:
            self.logger.info(
                f"[walk_forward_parallel] 股票池: {universe_type}, "
                f"{len(formatted_symbols)} 只, n_splits={n_splits}, gap={gap}"
            )

        runner = ParallelRunner(
            max_workers=self.max_workers,
            data_provider=self.data_provider,
        )
        result = runner.run_walk_forward_parallel(
            strategy_yaml=strategy_yaml,
            start_date=start_date,
            end_date=end_date,
            symbols=formatted_symbols,
            n_splits=n_splits,
            benchmark_symbol=benchmark_symbol,
            write_pg=True,
            gap=gap,
        )

        return result

    def run_oos(
        self,
        strategy_yaml: str,
        start_date: str = "",
        end_date: str = "",
        n_splits: int = 3,
        gap: int = 5,
    ) -> dict[str, Any]:
        """Walk-Forward OOS 验证（ADR-010 Phase 3 held-out 门）。

        在测试集区间上跑 Walk-Forward，返回 OOS 指标供合并决策。
        使用 config.test_start_date / test_end_date 作为默认区间。

        Args:
            strategy_yaml: 策略 YAML
            start_date: OOS 起始日期（默认 config.test_start_date）
            end_date: OOS 结束日期（默认 config.test_end_date）
            n_splits: Walk-Forward 折叠数
            gap: train/test 间隔离交易日数（purge/embargo，默认 5）

        Returns:
            WalkForwardResult dict: n_splits / fold_results / average_test_metrics /
            failed_folds / oos_sharpe
        """
        start_date = start_date or getattr(self.config, "test_start_date", "2025-01-01")
        end_date = end_date or getattr(self.config, "test_end_date", "2026-03-24")

        test_start = getattr(self.config, "test_start_date", "2025-01-01")
        test_end = getattr(self.config, "test_end_date", "2026-03-24")
        boundary_error = self._validate_oos_window(
            start_date, end_date, test_start, test_end
        )
        if boundary_error:
            return self._empty_oos_result(n_splits, boundary_error, gap=gap)

        if self.logger:
            self.logger.info(
                f"[OOS] Walk-Forward {start_date}~{end_date} "
                f"n_splits={n_splits} gap={gap}"
            )

        try:
            dsl = parse_strategy_yaml(strategy_yaml)
        except ValueError as e:
            return self._empty_oos_result(n_splits, f"策略解析失败: {e}", gap=gap)

        try:
            formatted_symbols, universe_type = self._resolve_oos_symbols(
                dsl, start_date
            )
            if not formatted_symbols:
                return self._empty_oos_result(
                    n_splits, f"股票池 '{universe_type}' 为空，数据源不可用", gap=gap
                )

            if self.logger:
                self.logger.info(
                    f"[OOS] 股票池: {universe_type}, {len(formatted_symbols)} 只股票"
                )

            # 性能优化（P0）：Walk-Forward fold 级并行
            # 默认走 ParallelRunner（max_workers=0 自动用 os.cpu_count()），
            # 每个 fold 独立进程跑 train+test，mmap IPC 文件零拷贝共享 full_data。
            # max_workers=1 时退化为串行（等价于旧 engine.walk_forward_run）。
            from long_earn.backtest.engine.parallel import (  # noqa: PLC0415
                ParallelRunner,
            )

            runner = ParallelRunner(
                max_workers=self.max_workers,
                data_provider=self.data_provider,
            )
            if self.logger:
                self.logger.info(
                    f"[OOS] 并行回测 max_workers={runner.max_workers} "
                    f"(0=自动 cpu_count)"
                )

            wf_result = runner.run_walk_forward_parallel(
                strategy_yaml=strategy_yaml,
                start_date=start_date,
                end_date=end_date,
                symbols=formatted_symbols,
                n_splits=n_splits,
                write_pg=True,
                gap=gap,
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"[OOS] Walk-Forward 失败: {e}")
            return self._empty_oos_result(n_splits, str(e), gap=gap)

        if isinstance(wf_result, dict) and wf_result.get("error"):
            # 引擎返回错误（如加载数据为空）
            return self._empty_oos_result(n_splits, wf_result["error"], gap=gap)

        return self._aggregate_oos_result(wf_result, n_splits)

    @staticmethod
    def _validate_oos_window(
        start_date: str,
        end_date: str,
        test_start: str,
        test_end: str,
    ) -> str:
        """验证 OOS 请求严格位于配置的测试集内。"""
        try:
            requested_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            requested_end = datetime.strptime(end_date, "%Y-%m-%d").date()
            allowed_start = datetime.strptime(test_start, "%Y-%m-%d").date()
            allowed_end = datetime.strptime(test_end, "%Y-%m-%d").date()
        except ValueError as exc:
            return f"OOS 日期格式无效: {exc}"
        if requested_start >= requested_end:
            return "OOS 日期倒序"
        if requested_start < allowed_start or requested_end > allowed_end:
            return f"OOS 区间必须位于测试集 {test_start}~{test_end} 内"
        return ""

    def _empty_oos_result(self, n_splits: int, error: str, *, gap: int = 0) -> dict[str, Any]:
        """构造空的 OOS 结果（失败/错误路径）。"""
        return {
            "n_splits": n_splits,
            "fold_results": [],
            "average_test_metrics": {},
            "failed_folds": list(range(n_splits)),
            "oos_sharpe": None,
            "error": error,
            "gap": gap,
        }

    def _resolve_oos_symbols(self, dsl: Any, start_date: str) -> tuple[list[str], str]:
        """解析 OOS 回测的股票池，返回 (formatted_symbols, universe_type)。

        含 main_board+gem 降级逻辑：指定股票池为空时退回默认池。
        """
        universe_type = dsl.universe.type or "main_board+gem"
        start_date_str = start_date.replace("-", "")
        universe_symbols = self._get_universe_symbols(universe_type, start_date_str)

        default_universe = "main_board+gem"
        if not universe_symbols and universe_type != default_universe:
            if self.logger:
                self.logger.warning(
                    f"股票池 '{universe_type}' 为空，降级到 {default_universe}"
                )
            universe_type = default_universe
            universe_symbols = self._get_universe_symbols(
                default_universe, start_date_str
            )

        if not universe_symbols:
            return [], universe_type

        formatted_symbols = PandasToPolarsProvider.format_symbols(universe_symbols)
        return formatted_symbols, universe_type

    def _aggregate_oos_result(
        self, wf_result: dict[str, Any], n_splits: int
    ) -> dict[str, Any]:
        """聚合 Walk-Forward 结果为 OOS 指标 dict。

        兼容 ParallelRunner（含 average_metrics）和旧 core.py 路径两种格式。
        """
        fold_results = wf_result.get("folds", []) or wf_result.get("fold_results", [])
        # 兼容 engine/core.py 与 engine/parallel.py 的两种键名：
        # - core.py 用 "test"（当前实现）
        # - 早期文档/测试用 "test_metrics"
        test_metrics = [
            f.get("test", {}) or f.get("test_metrics", {}) or {} for f in fold_results
        ]
        # failed_folds 兼容两种格式：
        # - ParallelRunner 返回 list of dicts（含 fold_id）
        # - 旧 core.py 返回 list of fold indices
        raw_failed = wf_result.get("failed_folds", [])
        if raw_failed and isinstance(raw_failed[0], dict):
            failed_folds = [f.get("fold_id", i) for i, f in enumerate(raw_failed)]
        else:
            failed_folds = list(raw_failed)

        # 计算平均 OOS 指标
        # 优先用 ParallelRunner 返回的 average_metrics.test（已聚合），
        # 退化到自行从 fold_results 聚合（兼容旧 core.py 路径）
        avg_metrics: dict[str, float] = {}
        avg_from_runner = wf_result.get("average_metrics", {}).get("test", {}) or {}
        if avg_from_runner:
            avg_metrics = dict(avg_from_runner)
        elif test_metrics:
            for key in ("sharpe_ratio", "total_return", "max_drawdown"):
                values = [
                    float(m.get(key, 0)) for m in test_metrics if m.get(key) is not None
                ]
                if values:
                    avg_metrics[key] = sum(values) / len(values)

        oos_sharpe = avg_metrics.get("sharpe_ratio")

        return {
            "n_splits": n_splits,
            "fold_results": fold_results,
            "average_test_metrics": avg_metrics,
            "failed_folds": failed_folds,
            "oos_sharpe": oos_sharpe,
        }

    def run_candidates(
        self,
        strategy_yamls: list[str],
        start_date: str = "",
        end_date: str = "",
        universe_type: str = "",
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """批量并行回测多个候选策略（ADR-010 阶段 5 收尾）。

        共享数据面板 + 进程池分发，各候选独立解析风控参数与 warmup
        （ADR-008 B5），diagnostics 保真回传（ADR-008 B6）。
        返回与 strategy_yamls 等长的结果列表，每项与 run() 返回结构一致。
        """
        if not strategy_yamls:
            return []

        from long_earn.backtest.engine.parallel import (  # noqa: PLC0415
            ParallelRunner,
        )

        start_date = start_date or self.config.backtest_start_date
        end_date = end_date or self.config.backtest_end_date

        # universe_type 缺省时从首候选 DSL 解析（HTR 候选通常同 universe）
        if not universe_type:
            try:
                first_dsl = parse_strategy_yaml(strategy_yamls[0])
                universe_type = first_dsl.universe.type or "main_board+gem"
            except ValueError:
                universe_type = "main_board+gem"

        start_date_str = start_date.replace("-", "")
        universe_symbols = self._get_universe_symbols(universe_type, start_date_str)
        if not universe_symbols and universe_type != "main_board+gem":
            if self.logger:
                self.logger.warning(
                    f"股票池 '{universe_type}' 为空，降级到 main_board+gem"
                )
            universe_type = "main_board+gem"
            universe_symbols = self._get_universe_symbols(
                "main_board+gem", start_date_str
            )

        if not universe_symbols:
            err = f"股票池 '{universe_type}' 为空，数据源不可用"
            return [
                {
                    "error": err,
                    "error_category": "engine_error",
                    "error_detail": f"无法获取 {universe_type} 成分股",
                }
                for _ in strategy_yamls
            ]

        formatted_symbols = PandasToPolarsProvider.format_symbols(universe_symbols)

        if self.logger:
            self.logger.info(
                f"[candidates] 股票池: {universe_type}, "
                f"{len(formatted_symbols)} 只, {len(strategy_yamls)} 候选"
            )

        runner = ParallelRunner(
            max_workers=self.max_workers,
            data_provider=self.data_provider,
        )
        outcomes = runner.run_candidates(
            strategy_yamls=strategy_yamls,
            start_date=start_date,
            end_date=end_date,
            symbols=formatted_symbols,
            write_pg=True,
            tags=tags,
        )

        # BacktestOutcome -> run() 同结构 dict（diagnostics 保真，ADR-008 B6）
        return [self._outcome_to_dict(o) for o in outcomes]

    def _outcome_to_dict(self, outcome: Any) -> dict[str, Any]:
        """把 BacktestOutcome 转为与 run() 返回同结构的 dict。

        ADR-008 B6：strategy_diagnostics 完整保留 degenerate/step_failures/
        factor_failures，不降级，确保 AcceptanceGate 的 degenerate 检测有效。
        """
        if not outcome.success:
            return {
                "error": outcome.error,
                "error_category": outcome.error_category or "unknown",
                "metrics_unreliable": outcome.metrics_unreliable,
            }

        failed_factor_aliases: set[str] = {
            alias for f in outcome.factor_failures if (alias := f.get("alias"))
        }
        failed_step_labels: set[str] = {
            label for f in outcome.step_failures if (label := f.get("step"))
        }
        strategy_diagnostics = {
            "factor_failures": list(outcome.factor_failures),
            "step_failures": list(outcome.step_failures),
            "failed_factor_aliases": sorted(failed_factor_aliases),
            "failed_step_labels": sorted(failed_step_labels),
            "total_signals": 0,  # 并行 worker 无法回传 signals 计数，仅用于展示
            "trade_count": outcome.trade_count,
            "degenerate": outcome.degenerate,
            "metrics_unreliable": outcome.metrics_unreliable,
        }
        return {
            "total_return": outcome.total_return,
            "annual_return": outcome.annual_return,
            "sharpe_ratio": outcome.sharpe_ratio,
            "max_drawdown": outcome.max_drawdown,
            "win_rate": outcome.win_rate,
            "trading_days": outcome.trading_days,
            "volatility": outcome.volatility,
            "calmar_ratio": outcome.calmar_ratio,
            "sortino_ratio": outcome.sortino_ratio,
            "strategy_diagnostics": strategy_diagnostics,
            "metrics_unreliable": outcome.metrics_unreliable,
        }

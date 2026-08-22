"""进程级并行编排层

提供参数网格并行回测和 Walk-Forward 并行回测。
每个 worker 独立构造引擎实例，通过 SharedMemory 共享数据底座。
并行 worker 直接并发写 PostgreSQL 审计表（PG MVCC 原生支持多写者），
不再使用「worker 临时 DuckDB 文件 + 主进程合并」的旧架构。
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml as yaml_lib
from loguru import logger

from long_earn.backtest.engine.core import (
    EventDrivenBacktestEngine,
    InMemoryAuditTrail,
)
from long_earn.backtest.engine.dsl import compute_warmup_days, parse_strategy_yaml
from long_earn.backtest.engine.dsl_strategy import DSLStrategy
from long_earn.backtest.engine.param_grid import (
    ParamGrid,
    apply_struct_params,
    render_template,
)
from long_earn.backtest.engine.shared_data import SharedDataContext
from long_earn.core.stdio import ensure_utf8_stdio


@dataclass(slots=True)
class BacktestTask:
    """单个并行回测任务（可 pickle）。"""

    strategy_yaml: str
    start_date: str
    end_date: str
    symbols: list[str]
    benchmark_symbol: str
    shm_token: str
    shm_size: int
    pickle_data: bytes
    stop_loss: float | None = None
    max_drawdown_limit: float | None = None
    max_position_pct: float = 1.0
    max_positions: int = 0
    task_id: str = ""
    param_desc: str = ""
    # 审计开关：非空字符串表示启用 worker 审计（worker 直接并发写 PG 主库
    # backtest_audit.logs，PostgreSQL MVCC 原生支持多写者）；空串表示关闭。
    # 不再承载临时文件路径语义。
    audit_db_path: str = ""
    # ADR-008 B5：warmup 注入契约。每 task 独立算（run_grid 每 combo、
    # run_candidates 每候选），worker 透传给 engine.run(warmup_days=...)。
    warmup_days: int = 0
    # run 级标签：测试/冒烟并行回测携带 RUN_TAG_TEST（"test"），worker 透传
    # 给 engine.run(tags=...)，写入 RUN_START payload.tags 供审计清理识别。
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BacktestOutcome:
    """单个并行回测结果（可 pickle）。

    ADR-008 B6：diagnostics 保真。worker 内从 DSLStrategy 实例提取
    factor_failures/step_failures，主进程据此重建完整 strategy_diagnostics，
    确保 AcceptanceGate 的 degenerate 检测不被降级破坏。
    """

    task_id: str
    success: bool
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    volatility: float = 0.0
    trading_days: int = 0
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0
    error: str = ""
    error_category: str = ""
    param_desc: str = ""
    metrics_unreliable: bool = False
    # ADR-008 B6：diagnostics 保真字段
    trade_count: int = 0
    degenerate: bool = False
    factor_failures: list[dict] = field(default_factory=list)
    step_failures: list[dict] = field(default_factory=list)


def _shift_start_date(start_date: str, warmup_days: int) -> str:
    """将起始日期前移 warmup_days 天（日历日），覆盖算子回溯窗口。

    ADR-008 B5：预取区间需前移 max_warmup，让 worker 内 engine.run 的
    时序因子在 start_date 当天就有非 NaN 值。warmup_days=0 时原样返回。
    """
    if warmup_days <= 0:
        return start_date
    from datetime import datetime, timedelta  # noqa: PLC0415

    dt = datetime.strptime(start_date, "%Y-%m-%d")
    return (dt - timedelta(days=warmup_days)).strftime("%Y-%m-%d")


@contextmanager
def _disable_xtquant_env():
    """临时禁用 xtquant 的环境变量上下文管理器（P2-06）。

    max_workers<=1 顺序执行时，_run_one_backtest 在主进程内运行，
    直接 os.environ[...] = "1" 会污染主进程环境。用上下文管理器包裹，
    退出后恢复原值（或删除新增的键）。
    """
    key = "LONG_EARN_DISABLE_XTQUANT"
    had_key = key in os.environ
    old_val = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if had_key:
            os.environ[key] = old_val  # type: ignore[assignment]
        else:
            os.environ.pop(key, None)


def _run_one_backtest(task: BacktestTask) -> BacktestOutcome:
    """worker 入口：独立构造引擎 + 策略，执行单次回测。

    P2-06：环境变量用 contextmanager 包裹，函数退出后自动清理，
    避免 max_workers<=1 顺序执行时污染主进程环境。

    Windows 乱码修复：ProcessPoolExecutor 以 spawn 方式创建 worker 子进程，
    子进程的 sys.stdout/stderr 会按 GBK 重建（不继承主进程的 UTF-8 reconfigure），
    导致 worker 内中文日志/print 在 UTF-8 终端上乱码。worker 入口显式切 UTF-8。
    """
    try:
        ensure_utf8_stdio()

        full_data = SharedDataContext.attach(
            task.shm_token, task.shm_size, task.pickle_data
        )

        dsl = parse_strategy_yaml(task.strategy_yaml)

        # 注入 PostgresAuditProvider：worker 直接并发写 PG 主库审计表，
        # PostgreSQL MVCC 原生支持多写者，无需 worker 临时文件与合并。
        audit_provider = None
        if task.audit_db_path:
            from long_earn.backtest.engine.audit import (  # noqa: PLC0415
                PostgresAuditProvider,
            )

            audit_provider = PostgresAuditProvider()

        engine = EventDrivenBacktestEngine(
            cost_config=dsl.trading_cost.to_broker_config(),
            stop_loss=task.stop_loss,
            max_drawdown_limit=task.max_drawdown_limit,
            max_position_pct=task.max_position_pct,
            max_positions=task.max_positions,
            audit_logger=InMemoryAuditTrail(),
            audit_provider=audit_provider,
        )
        engine.data_provider = None

        strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)

        # P2-06：环境变量用上下文管理器包裹，退出后自动恢复
        with _disable_xtquant_env():
            result = engine.run(
                strategy,
                task.start_date,
                task.end_date,
                task.symbols,
                task.benchmark_symbol,
                full_data=full_data,
                warmup_days=task.warmup_days,
                strategy_yaml=task.strategy_yaml,
                tags=task.tags,
            )

        if result.success:
            # ADR-008 B6：从 DSLStrategy 实例提取 diagnostics，保真回填
            trade_count = result.trade_count or 0
            factor_failures = list(strategy.factor_failures)
            step_failures = list(strategy.step_failures)
            any_step_failed = len(step_failures) > 0
            any_factor_failed = len(factor_failures) > 0
            degenerate = trade_count == 0
            metrics_unreliable = (
                result.metrics_unreliable
                or degenerate
                or any_step_failed
                or any_factor_failed
            )
            return BacktestOutcome(
                task_id=task.task_id,
                success=True,
                total_return=result.total_return,
                annual_return=result.annual_return,
                sharpe_ratio=result.sharpe_ratio,
                max_drawdown=result.max_drawdown,
                win_rate=result.win_rate,
                volatility=result.volatility,
                trading_days=result.trading_days,
                calmar_ratio=result.calmar_ratio,
                sortino_ratio=result.sortino_ratio,
                param_desc=task.param_desc,
                metrics_unreliable=metrics_unreliable,
                trade_count=trade_count,
                degenerate=degenerate,
                factor_failures=factor_failures,
                step_failures=step_failures,
            )
        return BacktestOutcome(
            task_id=task.task_id,
            success=False,
            error=result.message,
            error_category=result.error_category or "unknown",
            param_desc=task.param_desc,
        )
    except Exception as e:
        return BacktestOutcome(
            task_id=task.task_id,
            success=False,
            error=str(e),
            error_category="engine_error",
            param_desc=task.param_desc,
        )


@dataclass
class GridResult:
    """参数网格回测汇总结果。"""

    outcomes: list[BacktestOutcome] = field(default_factory=list)

    @property
    def best(self) -> BacktestOutcome | None:
        """按 sharpe_ratio 降序排序的最优结果。

        P0-03：过滤 metrics_unreliable=True 的结果，防止退化策略混入最优解。
        """
        reliable = [o for o in self.outcomes if o.success and not o.metrics_unreliable]
        if not reliable:
            # 降级：无可信结果时退回全部成功结果（向前兼容）
            reliable = [o for o in self.outcomes if o.success]
            if not reliable:
                return None
        return max(reliable, key=lambda o: o.sharpe_ratio)

    @property
    def best_by_return(self) -> BacktestOutcome | None:
        """按 total_return 降序排序的最优结果。

        P0-03：过滤 metrics_unreliable=True 的结果。
        """
        reliable = [o for o in self.outcomes if o.success and not o.metrics_unreliable]
        if not reliable:
            reliable = [o for o in self.outcomes if o.success]
            if not reliable:
                return None
        return max(reliable, key=lambda o: o.total_return)

    @property
    def success_count(self) -> int:
        return sum(1 for o in self.outcomes if o.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for o in self.outcomes if not o.success)


_MAX_GRID_DEFAULT = 256


class ParallelRunner:
    """并行回测编排器。

    Args:
        max_workers: 进程池大小，默认 ``os.cpu_count()``。
        data_provider: 可选的数据提供者（面向 :class:`DataProvider` 业务接口）。
            注入时主进程预取走降级链（DuckDB→miniqmt→ciccwm→akshare）；
            未注入时退回本地 :class:`MiniQmtDataProvider`（向后兼容）。
    """

    def __init__(
        self,
        max_workers: int = 0,
        data_provider: Any = None,
    ) -> None:
        self.max_workers = max_workers or os.cpu_count() or 1
        self.data_provider = data_provider

    def _prepare_data(self, symbols: list[str], start_date: str, end_date: str) -> Any:
        """预取合并面板为 polars DataFrame（主进程执行，worker 通过 SharedMemory 共享）。

        优先用注入的 ``data_provider``（走降级链），未注入时退回 ``MiniQmtDataProvider``。
        """
        from long_earn.backtest.data.polars_adapter import (  # noqa: PLC0415
            PandasToPolarsProvider,
        )

        if self.data_provider is not None:
            provider = self.data_provider
            # 已实现 get_merged_panel_as_polars 的 provider（如 CompositeDataProvider）
            # 直接调用；否则用 PandasToPolarsProvider 适配
            if hasattr(provider, "get_merged_panel_as_polars"):
                return provider.get_merged_panel_as_polars(
                    symbols, start_date, end_date
                )
            return PandasToPolarsProvider(provider).get_merged_panel_as_polars(
                symbols, start_date, end_date
            )
        # 向后兼容：未注入时退回本地 MiniQmtDataProvider
        from long_earn.backtest.data.miniqmt_provider import (  # noqa: PLC0415
            MiniQmtDataProvider,
        )

        return PandasToPolarsProvider(MiniQmtDataProvider()).get_merged_panel_as_polars(
            symbols, start_date, end_date
        )

    def run_grid(  # noqa: PLR0913
        self,
        strategy_template: str,
        param_grid: ParamGrid,
        start_date: str,
        end_date: str,
        symbols: list[str],
        benchmark_symbol: str = "",
        max_positions: int = 0,
        allow_large_grid: bool = False,
        audit_db_path: Path | str | None = None,
        tags: list[str] | None = None,
    ) -> GridResult:
        """参数网格并行回测。"""
        combos = param_grid.expand_all()
        total = len(combos)
        if total > _MAX_GRID_DEFAULT and not allow_large_grid:
            raise ValueError(
                f"参数组合 {total} 超过默认上限 {_MAX_GRID_DEFAULT}，"
                f"设置 allow_large_grid=True 以确认"
            )

        logger.info(f"[grid] 展开 {total} 组合, max_workers={self.max_workers}")

        # 生成所有策略 YAML + 每 combo 独立算 warmup/风控（ADR-008 B5）
        # struct_params 可改算子参数或风控参数，各 combo 必须独立解析
        tasks_data: list[dict[str, Any]] = []
        for _idx, (scalar_params, struct_params) in enumerate(combos):
            yaml_str = render_template(strategy_template, scalar_params)
            dsl = parse_strategy_yaml(yaml_str)
            if struct_params:
                dsl = apply_struct_params(dsl, struct_params)
            final_yaml = yaml_lib.dump(
                {"strategy": dsl.model_dump()},
                allow_unicode=True,
                sort_keys=False,
            )
            param_desc = ", ".join(
                f"{k}={v}" for k, v in {**scalar_params, **struct_params}.items()
            )
            tasks_data.append(
                {
                    "yaml": final_yaml,
                    "param_desc": param_desc,
                    "warmup_days": compute_warmup_days(dsl),
                    "stop_loss": dsl.risk_control.stop_loss,
                    "max_drawdown_limit": dsl.risk_control.max_drawdown_limit,
                    "max_position_pct": dsl.risk_control.max_position_per_stock,
                }
            )

        # 预取数据：区间前移 max_warmup 覆盖最大回溯需求（ADR-008 B5）
        max_warmup = max(td["warmup_days"] for td in tasks_data)
        prefetch_start = _shift_start_date(start_date, max_warmup)
        full_data = self._prepare_data(symbols, prefetch_start, end_date)

        if full_data.is_empty():
            logger.error("[grid] 数据预取为空，无法执行并行回测")
            return GridResult(
                outcomes=[
                    BacktestOutcome(
                        task_id="all",
                        success=False,
                        error="数据预取为空",
                        error_category="insufficient_data",
                    )
                ]
            )

        # 构造 BacktestTask 列表
        audit_base = Path(audit_db_path) if audit_db_path else None
        with SharedDataContext(full_data) as ctx:
            shm_token, shm_size, pickle_data = ctx.get_worker_args()

            tasks = [
                BacktestTask(
                    strategy_yaml=td["yaml"],
                    start_date=start_date,
                    end_date=end_date,
                    symbols=symbols,
                    benchmark_symbol=benchmark_symbol,
                    shm_token=shm_token,
                    shm_size=shm_size,
                    pickle_data=pickle_data,
                    stop_loss=td["stop_loss"],
                    max_drawdown_limit=td["max_drawdown_limit"],
                    max_position_pct=td["max_position_pct"],
                    max_positions=max_positions,
                    task_id=str(idx),
                    param_desc=td["param_desc"],
                    audit_db_path="pg" if audit_base else "",
                    warmup_days=td["warmup_days"],
                    tags=tags or [],
                )
                for idx, td in enumerate(tasks_data)
            ]

            outcomes = self._execute_tasks(tasks)

        result = GridResult(outcomes=outcomes)
        logger.info(
            f"[grid] 完成: {result.success_count}/{total} 成功, "
            f"best sharpe={result.best.sharpe_ratio if result.best else 'N/A'}"
        )
        return result

    def run_walk_forward_parallel(  # noqa: PLR0913
        self,
        strategy_yaml: str,
        start_date: str,
        end_date: str,
        symbols: list[str],
        n_splits: int = 3,
        benchmark_symbol: str = "",
        audit_db_path: Path | str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Walk-Forward 并行回测。"""
        from long_earn.backtest.engine.timeseries_split import (  # noqa: PLC0415
            TimeSeriesSplit,
        )

        dsl = parse_strategy_yaml(strategy_yaml)
        stop_loss = dsl.risk_control.stop_loss
        max_drawdown_limit = dsl.risk_control.max_drawdown_limit
        max_position_pct = dsl.risk_control.max_position_per_stock
        # ADR-008 B5：walk-forward 各 fold 训练期初也需要 warmup 填时序因子
        warmup_days = compute_warmup_days(dsl)
        prefetch_start = _shift_start_date(start_date, warmup_days)
        full_data = self._prepare_data(symbols, prefetch_start, end_date)

        if full_data.is_empty():
            return {"error": "数据预取为空"}

        from long_earn.backtest.engine.core import (  # noqa: PLC0415
            EventDrivenBacktestEngine,
        )

        engine = EventDrivenBacktestEngine()
        timestamps = engine._get_timestamps(full_data)
        splitter = TimeSeriesSplit(n_splits=n_splits)
        splits = splitter.split(timestamps)

        audit_base = Path(audit_db_path) if audit_db_path else None
        with SharedDataContext(full_data) as ctx:
            shm_token, shm_size, pickle_data = ctx.get_worker_args()

            tasks: list[BacktestTask] = []

            for fold_idx, (train_ts, test_ts) in enumerate(splits):
                train_start = str(train_ts[0])
                train_end = str(train_ts[-1])
                test_start = str(test_ts[0]) if test_ts else train_end
                test_end = str(test_ts[-1]) if test_ts else train_end

                train_task_id = f"{fold_idx}_train"
                test_task_id = f"{fold_idx}_test"
                tasks.append(
                    BacktestTask(
                        strategy_yaml=strategy_yaml,
                        start_date=train_start,
                        end_date=train_end,
                        symbols=symbols,
                        benchmark_symbol=benchmark_symbol,
                        shm_token=shm_token,
                        shm_size=shm_size,
                        pickle_data=pickle_data,
                        stop_loss=stop_loss,
                        max_drawdown_limit=max_drawdown_limit,
                        max_position_pct=max_position_pct,
                        task_id=train_task_id,
                        param_desc=f"fold {fold_idx} train",
                        audit_db_path="pg" if audit_base else "",
                        warmup_days=warmup_days,
                        tags=tags or [],
                    )
                )
                tasks.append(
                    BacktestTask(
                        strategy_yaml=strategy_yaml,
                        start_date=test_start,
                        end_date=test_end,
                        symbols=symbols,
                        benchmark_symbol=benchmark_symbol,
                        shm_token=shm_token,
                        shm_size=shm_size,
                        pickle_data=pickle_data,
                        stop_loss=stop_loss,
                        max_drawdown_limit=max_drawdown_limit,
                        max_position_pct=max_position_pct,
                        task_id=test_task_id,
                        param_desc=f"fold {fold_idx} test",
                        audit_db_path="pg" if audit_base else "",
                        warmup_days=warmup_days,
                        tags=tags or [],
                    )
                )

            outcomes = self._execute_tasks(tasks)

        # 按 fold 汇总
        fold_results: list[dict[str, Any]] = []
        all_train_metrics: list[dict[str, float]] = []
        all_test_metrics: list[dict[str, float]] = []
        failed_folds: list[dict[str, Any]] = []

        outcome_map = {o.task_id: o for o in outcomes}
        for fold_idx in range(n_splits):
            train_o = outcome_map.get(f"{fold_idx}_train")
            test_o = outcome_map.get(f"{fold_idx}_test")

            train_metrics: dict[str, float] = {}
            test_metrics: dict[str, float] = {}
            if train_o and train_o.success:
                train_metrics = {
                    "total_return": train_o.total_return,
                    "sharpe_ratio": train_o.sharpe_ratio,
                    "max_drawdown": train_o.max_drawdown,
                }
                all_train_metrics.append(train_metrics)
            else:
                # P2-05：记录失败 fold，与 core.py:walk_forward_run 对齐
                failed_folds.append(
                    {
                        "fold_id": fold_idx,
                        "phase": "train",
                        "error_category": (
                            train_o.error_category if train_o else "missing"
                        ),
                        "message": (train_o.error if train_o else "worker 未返回结果"),
                    }
                )
            if test_o and test_o.success:
                test_metrics = {
                    "total_return": test_o.total_return,
                    "sharpe_ratio": test_o.sharpe_ratio,
                    "max_drawdown": test_o.max_drawdown,
                }
                all_test_metrics.append(test_metrics)
            else:
                failed_folds.append(
                    {
                        "fold_id": fold_idx,
                        "phase": "test",
                        "error_category": (
                            test_o.error_category if test_o else "missing"
                        ),
                        "message": (test_o.error if test_o else "worker 未返回结果"),
                    }
                )

            fold_results.append(
                {
                    "fold_id": fold_idx,
                    "train": train_metrics,
                    "test": test_metrics,
                }
            )

        def _avg(metrics_list: list[dict[str, float]]) -> dict[str, float]:
            if not metrics_list:
                return {}
            return {
                k: float(np.mean([m[k] for m in metrics_list])) for k in metrics_list[0]
            }

        return {
            "fold_results": fold_results,
            "average_metrics": {
                "train": _avg(all_train_metrics),
                "test": _avg(all_test_metrics),
            },
            "n_splits": n_splits,
            "failed_folds": failed_folds,
        }

    def run_candidates(  # noqa: PLR0913
        self,
        strategy_yamls: list[str],
        start_date: str,
        end_date: str,
        symbols: list[str],
        benchmark_symbol: str = "",
        audit_db_path: Path | str | None = None,
        tags: list[str] | None = None,
    ) -> list[BacktestOutcome]:
        """批量候选并行回测（ADR-010 阶段 5 收尾）。

        各候选 DSL 独立解析风控参数与 warmup（ADR-008 B5）；
        预取区间前移 max_warmup 覆盖最大回溯需求；
        SharedMemory 共享面板 + 进程池分发。
        返回 list[BacktestOutcome]，顺序与输入 strategy_yamls 对齐。
        """
        if not strategy_yamls:
            return []

        # 逐候选解析：风控参数 + warmup 各自独立
        candidates: list[dict[str, Any]] = []
        for idx, yaml_str in enumerate(strategy_yamls):
            dsl = parse_strategy_yaml(yaml_str)
            candidates.append(
                {
                    "yaml": yaml_str,
                    "warmup_days": compute_warmup_days(dsl),
                    "stop_loss": dsl.risk_control.stop_loss,
                    "max_drawdown_limit": dsl.risk_control.max_drawdown_limit,
                    "max_position_pct": dsl.risk_control.max_position_per_stock,
                    "max_positions": 0,
                    "task_id": f"candidate_{idx}",
                }
            )

        # 预取区间前移 max_warmup（ADR-008 B5）
        max_warmup = max(c["warmup_days"] for c in candidates)
        prefetch_start = _shift_start_date(start_date, max_warmup)
        full_data = self._prepare_data(symbols, prefetch_start, end_date)

        if full_data.is_empty():
            logger.error("[candidates] 数据预取为空，无法执行并行回测")
            return [
                BacktestOutcome(
                    task_id=c["task_id"],
                    success=False,
                    error="数据预取为空",
                    error_category="insufficient_data",
                )
                for c in candidates
            ]

        logger.info(
            f"[candidates] {len(candidates)} 候选, "
            f"max_workers={self.max_workers}, max_warmup={max_warmup}"
        )

        audit_base = Path(audit_db_path) if audit_db_path else None
        with SharedDataContext(full_data) as ctx:
            shm_token, shm_size, pickle_data = ctx.get_worker_args()

            tasks = [
                BacktestTask(
                    strategy_yaml=c["yaml"],
                    start_date=start_date,
                    end_date=end_date,
                    symbols=symbols,
                    benchmark_symbol=benchmark_symbol,
                    shm_token=shm_token,
                    shm_size=shm_size,
                    pickle_data=pickle_data,
                    stop_loss=c["stop_loss"],
                    max_drawdown_limit=c["max_drawdown_limit"],
                    max_position_pct=c["max_position_pct"],
                    max_positions=c["max_positions"],
                    task_id=c["task_id"],
                    audit_db_path="pg" if audit_base else "",
                    warmup_days=c["warmup_days"],
                    tags=tags or [],
                )
                for c in candidates
            ]

            outcomes = self._execute_tasks(tasks)

        success_count = sum(1 for o in outcomes if o.success)
        logger.info(f"[candidates] 完成: {success_count}/{len(candidates)} 成功")
        return outcomes

    def _execute_tasks(self, tasks: list[BacktestTask]) -> list[BacktestOutcome]:
        """执行任务列表，max_workers=1 时退化为顺序。"""
        if self.max_workers <= 1:
            return [_run_one_backtest(t) for t in tasks]

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            return list(executor.map(_run_one_backtest, tasks))

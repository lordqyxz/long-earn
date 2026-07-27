"""进程级并行编排层

提供参数网格并行回测和 Walk-Forward 并行回测。
每个 worker 独立构造引擎实例，通过 SharedMemory 共享数据底座。
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
from long_earn.backtest.engine.dsl import parse_strategy_yaml
from long_earn.backtest.engine.param_grid import (
    ParamGrid,
    apply_struct_params,
    render_template,
)
from long_earn.backtest.engine.shared_data import SharedDataContext


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
    audit_db_path: str = ""


@dataclass(slots=True)
class BacktestOutcome:
    """单个并行回测结果（可 pickle）。"""

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


def _worker_db_path(base: Path, task_id: str) -> Path:
    """派生 worker 专属 DuckDB 路径，避免多进程共享连接写冲突。"""
    return base.parent / f"{base.stem}_{task_id}{base.suffix}"


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
    """
    try:
        full_data = SharedDataContext.attach(
            task.shm_token, task.shm_size, task.pickle_data
        )

        dsl = parse_strategy_yaml(task.strategy_yaml)

        # 注入 DuckDBAuditProvider：每个 worker 使用独立的 db 文件，
        # 避免 DuckDB 连接跨进程共享（非线程安全）。
        audit_provider = None
        if task.audit_db_path:
            from long_earn.backtest.engine.audit import (  # noqa: PLC0415
                DuckDBAuditProvider,
            )

            audit_provider = DuckDBAuditProvider(db_path=Path(task.audit_db_path))

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

        from long_earn.services.backtest_service import DSLStrategy  # noqa: PLC0415

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
            )

        if result.success:
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
                metrics_unreliable=result.metrics_unreliable or False,
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

    def _prepare_data(
        self, symbols: list[str], start_date: str, end_date: str
    ) -> Any:
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
                return provider.get_merged_panel_as_polars(symbols, start_date, end_date)
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

        # 生成所有策略 YAML
        tasks_data: list[tuple[str, str]] = []
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
            tasks_data.append((final_yaml, param_desc))

        # 预取数据
        first_dsl = parse_strategy_yaml(tasks_data[0][0])
        stop_loss = first_dsl.risk_control.stop_loss
        max_drawdown_limit = first_dsl.risk_control.max_drawdown_limit
        max_position_pct = first_dsl.risk_control.max_position_per_stock

        full_data = self._prepare_data(symbols, start_date, end_date)

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
                    strategy_yaml=yaml_str,
                    start_date=start_date,
                    end_date=end_date,
                    symbols=symbols,
                    benchmark_symbol=benchmark_symbol,
                    shm_token=shm_token,
                    shm_size=shm_size,
                    pickle_data=pickle_data,
                    stop_loss=stop_loss,
                    max_drawdown_limit=max_drawdown_limit,
                    max_position_pct=max_position_pct,
                    max_positions=max_positions,
                    task_id=str(idx),
                    param_desc=param_desc,
                    audit_db_path=str(_worker_db_path(audit_base, str(idx)))
                    if audit_base
                    else "",
                )
                for idx, (yaml_str, param_desc) in enumerate(tasks_data)
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
    ) -> dict[str, Any]:
        """Walk-Forward 并行回测。"""
        from long_earn.backtest.engine.timeseries_split import (  # noqa: PLC0415
            TimeSeriesSplit,
        )

        full_data = self._prepare_data(symbols, start_date, end_date)

        if full_data.is_empty():
            return {"error": "数据预取为空"}

        dsl = parse_strategy_yaml(strategy_yaml)
        stop_loss = dsl.risk_control.stop_loss
        max_drawdown_limit = dsl.risk_control.max_drawdown_limit
        max_position_pct = dsl.risk_control.max_position_per_stock

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
                        audit_db_path=str(_worker_db_path(audit_base, train_task_id))
                        if audit_base
                        else "",
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
                        audit_db_path=str(_worker_db_path(audit_base, test_task_id))
                        if audit_base
                        else "",
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
                        "error_category": (train_o.error_category if train_o else "missing"),
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
                        "error_category": (test_o.error_category if test_o else "missing"),
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

    def _execute_tasks(self, tasks: list[BacktestTask]) -> list[BacktestOutcome]:
        """执行任务列表，max_workers=1 时退化为顺序。"""
        if self.max_workers <= 1:
            return [_run_one_backtest(t) for t in tasks]

        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            return list(executor.map(_run_one_backtest, tasks))

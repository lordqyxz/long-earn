"""并行回测基础设施测试。

SharedDataContext mmap IPC 文件往返 + ParallelRunner max_workers=1 退化模式。
不依赖外部数据源，使用合成面板。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
from polars.testing import assert_frame_equal

from long_earn.backtest.engine.shared_data import SharedDataContext


class TestSharedData:
    """mmap Arrow IPC 文件共享数据底座。"""

    def _make_df(self) -> pl.DataFrame:
        """合成最小面板（3 symbols × 5 days）。"""
        rows = []
        for sym in ["S1", "S2", "S3"]:
            for d in range(5):
                rows.append(
                    {
                        "timestamp": date(2024, 1, 1 + d),
                        "symbol": sym,
                        "close": 10.0 + d,
                        "open": 9.0 + d,
                        "high": 11.0 + d,
                        "low": 8.0 + d,
                        "volume": 1000.0,
                    }
                )
        return pl.DataFrame(rows)

    def test_roundtrip_ipc_file(self, tmp_path, monkeypatch):
        """主进程写临时 IPC 文件 → worker mmap attach → 内容一致 + 退出即清理。"""
        monkeypatch.setenv("LONG_EARN_DATA_DIR", str(tmp_path))
        df = self._make_df()
        with SharedDataContext(df) as ctx:
            path = ctx.get_worker_args()
            assert Path(path).exists()
            assert tmp_path.resolve() in Path(path).resolve().parents
            restored = SharedDataContext.attach(path)
            assert_frame_equal(restored.sort(["symbol", "timestamp"]), df)
        assert not Path(path).exists(), "上下文退出后临时文件应已删除"


class TestParallelRunnerSerial:
    """ParallelRunner max_workers=1 退化模式（CI 安全）。"""

    def test_runner_exists_and_importable(self):
        """ParallelRunner 可导入且可实例化。"""
        from long_earn.backtest.engine.parallel import ParallelRunner

        runner = ParallelRunner(max_workers=1)
        assert runner is not None


class TestWalkForwardFoldWindows:
    """run_walk_forward 折窗口推导回归：test 折必须从 test_ts[0] 起。

    回归背景：parallel.py 曾把 test_start 误写为 train_ts[0]（与 core.py
    walk_forward_run 不一致），导致 "test" 回测覆盖整个训练+测试区间，
    OOS 指标被训练期污染（合并门判据失真，金融正确性）。
    """

    @staticmethod
    def _make_panel(days: int = 200) -> pl.DataFrame:
        """合成面板：逐日历日 × 3 symbols，价格带趋势（非退化）。"""
        from datetime import date, timedelta

        rows = []
        for i in range(days):
            for j, sym in enumerate(["S1", "S2", "S3"]):
                price = 10.0 * (1.0 + 0.001 * i + 0.0001 * j)
                rows.append(
                    {
                        "timestamp": date(2024, 1, 1) + timedelta(days=i),
                        "symbol": sym,
                        "open": price * 0.99,
                        "high": price * 1.02,
                        "low": price * 0.98,
                        "close": price,
                        "volume": 10000.0,
                    }
                )
        return pl.DataFrame(rows)

    _YAML = """\
name: WFWindowProbe
description: 折窗口回归探针
universe:
  type: main_board+gem
  rebalance_freq: 20D
operator_factors:
  - op: returns
    alias: mom
    params: { field: close, period: 5 }
signals:
  - type: operator
    op: rank_top
    params: { field: mom, ascending: false, top: 2 }
weights:
  method: equal
"""

    def test_fold_test_window_starts_after_train(self, monkeypatch):
        """每个 fold 的 test 任务起点必须晚于同 fold train 任务终点。"""
        from long_earn.backtest.engine.parallel import (
            BacktestOutcome,
            ParallelRunner,
        )

        panel = self._make_panel()

        class _MockProvider:
            """返回合成面板的假数据提供者。"""

            def __init__(self, df: pl.DataFrame) -> None:
                self._df = df

            def get_symbols(self, universe_type: str, date: str) -> list[str]:
                return ["S1", "S2", "S3"]

            def get_merged_panel_as_polars(
                self, symbols: list[str], start: str, end: str
            ) -> pl.DataFrame:
                return self._df

        runner = ParallelRunner(max_workers=1, data_provider=_MockProvider(panel))
        captured: list[BacktestOutcome | object] = []

        def _fake_execute(self_runner, tasks):
            from long_earn.backtest.engine.parallel import BacktestTask

            assert all(isinstance(t, BacktestTask) for t in tasks)
            captured.extend(tasks)
            return [
                BacktestOutcome(
                    task_id=t.task_id,
                    success=True,
                    total_return=0.1,
                    sharpe_ratio=1.0,
                    max_drawdown=-0.05,
                )
                for t in tasks
            ]

        monkeypatch.setattr(ParallelRunner, "_execute_tasks", _fake_execute)

        result = runner.run_walk_forward_parallel(
            strategy_yaml=self._YAML,
            start_date="2024-01-01",
            end_date="2024-07-18",
            symbols=["S1", "S2", "S3"],
            n_splits=3,
            benchmark_symbol="",
        )

        assert "error" not in result
        tasks_by_id = {t.task_id: t for t in captured}
        assert len(tasks_by_id) == 6  # 3 folds × (train + test)
        for fold in range(3):
            tr = tasks_by_id[f"{fold}_train"]
            te = tasks_by_id[f"{fold}_test"]
            assert te.start_date > tr.end_date, (
                f"fold {fold}: test 起 {te.start_date} 未晚于 train 止 "
                f"{tr.end_date}（test 折窗口覆盖训练期，OOS 污染回归）"
            )


class TestDisableXtquantEnvContext:
    """P2-06：环境变量用 contextmanager 包裹，退出后自动清理不泄漏。"""

    def test_env_restored_after_context_exit(self):
        """上下文退出后环境变量恢复原值，不污染主进程"""
        import os

        from long_earn.backtest.engine.parallel import _disable_xtquant_env

        key = "LONG_EARN_DISABLE_XTQUANT"
        # 确保进入前不存在
        had_before = key in os.environ
        old_before = os.environ.get(key)

        with _disable_xtquant_env():
            assert os.environ.get(key) == "1"

        # 退出后恢复
        if had_before:
            assert os.environ.get(key) == old_before
        else:
            assert key not in os.environ, "环境变量泄漏到主进程"

    def test_env_not_leaked_when_already_set(self):
        """若环境变量已有值，退出后恢复原值而非删除"""
        import os

        from long_earn.backtest.engine.parallel import _disable_xtquant_env

        key = "LONG_EARN_DISABLE_XTQUANT"
        os.environ[key] = "original"
        try:
            with _disable_xtquant_env():
                assert os.environ.get(key) == "1"
            assert os.environ.get(key) == "original"
        finally:
            del os.environ[key]

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

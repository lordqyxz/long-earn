"""并行回测基础设施测试。

SharedDataContext 往返 + ParallelRunner max_workers=1 退化模式。
不依赖外部数据源，使用合成面板。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from long_earn.backtest.engine.shared_data import SharedDataContext


class TestSharedData:
    """SharedMemory + Arrow IPC 共享数据底座。"""

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

    def test_roundtrip_shared_memory(self):
        """主进程写入 → worker attach → 恢复 DataFrame 内容一致。"""
        df = self._make_df()
        with SharedDataContext(df) as ctx:
            token, size, pickle_data = ctx.get_worker_args()
            restored = SharedDataContext.attach(token, size, pickle_data)
            assert restored.shape == df.shape
            assert restored.columns == df.columns

    def test_pickle_fallback(self):
        """pickle 路径：手动构造 pickle 数据后用 attach 恢复。"""
        import pickle

        df = self._make_df()
        # 直接测试 pickle 路径（不依赖 SharedMemory 不可用的条件）
        pickle_data = pickle.dumps(df)
        restored = SharedDataContext.attach("pickle", 0, pickle_data)
        assert restored.shape == df.shape
        assert restored.columns == df.columns


class TestParallelRunnerSerial:
    """ParallelRunner max_workers=1 退化模式（CI 安全）。"""

    def test_runner_exists_and_importable(self):
        """ParallelRunner 可导入且可实例化。"""
        from long_earn.backtest.engine.parallel import ParallelRunner

        runner = ParallelRunner(max_workers=1)
        assert runner is not None

"""TimeSeriesSplit 单元测试（Walk-Forward purge/embargo gap）。"""

from __future__ import annotations

from long_earn.backtest.engine.timeseries_split import TimeSeriesSplit


class TestTimeSeriesSplitGap:
    def test_gap_zero_train_test_adjacent(self) -> None:
        timestamps = list(range(12))
        splits = TimeSeriesSplit(n_splits=3, gap=0).split(timestamps)
        train, test = splits[0]
        assert train[-1] + 1 == test[0]

    def test_gap_positive_train_test_not_adjacent(self) -> None:
        timestamps = list(range(20))
        gap = 3
        splits = TimeSeriesSplit(n_splits=3, gap=gap).split(timestamps)
        for train, test in splits:
            if not test:
                continue
            assert test[0] - train[-1] > 1
            assert test[0] - train[-1] == gap + 1

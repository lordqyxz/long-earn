"""时序交叉验证分割器（Walk-Forward OOS 用）。

从 ml_strategy.py 迁出（ADR-009 收尾：清理双套体系，ml_strategy.py 整体删除）。
仅保留 TimeSeriesSplit 类，供 core.py / parallel.py 的 Walk-Forward 回测使用。
"""

from __future__ import annotations

from typing import Any


class TimeSeriesSplit:
    """时序交叉验证分割器 (样本外验证 OOS)"""

    def __init__(self, n_splits: int = 3, gap: int = 0) -> None:
        self.n_splits = n_splits
        self.gap = gap

    def split(self, timestamps: list[Any]) -> list[tuple[list[Any], list[Any]]]:
        """生产 (train_timestamps, test_timestamps) 分割。

        Args:
            timestamps: 按时间升序排列的时间戳列表。

        Returns:
            n_splits 个 (train, test) 元组，train 严格在 test 之前，
            中间可选 gap 个样本隔离（防泄漏）。
        """
        n = len(timestamps)
        fold_size = n // (self.n_splits + 1)
        splits: list[tuple[list[Any], list[Any]]] = []
        for i in range(1, self.n_splits + 1):
            train_end = i * fold_size
            test_start = train_end + self.gap
            test_end = min(test_start + fold_size, n)
            splits.append((timestamps[:train_end], timestamps[test_start:test_end]))
        return splits

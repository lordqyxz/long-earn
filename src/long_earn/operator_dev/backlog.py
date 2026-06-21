"""算子缺口 backlog —— 待开发算子的优先级队列。

默认内存实现（进程内、测试友好）；可子类化为文件/DuckDB 持久化。策略研发
gap_detector 产出 spec 写入 backlog；算子开发子图 pick_task 按优先级取出消费。
"""

from __future__ import annotations

import threading
from collections import deque

from long_earn.operator_dev.spec import OperatorSpec, OperatorSpecPriority

# 优先级权重：HIGH 先于 NORMAL 先于 LOW
_PRIORITY_ORDER = {
    OperatorSpecPriority.HIGH: 0,
    OperatorSpecPriority.NORMAL: 1,
    OperatorSpecPriority.LOW: 2,
}


class OperatorBacklog:
    """线程安全的算子缺口 backlog（内存版）。

    按 priority 分桶，桶内 FIFO；跨桶按 HIGH→NORMAL→LOW 取。同名 spec 不重复入队。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[int, deque[OperatorSpec]] = {
            0: deque(),
            1: deque(),
            2: deque(),
        }
        self._by_name: dict[str, OperatorSpec] = {}

    def submit(self, spec: OperatorSpec) -> bool:
        """投递一个 spec。同名已存在（任意状态）则忽略，返回 False。"""
        with self._lock:
            if spec.name in self._by_name:
                return False
            self._by_name[spec.name] = spec
            self._buckets[_PRIORITY_ORDER[spec.priority]].append(spec)
            return True

    def pick_next(self) -> OperatorSpec | None:
        """取下一个 pending 的 spec；无则返回 None。已取出但仍 pending。"""
        with self._lock:
            for order in (0, 1, 2):
                bucket = self._buckets[order]
                for spec in bucket:
                    if spec.status == "pending":
                        spec.status = "in_progress"
                        return spec
            return None

    def update_status(self, name: str, status: str) -> bool:
        """更新某 spec 状态：pending | in_progress | resolved | blocked | registered。"""
        with self._lock:
            spec = self._by_name.get(name)
            if spec is None:
                return False
            spec.status = status
            return True

    def get(self, name: str) -> OperatorSpec | None:
        with self._lock:
            return self._by_name.get(name)

    def all_specs(self) -> list[OperatorSpec]:
        with self._lock:
            return list(self._by_name.values())

    def is_empty(self) -> bool:
        with self._lock:
            return all(
                not any(s.status == "pending" for s in bucket)
                for bucket in self._buckets.values()
            )

"""子代理任务运行时（ADR-024 §D）

``run_*`` 长时任务（策略研发 / 股票分析 / 事件采集）经 :class:`TaskRunner`
提交为后台 daemon 线程，主循环立即继续；``query_task(task_id)`` 查询进度
与产物。

契约（ADR-024 §D）：

- 编排决策留在主智能体：后台任务只执行被委托的子图，不派生子任务；
- 失败语义结构化：失败原因与可重试标记随任务状态返回，禁止静默吞异常；
- 任务生命周期事件（提交 / 完成 / 失败）写入日志并关联 task_id。

线程模型：每个任务一个 daemon 线程（进程退出不阻塞），并发数由信号量
封顶；任务状态一经写入即不可变（以整对象交换代替原地变更，读取无撕裂）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from long_earn.master_agent_tools import ToolOutput
    from long_earn.services import LoggerService

# 任务状态（生命周期）
TASK_RUNNING = "running"
TASK_SUCCEEDED = "succeeded"
TASK_FAILED = "failed"

# 缺省并发上限：同时执行的后台任务数（排队任务不受限）
_DEFAULT_MAX_WORKERS = 3


@dataclass(frozen=True)
class TaskState:
    """后台任务状态快照 — 一经写入即不可变。"""

    task_id: str
    kind: str
    status: str
    submitted_at: float
    finished_at: float | None = None
    output: ToolOutput | None = None
    error: str = ""
    retryable: bool = False


class TaskRunner:
    """后台任务执行器 + 注册表（线程安全）。

    用法::

        runner = TaskRunner(logger)
        task_id = runner.submit("run_research", lambda: do_research())
        state = runner.get(task_id)   # 轮询；完成后 output 为结构化产物
    """

    def __init__(
        self,
        logger: LoggerService,
        max_workers: int = _DEFAULT_MAX_WORKERS,
    ) -> None:
        """初始化任务运行时。

        Args:
            logger: 日志服务（任务生命周期事件关联 task_id）
            max_workers: 同时执行的后台任务上限；超出部分排队
        """
        self._logger = logger
        self._semaphore = threading.BoundedSemaphore(max_workers)
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskState] = {}
        self._counter = count(1)

    def submit(self, kind: str, fn: Callable[[], ToolOutput]) -> str:
        """提交后台任务并立即返回 task_id。

        Args:
            kind: 任务类型（即触发它的工具名，如 ``run_research``）
            fn: 任务体；返回结构化输出，异常上抛由本运行时捕获

        Returns:
            task_id（形如 ``task-1``，进程内单调递增）
        """
        with self._lock:
            task_id = f"task-{next(self._counter)}"
            self._tasks[task_id] = TaskState(
                task_id=task_id,
                kind=kind,
                status=TASK_RUNNING,
                submitted_at=time.time(),
            )
        thread = threading.Thread(
            target=self._run,
            args=(task_id, kind, fn),
            name=f"le-task-{task_id}",
            daemon=True,
        )
        thread.start()
        self._logger.info(f"任务提交: {task_id} ({kind})")
        return task_id

    def get(self, task_id: str) -> TaskState | None:
        """查询任务状态；未知 task_id 返回 None。"""
        with self._lock:
            return self._tasks.get(task_id)

    def _run(self, task_id: str, kind: str, fn: Callable[[], ToolOutput]) -> None:
        """任务体：信号量内执行，异常捕获为结构化失败状态。"""
        with self._semaphore:
            try:
                output = fn()
            except Exception as e:
                self._swap(
                    task_id,
                    TaskState(
                        task_id=task_id,
                        kind=kind,
                        status=TASK_FAILED,
                        submitted_at=self._submitted_at(task_id),
                        finished_at=time.time(),
                        error=f"{type(e).__name__}: {e}",
                        retryable=True,
                    ),
                )
                self._logger.error(f"任务失败: {task_id} ({kind}): {e}")
                return
        self._swap(
            task_id,
            TaskState(
                task_id=task_id,
                kind=kind,
                status=TASK_SUCCEEDED,
                submitted_at=self._submitted_at(task_id),
                finished_at=time.time(),
                output=output,
            ),
        )
        self._logger.info(f"任务完成: {task_id} ({kind})")

    def _swap(self, task_id: str, state: TaskState) -> None:
        """以整对象交换更新任务状态（读取侧无撕裂）。"""
        with self._lock:
            self._tasks[task_id] = state

    def _submitted_at(self, task_id: str) -> float:
        """读取任务的提交时间（仅任务线程内部使用）。"""
        with self._lock:
            return self._tasks[task_id].submitted_at

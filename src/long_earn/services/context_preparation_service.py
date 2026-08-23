"""研究与分析上下文准备服务实现。"""

from __future__ import annotations

from collections.abc import Callable

from long_earn.services import LoggerService, MemoryService


class ContextPreparationServiceImpl:
    """协调事件激活与可选的事件采集。

    事件推理由组合根以回调注入，使服务层不依赖具体采集工具或子图实现。
    """

    def __init__(
        self,
        memory: MemoryService,
        logger: LoggerService,
        infer_events: Callable[[str], None] | None = None,
    ) -> None:
        self._memory = memory
        self._logger = logger
        self._infer_events = infer_events

    def prepare(
        self,
        query: str,
        *,
        k: int = 5,
        force_refresh: bool = False,
    ) -> str:
        """激活已有事件，未命中或强制刷新时采集后再次激活。"""
        if not query.strip():
            return ""

        activated = [] if force_refresh else self._activate(query, k, "activate")
        if activated:
            return "\n".join(activated)

        self._infer(query)
        return "\n".join(self._activate(query, k, "二次激活"))

    def _activate(self, query: str, k: int, phase: str) -> list[str]:
        activate_events = self._memory.activate_events
        if not callable(activate_events):
            return []
        try:
            raw = activate_events(query, k=k)
            return [str(item) for item in (raw or [])]
        except Exception as exc:
            self._logger.warning(f"prepare_context {phase}失败: {exc}")
            return []

    def _infer(self, query: str) -> None:
        if self._infer_events is None:
            return
        try:
            self._infer_events(query)
        except Exception as exc:
            self._logger.warning(f"prepare_context 事件推理跳过: {exc}")

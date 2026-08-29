"""研究与分析上下文准备服务实现（ADR-021：纯确定性激活）。"""

from __future__ import annotations

from long_earn.services import ContextActivation, LoggerService, MemoryService


class ContextPreparationServiceImpl:
    """事件/知识子图的确定性激活服务。

    ADR-021：本服务只做检索与激活（确定性脚手架），不内嵌任何 LLM 调用；
    未命中时的采集推理由调用方（agent 节点）显式触发。
    """

    def __init__(self, memory: MemoryService, logger: LoggerService) -> None:
        self._memory = memory
        self._logger = logger

    def prepare(self, query: str, *, k: int = 5) -> ContextActivation:
        """激活与查询相关的事件/知识，返回结构化激活结果。"""
        if not query.strip():
            return ContextActivation()

        activate_events = self._memory.activate_events
        if not callable(activate_events):
            return ContextActivation()

        try:
            raw = activate_events(query, k=k)
            items = tuple(str(item) for item in (raw or []))
        except Exception as exc:
            self._logger.warning(f"prepare_context 激活失败: {exc}")
            return ContextActivation()
        return ContextActivation(items=items)

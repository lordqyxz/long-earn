from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass
class AuditRecord:
    """审计记录的通用数据结构"""

    run_id: str
    timestamp: datetime
    event_type: str
    trace_id: str
    parent_id: str | None
    component: str
    status: str
    payload: dict[str, Any]
    latency_ms: float | None = None


class AuditProvider(Protocol):
    """审计存储提供者接口"""

    def log_event(self, record: AuditRecord) -> None:
        """记录单个审计事件"""
        ...

    def query_events(
        self, run_id: str, filters: dict[str, Any]
    ) -> Sequence[AuditRecord]:
        """根据过滤条件查询审计事件"""
        ...

    def get_causal_chain(self, trace_id: str) -> Sequence[AuditRecord]:
        """获取特定交易的完整因果链条"""
        ...

    def close(self) -> None:
        """释放存储资源"""
        ...

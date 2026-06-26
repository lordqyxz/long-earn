"""算子开发子图状态。

一次 invoke 处理 backlog 中所有 pending spec（外层循环），每个 spec 的内部状态
沿 pick_task → spec_review → implement → test_validate → [refine] → register →
mark_blocked 流转，结果累计在 results 列表。
"""

from __future__ import annotations

from typing import Any, TypedDict

from long_earn.operator_dev.spec import OperatorSpec


class OperatorDevState(TypedDict, total=False):
    """算子开发子图状态。"""

    # 运行态：当前正在处理的 spec（直接存对象，免去 dict 往返转换）
    current_spec: OperatorSpec | None
    refine_count: int
    source_code: str
    code_ready: bool
    failure_report: str

    # 输出
    results: list[dict[str, Any]]  # 每个 spec 的最终结果摘要
    registered_names: list[str]  # 本轮成功注册的算子名

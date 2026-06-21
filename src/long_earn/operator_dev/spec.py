"""算子规约 (OperatorSpec) — 算子开发子图的输入契约。

来源：
- 自动：strategy_rd 子图 gap_detector 节点产出（扫描 improvement_suggestions
  与算子目录的差异）。
- 人工：CLI / dashboard / e2e 测试投递。

强制约束：``reference_strategy`` 非空——validate 节点据此跑"有/无新算子"强制
回测对比，自洽不依赖运行时兜底。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OperatorSpecPriority(StrEnum):
    """算子缺口优先级。"""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class OperatorSpec:
    """一个待开发的算子缺口。

    Attributes:
        name: 目标算子名，如 ``"log_return"``，必须与实现类的 ``Operator.name`` 一致。
        intent: 一句话意图：做什么、解决什么缺口。
        input_fields: 依赖的输入列，如 ``["close"]``。
        category: factor | filter | rank | compose | technical
        expected_output: 语义说明：每行 float / bool / 横截面排名。
        reference_strategy: 触发它的策略 DSL（强制非空，validate 用）。
        motivation: 为什么现有目录满足不了。
        priority: high | normal | low
    """

    name: str
    intent: str
    input_fields: list[str]
    category: str
    expected_output: str
    reference_strategy: str
    motivation: str = ""
    priority: OperatorSpecPriority = OperatorSpecPriority.NORMAL
    # 运行态：实现源码、状态机用，不参与判等
    source_code: str = ""
    status: str = "pending"
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("OperatorSpec.name 不能为空")
        if not self.reference_strategy:
            raise ValueError(
                f"OperatorSpec({self.name}).reference_strategy 必须非空"
                "（validate 节点据此做强制回测对比）"
            )
        if isinstance(self.priority, str):
            self.priority = OperatorSpecPriority(self.priority)

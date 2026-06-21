"""算子开发子图 — 量化算子研发模块

异步闭环：消费算子缺口 (OperatorSpec) → 实现 → 测试（因果性证明）→ 验证
（AST 安全审计 + 契约 + 强制回测对比）→ 注册 → 通知。

设计目标（见 ``plans/new backtest.md``）：
- 策略研发永不阻塞等待算子开发——缺口写进 backlog 就继续当前迭代。
- LLM 代码执行风险收敛在本子图这一个可审查、可关停的子流程内。
- 产物是代码库里的 ``.py`` 文件 + 内存热注册，与人类写的算子无任何区别。

本模块是"量化算子研发"独立模块，对外暴露 :func:`create_operator_dev_subgraph`
与 :class:`OperatorSpec` / :class:`OperatorBacklog`。
"""

from long_earn.operator_dev.agents import (
    FakeImplementer,
    LLMImplementer,
    OperatorImplementer,
)
from long_earn.operator_dev.backlog import OperatorBacklog
from long_earn.operator_dev.sandbox import (
    OperatorLoadError,
    audit_source,
    load_operator_class,
)
from long_earn.operator_dev.spec import OperatorSpec, OperatorSpecPriority
from long_earn.operator_dev.subgraph import create_operator_dev_subgraph

__all__ = [
    "FakeImplementer",
    "LLMImplementer",
    "OperatorBacklog",
    "OperatorImplementer",
    "OperatorLoadError",
    "OperatorSpec",
    "OperatorSpecPriority",
    "audit_source",
    "create_operator_dev_subgraph",
    "load_operator_class",
]

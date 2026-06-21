"""算子开发 Agent —— 实现 / 修复算子源码。

定义 :class:`OperatorImplementer` 协议，便于：
- 生产用 :class:`LLMImplementer`（调 LLM 生成源码）；
- 测试用 :class:`FakeImplementer`（确定性返回预置源码，不依赖真实 LLM）。

LLM 生成源码后必须经 :mod:`long_earn.operator_dev.sandbox` 审计 + 因果性证明才能
注册——本模块只负责"产出源码"，不负责"判定可否上线"。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from long_earn.operator_dev.spec import OperatorSpec

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LLMService


# 算子源码模板：实现一个标准因果因子算子的骨架，供 LLM 参考。
_SOURCE_TEMPLATE_HINT = """\
import polars as pl
from typing import ClassVar
from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class {Name}Params(OperatorParams):
    field: str = "close"
    # TODO: 按 spec 补参数


@operator
class {Name}(Operator):
    name: ClassVar[str] = "{name}"
    category: ClassVar[str] = "{category}"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = {Name}Params
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        # TODO: 实现因果计算（仅回溯历史，禁止 shift(负数) / 读未来）
        ...
"""


class OperatorImplementer(Protocol):
    """算子实现者协议：把 spec 翻译成算子源码字符串。"""

    def implement(self, spec: OperatorSpec) -> str:
        """根据 spec 产出算子 Python 源码（含 @operator 类）。"""
        ...

    def refine(self, spec: OperatorSpec, failure_report: str) -> str:
        """根据失败报告（审计/测试/回测对比）重写源码。"""
        ...


class LLMImplementer:
    """生产实现者：调 LLM 生成算子源码。"""

    def __init__(self, context: RuntimeContext) -> None:
        self.llm: LLMService = context.require_llm()

    def implement(self, spec: OperatorSpec) -> str:
        prompt = self._build_prompt(spec, failure="")
        return self.llm.invoke(prompt).content

    def refine(self, spec: OperatorSpec, failure_report: str) -> str:
        prompt = self._build_prompt(spec, failure=failure_report)
        return self.llm.invoke(prompt).content

    def _build_prompt(self, spec: OperatorSpec, failure: str) -> str:
        hint = _SOURCE_TEMPLATE_HINT.format(
            Name=spec.name.replace("_", " ").title().replace(" ", ""),
            name=spec.name,
            category=spec.category,
        )
        failure_section = f"\n\n## 上次失败报告（请修复）\n{failure}" if failure else ""
        return (
            f"实现一个量化算子。严格只用 polars/numpy/math/long_earn.backtest.*，"
            f"禁止 os/subprocess/eval 等。必须因果（仅回溯历史，禁止读未来）。\n\n"
            f"## 算子规约\n{spec!r}\n\n"
            f"## 源码骨架参考\n```\n{hint}\n```\n"
            f"只输出算子源码，不要解释。{failure_section}"
        )


class FakeImplementer:
    """测试用确定性实现者：按 spec.name 返回预置源码。

    e2e 测试注入本类，使算子开发子图不依赖真实 LLM 即可端到端跑通。
    """

    def __init__(self, sources: dict[str, str] | None = None) -> None:
        # name -> source；refine 时若同 name 有修正版可放 _refined_sources
        self._sources = dict(sources or {})
        self._refined: dict[str, str] = {}
        self.refine_calls: list[tuple[str, str]] = []

    def implement(self, spec: OperatorSpec) -> str:
        if spec.name not in self._sources:
            raise KeyError(f"FakeImplementer 未注册 spec '{spec.name}' 的源码")
        return self._sources[spec.name]

    def refine(self, spec: OperatorSpec, failure_report: str) -> str:
        self.refine_calls.append((spec.name, failure_report))
        # 默认 refine 返回同一份源码；测试可注入修正版
        if spec.name in self._refined:
            return self._refined[spec.name]
        return self._sources.get(spec.name, "")

    def set_refined_source(self, name: str, source: str) -> None:
        """注入 refine 后的修正源码（用于测试修复路径）。"""
        self._refined[name] = source

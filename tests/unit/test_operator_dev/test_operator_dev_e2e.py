"""算子研发模块 (operator_dev) 端到端测试。

用 FakeImplementer 注入确定性算子源码，使子图不依赖真实 LLM 即可端到端跑通：
1. 正向：正确算子 → 经审计 + 契约 + 因果性证明 → 注册上线；
2. 负向：含未来函数 / 危险 import / 契约不符 → 拦截 → blocked，不注册；
3. refine 修复路径：首轮坏源码 → refine 注入正确源码 → 注册成功。
"""

from __future__ import annotations

import pytest

from long_earn.backtest.operators import OPERATOR_REGISTRY, get_operator
from long_earn.operator_dev import (
    FakeImplementer,
    OperatorBacklog,
    OperatorSpec,
    create_operator_dev_subgraph,
)

# 正确因果算子：对数收益 = log(close / shift(close, period))
_GOOD = '''
import polars as pl
from typing import ClassVar
from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class P(OperatorParams):
    field: str = "close"
    period: int = 1


@operator
class log_return(Operator):
    name: ClassVar[str] = "log_return"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = P
    min_history: ClassVar[int] = 1

    def apply(self, panel, params):
        expr = (
            (pl.col(params.field) / pl.col(params.field).shift(params.period))
            .log().over("symbol").alias("log_return")
        )
        return temporal_series(panel, expr)
'''

# 含未来函数：shift(-1) 读未来
_LEAK = '''
import polars as pl
from typing import ClassVar
from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class P(OperatorParams):
    field: str = "close"


@operator
class {name}(Operator):
    name: ClassVar[str] = "{name}"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = P
    min_history: ClassVar[int] = 0

    def apply(self, panel, params):
        return temporal_series(panel, pl.col(params.field).shift(-1).over("symbol").alias("leak"))
'''

# 危险 import：os
_UNSAFE = '''
import os
from typing import ClassVar
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class P(OperatorParams):
    pass


@operator
class unsafe_op(Operator):
    name: ClassVar[str] = "unsafe_op"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = P
    min_history: ClassVar[int] = 0

    def apply(self, panel, params):
        return panel["close"]
'''

# 契约不符：causal=False
_NON_CAUSAL = '''
from typing import ClassVar
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class P(OperatorParams):
    pass


@operator
class non_causal_op(Operator):
    name: ClassVar[str] = "non_causal_op"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = P
    causal: ClassVar[bool] = False
    min_history: ClassVar[int] = 0

    def apply(self, panel, params):
        return panel["close"]
'''

_BLOCKED_OPS = ("future_peek", "unsafe_op", "non_causal_op")
_BLOCKED_SOURCES = {
    "future_peek": _LEAK.format(name="future_peek"),
    "unsafe_op": _UNSAFE,
    "non_causal_op": _NON_CAUSAL,
}


def _spec(name: str) -> OperatorSpec:
    return OperatorSpec(
        name=name, intent="测试算子", input_fields=["close"],
        category="factor", expected_output="每行 float",
        reference_strategy="shift(close,1)",
    )


def _run(name: str, source: str, *, refined: str | None = None) -> dict:
    backlog = OperatorBacklog()
    backlog.submit(_spec(name))
    impl = FakeImplementer({name: source})
    if refined is not None:
        impl.set_refined_source(name, refined)
    return create_operator_dev_subgraph(implementer=impl, backlog=backlog).invoke({})


@pytest.fixture(autouse=True)
def _cleanup_registry():
    yield
    for name in ("log_return", *_BLOCKED_OPS):
        OPERATOR_REGISTRY.pop(name, None)


def _status(result: dict, name: str) -> str:
    return {r["name"]: r["status"] for r in result["results"]}[name]


class TestOperatorDevE2E:
    def test_correct_operator_registered(self):
        result = _run("log_return", _GOOD)
        assert result["registered_names"] == ["log_return"]
        assert type(get_operator("log_return")).category == "factor"

    @pytest.mark.parametrize("name", _BLOCKED_OPS)
    def test_bad_operator_blocked(self, name: str):
        """含未来函数 / 危险 import / 契约不符 → blocked，不注册。"""
        result = _run(name, _BLOCKED_SOURCES[name])
        assert _status(result, name) == "blocked"
        assert name not in OPERATOR_REGISTRY

    def test_non_causal_refines_three_times(self):
        """含未来函数的算子 refine 用尽 3 次仍未通过。"""
        impl = FakeImplementer({"future_peek": _LEAK.format(name="future_peek")})
        backlog = OperatorBacklog()
        backlog.submit(_spec("future_peek"))
        create_operator_dev_subgraph(implementer=impl, backlog=backlog).invoke({})
        assert len(impl.refine_calls) == 3

    def test_refine_recovers_from_failure(self):
        """首轮坏源码 → refine 注入正确源码 → 注册成功。"""
        result = _run(
            "log_return", _LEAK.format(name="log_return"), refined=_GOOD
        )
        assert result["registered_names"] == ["log_return"]
        assert "log_return" in OPERATOR_REGISTRY

    def test_duplicate_spec_deduped(self):
        backlog = OperatorBacklog()
        spec = _spec("log_return")
        assert backlog.submit(spec) is True
        assert backlog.submit(spec) is False

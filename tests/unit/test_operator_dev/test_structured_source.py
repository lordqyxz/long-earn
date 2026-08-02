"""LLMImplementer 结构化 JSON 返回 source_code。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from long_earn.operator_dev.agents import LLMImplementer, OperatorSourceResult

_BARE = """\
import polars as pl
from typing import ClassVar
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class P(OperatorParams):
    field: str = "close"


@operator
class demo_op(Operator):
    name: ClassVar[str] = "demo_op"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = P
    min_history: ClassVar[int] = 0

    def apply(self, panel, params):
        return panel[params.field]
"""


def test_operator_source_result_accepts_clean_code() -> None:
    result = OperatorSourceResult.model_validate({"source_code": _BARE})
    assert "class demo_op" in result.source_code


def test_operator_source_result_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        OperatorSourceResult.model_validate({"source_code": ""})


def test_parse_source_from_json_object() -> None:
    payload = json.dumps({"source_code": _BARE}, ensure_ascii=False)
    source = LLMImplementer._parse_source(payload)
    assert source.startswith("import polars")
    assert "```" not in source


def test_parse_source_from_json_fence_wrapper() -> None:
    """外层偶发 ```json 由 parse_llm_json 处理；字段内仍是纯源码。"""
    inner = json.dumps({"source_code": _BARE}, ensure_ascii=False)
    wrapped = f"```json\n{inner}\n```"
    source = LLMImplementer._parse_source(wrapped)
    assert source.startswith("import polars")

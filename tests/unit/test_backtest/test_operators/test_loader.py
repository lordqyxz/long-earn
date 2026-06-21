"""算子目录加载器 / 契约校验 / 冲突检测测试。"""

from __future__ import annotations

import pytest

from long_earn.backtest.operators import (
    OPERATOR_REGISTRY,
    VALID_CATEGORIES,
    OperatorContractError,
    OperatorNotFoundError,
    get_operator,
    list_operators,
    register_operator,
)
from long_earn.backtest.operators.base import (
    Operator,
    OperatorParams,
    operator,
    validate_contract,
)

EXPECTED_OPS = {
    "shift", "returns", "windowed", "filter_threshold", "rank_top",
    "arithmetic", "sma", "ema", "rsi", "macd", "bollinger",
}


class TestLoader:
    def test_all_expected_operators_registered(self):
        assert set(OPERATOR_REGISTRY) == EXPECTED_OPS

    def test_every_operator_has_valid_category_and_is_causal(self):
        for name, op in OPERATOR_REGISTRY.items():
            cls = type(op)
            assert cls.category in VALID_CATEGORIES, name
            assert cls.causal is True, f"{name} 非因果"

    def test_get_operator_returns_instance(self):
        op = get_operator("shift")
        assert isinstance(op, Operator)
        assert type(op).name == "shift"

    def test_get_operator_unknown_raises(self):
        with pytest.raises(OperatorNotFoundError):
            get_operator("does_not_exist")

    def test_list_operators_schema(self):
        entry = list_operators()["shift"]
        assert set(entry) == {"category", "inputs", "params_schema", "min_history"}
        assert entry["category"] == "factor"
        assert entry["params_schema"]["type"] == "object"


class TestContractValidation:
    """契约校验：缺字段 / 非法值一律拒。用参数化避免重复样板。"""

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"name": "", "category": "factor"}, "name"),
            ({"name": "x", "category": "nonsense"}, "category"),
            ({"name": "x", "category": "factor", "inputs": "close"}, "inputs"),
            ({"name": "x", "category": "factor", "params_cls": dict}, "params_cls"),
            ({"name": "x", "category": "factor", "causal": False}, "因果"),
            ({"name": "x", "category": "factor", "min_history": -1}, "min_history"),
        ],
    )
    def test_contract_violation_rejected(self, kwargs, match):
        class Bad(Operator):
            params_cls = OperatorParams

            def apply(self, panel, params):  # type: ignore[no-untyped-def]
                ...

        for k, v in kwargs.items():
            setattr(Bad, k, v)
        with pytest.raises(OperatorContractError, match=match):
            validate_contract(Bad)


class TestHotRegister:
    def test_register_operator_makes_op_available(self):
        class P(OperatorParams):
            v: int = 0

        @operator
        class Tmp(Operator):
            name = "_tmp_test_op"
            category = "factor"
            params_cls = P

            def apply(self, panel, params):  # type: ignore[no-untyped-def]
                import polars as pl

                return pl.Series("tmp", [0.0] * panel.height)

        try:
            register_operator(Tmp())
            assert get_operator("_tmp_test_op").name == "_tmp_test_op"
        finally:
            OPERATOR_REGISTRY.pop("_tmp_test_op", None)

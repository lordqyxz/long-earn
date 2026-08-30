"""算子目录加载器 / 契约校验 / 冲突检测测试。"""

from __future__ import annotations

import polars as pl
import pytest

from long_earn.backtest.operators import (
    OPERATOR_REGISTRY,
    PROOF_REGISTRY,
    VALID_CATEGORIES,
    OperatorContractError,
    OperatorNotFoundError,
    get_operator,
    get_operator_proof,
    list_operators,
    register_operator,
)
from long_earn.backtest.operators.base import (
    Operator,
    OperatorParams,
    operator,
    validate_contract,
)
from long_earn.backtest.operators.causality import prove_registration_causality

EXPECTED_OPS = {
    "shift",
    "returns",
    "windowed",
    "filter_threshold",
    "rank_top",
    "arithmetic",
    "sma",
    "ema",
    "rsi",
    "macd",
    "bollinger",
    # operator_dev 自主研发写盘算子（htr_subgraph 接入后由 LLM 生成）
    "log_return",
    "realized_vol",
    # operator_dev 新增算子
    "price_stability",
    "return_quality",
    "lowvol_momentum_combo",
    "quality_momentum",
    "e2e_volatility",
}


class TestLoader:
    def test_all_expected_operators_registered(self):
        assert set(OPERATOR_REGISTRY) == EXPECTED_OPS

    def test_every_operator_has_valid_category_and_is_causal(self):
        for name, op in OPERATOR_REGISTRY.items():
            cls = type(op)
            assert cls.category in VALID_CATEGORIES, name
            assert cls.causal is True, f"{name} 非因果"

    def test_all_registered_operators_have_retained_proofs(self):
        """AUDIT-P3-10：每个注册算子必须附带有效的 prove_causality 报告。"""
        assert OPERATOR_REGISTRY, "注册表不应为空（自检环境）"
        for name in OPERATOR_REGISTRY:
            proof = get_operator_proof(name)
            assert proof is not None, f"{name} 缺少 prove_causality 报告"
            assert proof.implementation_hash, f"{name} 报告缺实现指纹"
            assert proof.parameter_hashes, f"{name} 报告未覆盖参数"

    def test_get_operator_returns_instance(self):
        op = get_operator("shift")
        assert isinstance(op, Operator)
        assert type(op).name == "shift"

    def test_get_operator_unknown_raises(self):
        with pytest.raises(OperatorNotFoundError):
            get_operator("does_not_exist")

    def test_get_operator_renamed_raises_with_hint(self):
        """旧名须提示新名，强制 YAML 迁移（不静默别名）。"""
        from long_earn.backtest.operators._loader import OPERATOR_RENAMES

        for old, new in OPERATOR_RENAMES.items():
            with pytest.raises(OperatorNotFoundError, match=new):
                get_operator(old)

    def test_list_operators_schema(self):
        entry = list_operators()["shift"]
        assert set(entry) == {
            "category",
            "inputs",
            "field_params",
            "params_schema",
            "min_history",
        }
        assert entry["category"] == "factor"
        assert entry["params_schema"]["type"] == "object"
        assert entry["field_params"] == ["field"]


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
            # 注册成功即自动产出并留存 prove_causality 证明（AUDIT-P3-10）
            assert get_operator_proof("_tmp_test_op") is not None
        finally:
            OPERATOR_REGISTRY.pop("_tmp_test_op", None)
            PROOF_REGISTRY.pop("_tmp_test_op", None)

    def test_register_operator_with_explicit_proof_succeeds(self):
        """带证明注册：operator_dev 复用验证节点产出的 CausalityProof 也能成功。"""

        @operator
        class Tmp(Operator):
            name = "_tmp_proof_op"
            category = "factor"
            params_cls = OperatorParams

            def apply(self, panel, params):  # type: ignore[no-untyped-def]
                import polars as pl

                return pl.Series("tmp", [0.0] * panel.height)

        op = Tmp()
        proof = prove_registration_causality(op)
        try:
            register_operator(op, causality_proof=proof)
            assert get_operator("_tmp_proof_op").name == "_tmp_proof_op"
            # 传入的证明对象原样留存，作为"注册附带 prove_causality 报告"的可审计事实
            assert get_operator_proof("_tmp_proof_op") is proof
        finally:
            OPERATOR_REGISTRY.pop("_tmp_proof_op", None)
            PROOF_REGISTRY.pop("_tmp_proof_op", None)

    def test_register_operator_rejects_future_leak_despite_causal_flag(self):
        """直接注册不能靠默认 causal=True 绕过数值因果门。"""

        @operator
        class FutureLeak(Operator):
            name = "_tmp_future_leak"
            category = "factor"
            params_cls = OperatorParams

            def apply(self, panel, params):  # type: ignore[no-untyped-def]
                return panel["close"].shift(-1)

        with pytest.raises(OperatorContractError, match="因果性注册证明失败"):
            register_operator(FutureLeak())
        # 注册失败：两个注册表都不得被污染
        assert "_tmp_future_leak" not in OPERATOR_REGISTRY
        assert "_tmp_future_leak" not in PROOF_REGISTRY

    def test_directory_registration_rejects_future_leak(self):
        """启动目录扫描共用同一个 fail-closed 注册门。"""
        from long_earn.backtest.operators._loader import _register_class

        @operator
        class FutureLeak(Operator):
            name = "_tmp_directory_future_leak"
            category = "factor"
            params_cls = OperatorParams

            def apply(self, panel, params):  # type: ignore[no-untyped-def]
                return pl.Series("leak", panel["close"].shift(-1))

        with pytest.raises(OperatorContractError, match="因果性注册证明失败"):
            _register_class(FutureLeak)
        assert "_tmp_directory_future_leak" not in OPERATOR_REGISTRY
        assert "_tmp_directory_future_leak" not in PROOF_REGISTRY

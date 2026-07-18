"""算子 required_fields 契约测试 — ADR-014 任务3。

验证 ``Operator.required_fields(params)`` 正确合并静态 inputs + 参数驱动字段，
供连接器按需取数。
"""

from __future__ import annotations

from long_earn.backtest.operators.factor.shift import Shift, ShiftParams
from long_earn.backtest.operators.technical.sma_ema import SMA, SMAParams
from long_earn.backtest.operators.compose.arithmetic import (
    Arithmetic,
    ArithmeticParams,
)


class TestRequiredFields:
    """required_fields 合并逻辑测试。"""

    def test_shift_field_params_resolved(self) -> None:
        """shift 算子：params.field="close" → required_fields 含 close。"""
        params = ShiftParams(field="close", periods=5)
        assert Shift.field_params == ["field"]
        fields = Shift.required_fields(params)
        assert "close" in fields

    def test_shift_different_field(self) -> None:
        """shift 算子：params.field="volume" → required_fields 含 volume。"""
        params = ShiftParams(field="volume", periods=3)
        fields = Shift.required_fields(params)
        assert "volume" in fields
        assert "close" not in fields  # 静态 inputs 为空

    def test_sma_field_params(self) -> None:
        """SMA 算子：params.field="high" → required_fields 含 high。"""
        params = SMAParams(field="high", window=10)
        assert SMA.field_params == ["field"]
        fields = SMA.required_fields(params)
        assert "high" in fields

    def test_arithmetic_lhs_rhs(self) -> None:
        """Arithmetic 算子：lhs/rhs 列名 → required_fields 含两者。"""
        params = ArithmeticParams(lhs="close", rhs="open", op="/", alias="ratio")
        assert Arithmetic.field_params == ["lhs", "rhs"]
        fields = Arithmetic.required_fields(params)
        assert "close" in fields
        assert "open" in fields

    def test_arithmetic_both_columns(self) -> None:
        """Arithmetic 算子：lhs/rhs 都是列名 → required_fields 含两者。"""
        params = ArithmeticParams(lhs="close", rhs="volume", op="*", alias="dollar_vol")
        fields = Arithmetic.required_fields(params)
        assert "close" in fields
        assert "volume" in fields

    def test_no_params_returns_static_inputs(self) -> None:
        """params=None 时返回静态 inputs（field_params 无法解析）。"""
        fields = Shift.required_fields(None)
        assert fields == list(Shift.inputs)

    def test_list_operators_exposes_field_params(self) -> None:
        """list_operators() 暴露 field_params 键。"""
        from long_earn.backtest.operators._loader import list_operators

        catalog = list_operators()
        assert "shift" in catalog
        entry = catalog["shift"]
        assert "field_params" in entry
        assert entry["field_params"] == ["field"]
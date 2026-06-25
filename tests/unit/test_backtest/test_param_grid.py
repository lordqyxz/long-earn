"""param_grid.py 参数网格测试"""

from long_earn.backtest.engine.dsl import parse_strategy_yaml
from long_earn.backtest.engine.param_grid import (
    ParamGrid,
    apply_struct_params,
    render_template,
)


class TestRenderTemplate:
    def test_scalar_interpolation(self):
        template = "name: ${strategy_name}\nstop_loss: ${stop_loss}"
        result = render_template(
            template, {"strategy_name": "Momentum", "stop_loss": 0.1}
        )
        assert "Momentum" in result
        assert "0.1" in result

    def test_missing_safe(self):
        template = "name: ${name}\nthreshold: ${threshold}"
        result = render_template(template, {"name": "Test"})
        assert "${threshold}" in result

    def test_code_braces_not_affected(self):
        template = 'factors:\n  score: "close / shift(close, ${lookback}) - 1"'
        result = render_template(template, {"lookback": 20})
        assert "shift(close, 20)" in result


class TestApplyStructParams:
    def test_top_level_field(self):
        dsl = parse_strategy_yaml(
            "name: Test\nuniverse:\n  type: csi300\n"
        )
        result = apply_struct_params(dsl, {"name": "NewName"})
        assert result.name == "NewName"

    def test_nested_field(self):
        dsl = parse_strategy_yaml(
            "name: Test\nuniverse:\n  type: csi300\nrisk_control:\n  stop_loss: 0.15\n"
        )
        result = apply_struct_params(
            dsl, {"risk_control.stop_loss": 0.08}
        )
        assert result.risk_control.stop_loss == 0.08

    def test_no_params_returns_copy(self):
        dsl = parse_strategy_yaml("name: Test\n")
        result = apply_struct_params(dsl, {})
        assert result.name == "Test"
        assert result is not dsl


class TestParamGrid:
    def test_cartesian_count(self):
        grid = ParamGrid(
            scalars={"threshold": [0.1, 0.2], "lookback": [10, 20, 30]},
        )
        assert grid.total_combinations == 6

    def test_expand_scalars(self):
        grid = ParamGrid(
            scalars={"a": [1, 2], "b": ["x", "y"]},
        )
        combos = grid.expand_scalars()
        assert len(combos) == 4
        assert {"a": 1, "b": "x"} in combos
        assert {"a": 2, "b": "y"} in combos

    def test_expand_all(self):
        grid = ParamGrid(
            scalars={"s": [1, 2]},
            structs={"name": ["A", "B"]},
        )
        pairs = grid.expand_all()
        assert len(pairs) == 4
        s_vals = [p[0]["s"] for p in pairs]
        assert set(s_vals) == {1, 2}

    def test_empty_grid(self):
        grid = ParamGrid()
        assert grid.total_combinations == 1
        assert grid.expand_all() == [({}, {})]

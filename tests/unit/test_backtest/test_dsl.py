"""YAML DSL 解析器测试"""

import pytest

from long_earn.backtest.engine.dsl import parse_strategy_yaml

SIMPLE_YAML = """strategy:
  name: TestStrategy
  description: 测试策略
  universe:
    type: csi300
    rebalance_freq: 20D
  start_date: 2024-01-01
  end_date: 2024-03-31
  signals:
    - type: filter
      condition: close > 0
  weights:
    method: equal
"""

FULL_YAML = """strategy:
  name: FullStrategy
  description: 完整策略
  universe:
    type: csi300
  start_date: 2024-01-01
  end_date: 2024-03-31
  factors:
    momentum: close / shift(close, 20) - 1
    value_factor: 1 / roe
  signals:
    - type: filter
      condition: momentum > 0
    - type: rank
      by: momentum
      ascending: false
      top: 5
  weights:
    method: custom_formula
    formula: momentum
  risk_control:
    max_position_per_stock: 0.2
"""


class TestParseStrategyYaml:
    def test_parse_simple(self):
        strategy = parse_strategy_yaml(SIMPLE_YAML)
        assert strategy.name == "TestStrategy"
        assert strategy.universe.type == "csi300"
        assert strategy.weights.method == "equal"
        assert len(strategy.signals) == 1

    def test_parse_full(self):
        strategy = parse_strategy_yaml(FULL_YAML)
        assert strategy.name == "FullStrategy"
        assert len(strategy.factors) == 2
        assert strategy.factors["momentum"] == "close / shift(close, 20) - 1"
        assert len(strategy.signals) == 2
        assert strategy.weights.method == "custom_formula"
        assert strategy.risk_control.max_position_per_stock == 0.2

    def test_parse_empty_raises(self):
        with pytest.raises(ValueError, match="YAML 内容为空"):
            parse_strategy_yaml("")

    def test_signal_missing_type_raises(self):
        yaml = """strategy:
  name: T
  universe:
    type: csi300
  signals:
    - condition: close > 0
  weights:
    method: equal
"""
        with pytest.raises(ValueError, match="缺少 type 字段"):
            parse_strategy_yaml(yaml)


class TestExtractFieldNames:
    """字段提取函数排除 Python 关键字 / evaluator 内置函数"""

    def test_python_constants_excluded(self):
        """True/False/None 是 Python 常量，evaluator 走 ast.Constant，不应被当字段"""
        from long_earn.backtest.engine.dsl import _extract_field_names

        result = _extract_field_names("close > 0 if revenue_yoy > 0 else None")
        # 真实字段：close / revenue_yoy；不应含 if / else / None
        assert result == {"close", "revenue_yoy"}, f"got {result}"

    def test_logical_keywords_excluded(self):
        """and / or / not / in / is 是 Python 逻辑关键字"""
        from long_earn.backtest.engine.dsl import _extract_field_names

        result = _extract_field_names("close > 0 and revenue_yoy > 0 or not low")
        assert result == {"close", "revenue_yoy", "low"}, f"got {result}"

    def test_safe_functions_excluded(self):
        """SafeExpressionEvaluator 内置函数（clip/log/exp/sqrt/where）不被当字段"""
        from long_earn.backtest.engine.dsl import _extract_field_names

        result = _extract_field_names("clip(log(close), 0, 1) + sqrt(volume)")
        assert result == {"close", "volume"}, f"got {result}"

    def test_validate_fields_accepts_ternary_expression(self):
        """validate_fields 不应把 if/else 当成 missing 字段"""
        from long_earn.backtest.engine.dsl import (
            StrategyDSL,
            validate_fields,
        )

        strategy = StrategyDSL.model_validate({
            "name": "Test",
            "factors": {
                # IfExp 三元表达式：evaluator 原生支持
                "alpha": "close if volume > 1000 else low",
            },
            "signals": [
                {"type": "rank", "by": "alpha", "top": 10},
            ],
            "weights": {"method": "equal"},
        })

        missing = validate_fields(strategy, ["close", "low", "volume"])
        # alpha 是 factor 别名 + close/low/volume 是 available → missing 应空
        assert missing == [], f"got {missing}"

    def test_validate_fields_still_catches_real_missing(self):
        """真正缺失的字段仍要被报告"""
        from long_earn.backtest.engine.dsl import (
            StrategyDSL,
            validate_fields,
        )

        strategy = StrategyDSL.model_validate({
            "name": "Test",
            "factors": {"alpha": "close * unknown_field"},
            "signals": [{"type": "rank", "by": "alpha", "top": 10}],
            "weights": {"method": "equal"},
        })

        missing = validate_fields(strategy, ["close"])
        assert missing == ["unknown_field"]

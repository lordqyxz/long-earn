"""YAML DSL 解析器测试"""

import pytest

from long_earn.backtest.engine.dsl import (
    _extract_field_names,
    parse_strategy_yaml,
    validate_fields,
)

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
    """YAML 策略解析测试"""

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

    def test_parse_with_top_level_key(self):
        """带 strategy 键"""
        yaml_with_key = "strategy:\n  name: Foo\n  universe:\n    type: csi300\n  weights:\n    method: equal"
        strategy = parse_strategy_yaml(yaml_with_key)
        assert strategy.name == "Foo"

    def test_parse_empty_raises(self):
        with pytest.raises(ValueError, match="YAML 内容为空"):
            parse_strategy_yaml("")

    def test_parse_invalid_yaml_raises(self):
        with pytest.raises(ValueError, match="YAML 解析失败"):
            parse_strategy_yaml("@@@invalid@@@")


class TestParseStrategyYamlValidation:
    """YAML 策略参数校验测试"""

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

    def test_filter_missing_condition_raises(self):
        yaml = """strategy:
  name: T
  universe:
    type: csi300
  signals:
    - type: filter
  weights:
    method: equal
"""
        with pytest.raises(ValueError, match="缺少 condition 字段"):
            parse_strategy_yaml(yaml)

    def test_rank_missing_by_raises(self):
        yaml = """strategy:
  name: T
  universe:
    type: csi300
  signals:
    - type: rank
  weights:
    method: equal
"""
        with pytest.raises(ValueError, match="缺少 by 字段"):
            parse_strategy_yaml(yaml)

    def test_model_validate_failure(self):
        yaml = """strategy:
  name: T
  universe: not_a_dict
  signals:
    - type: filter
      condition: close > 0
  weights:
    method: equal
"""
        with pytest.raises(ValueError, match="策略参数校验失败"):
            parse_strategy_yaml(yaml)


class TestValidateFields:
    """字段校验测试"""

    def test_all_fields_valid(self):
        strategy = parse_strategy_yaml(FULL_YAML)
        available = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "roe",
            "momentum",
            "value_factor",
        ]
        missing = validate_fields(strategy, available)
        assert missing == []

    def test_missing_field_detected(self):
        strategy = parse_strategy_yaml(FULL_YAML)
        available = ["open", "close", "volume"]
        missing = validate_fields(strategy, available)
        assert "roe" in missing, f"Expected 'roe' in missing, got {missing}"

    def test_factor_alias_covers_field(self):
        yaml = """strategy:
  name: T
  universe:
    type: csi300
  factors:
    my_close: close
  signals:
    - type: filter
      condition: my_close > 0
  weights:
    method: equal
"""
        strategy = parse_strategy_yaml(yaml)
        available = ["open", "close"]
        missing = validate_fields(strategy, available)
        assert missing == []

    def test_expression_signal_type(self):
        yaml = """strategy:
  name: T
  universe:
    type: csi300
  signals:
    - type: expression
      formula: close * roe
      alias: score
  weights:
    method: equal
"""
        strategy = parse_strategy_yaml(yaml)
        available = ["close", "roe"]
        missing = validate_fields(strategy, available)
        assert missing == []

    def test_weight_signal_field(self):
        yaml = """strategy:
  name: T
  universe:
    type: csi300
  signals:
    - type: filter
      condition: close > 0
  weights:
    method: signal
    signal_field: momentum
"""
        strategy = parse_strategy_yaml(yaml)
        available = ["close"]
        missing = validate_fields(strategy, available)
        assert "momentum" in missing


class TestExtractFieldNames:
    """表达式字段提取测试"""

    def test_simple_field(self):
        fields = _extract_field_names("close > 0")
        assert "close" in fields

    def test_shift_expression(self):
        fields = _extract_field_names("close / shift(close, 20) - 1")
        assert "close" in fields
        assert "shift" not in fields

    def test_compound(self):
        fields = _extract_field_names("roe > 0.1 and net_profit_yoy > 0.2")
        assert "roe" in fields
        assert "net_profit_yoy" in fields

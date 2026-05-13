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

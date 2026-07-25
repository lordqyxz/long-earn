"""YAML DSL 解析器测试（ADR-009 收尾：仅算子目录路径）"""

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
  operator_factors:
    - op: returns
      alias: momentum
      params:
        field: close
        period: 20
  signals:
    - type: operator
      op: filter_threshold
      params:
        field: momentum
        op: ">"
        value: 0
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
  operator_factors:
    - op: returns
      alias: momentum
      params:
        field: close
        period: 20
    - op: log_return
      alias: log_ret
      params:
        field: close
        period: 5
  signals:
    - type: operator
      op: filter_threshold
      params:
        field: momentum
        op: ">"
        value: 0
    - type: operator
      op: rank_top
      params:
        field: momentum
        top: 5
        ascending: false
  weights:
    method: equal
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
        assert strategy.has_operator_steps() is True

    def test_parse_full(self):
        strategy = parse_strategy_yaml(FULL_YAML)
        assert strategy.name == "FullStrategy"
        assert len(strategy.operator_factors) == 2
        assert strategy.operator_factors[0]["op"] == "returns"
        assert len(strategy.signals) == 2
        assert strategy.has_operator_steps() is True
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
    - op: filter_threshold
      params:
        field: close
        op: ">"
        value: 0
  weights:
    method: equal
"""
        with pytest.raises(ValueError, match="缺少 type 字段"):
            parse_strategy_yaml(yaml)

    def test_reject_legacy_factors(self):
        """ADR-009 收尾：旧式 factors 字段应被拒。"""
        legacy_yaml = """strategy:
  name: Legacy
  factors:
    momentum: close / shift(close, 20) - 1
  signals:
    - type: filter
      condition: momentum > 0
"""
        with pytest.raises(ValueError, match="factors"):
            parse_strategy_yaml(legacy_yaml)

    def test_reject_legacy_signal_type(self):
        """ADR-009 收尾：旧式 filter/rank/expression 信号应被拒。"""
        legacy_yaml = """strategy:
  name: Legacy
  signals:
    - type: filter
      condition: close > 0
"""
        with pytest.raises(ValueError, match="type='filter'"):
            parse_strategy_yaml(legacy_yaml)

    def test_reject_legacy_weights_method(self):
        """ADR-009 收尾：旧式 custom_formula/signal 权重应被拒。"""
        legacy_yaml = """strategy:
  name: Legacy
  operator_factors:
    - op: returns
      alias: momentum
      params:
        field: close
        period: 5
  signals:
    - type: operator
      op: filter_threshold
      params:
        field: momentum
        op: ">"
        value: 0
  weights:
    method: custom_formula
    formula: momentum
"""
        with pytest.raises(ValueError, match=r"weights\.method"):
            parse_strategy_yaml(legacy_yaml)

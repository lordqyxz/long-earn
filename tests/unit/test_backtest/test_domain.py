"""领域实体与异常测试"""

import pytest

from long_earn.backtest.domain.entities import DateRange, PerformanceMetrics, Portfolio
from long_earn.backtest.domain.exceptions import StrategyValidationError


class TestDateRange:
    """日期范围值对象测试"""

    def test_creation(self):
        dr = DateRange("2024-01-01", "2024-03-31")
        assert dr.start == "2024-01-01"
        assert dr.end == "2024-03-31"

    def test_immutable(self):
        dr = DateRange("2024-01-01", "2024-03-31")
        with pytest.raises(Exception, match="cannot assign to field"):
            dr.start = "2024-02-01"  # type: ignore[misc]

    def test_end_before_start_raises(self):
        with pytest.raises(StrategyValidationError, match="不能晚于"):
            DateRange("2024-03-31", "2024-01-01")

    def test_equal_dates_ok(self):
        dr = DateRange("2024-01-01", "2024-01-01")
        assert dr.start == dr.end

    def test_str(self):
        dr = DateRange("2024-01-01", "2024-03-31")
        assert str(dr) == "2024-01-01 ~ 2024-03-31"


class TestPerformanceMetrics:
    """绩效指标值对象测试"""

    def test_defaults(self):
        m = PerformanceMetrics()
        assert m.total_return == 0.0
        assert m.sharpe_ratio == 0.0
        assert not m.is_profitable
        assert not m.is_risk_adjusted_good

    def test_profitable(self):
        m = PerformanceMetrics(total_return=0.15)
        assert m.is_profitable

    def test_risk_adjusted_good(self):
        m = PerformanceMetrics(total_return=0.3, sharpe_ratio=1.5, max_drawdown=0.15)
        assert m.is_profitable
        assert m.is_risk_adjusted_good

    def test_high_drawdown_not_good(self):
        m = PerformanceMetrics(total_return=0.3, sharpe_ratio=1.5, max_drawdown=0.5)
        assert m.is_profitable
        assert not m.is_risk_adjusted_good


class TestPortfolio:
    """投资组合实体测试"""

    def test_initial_state(self):
        p = Portfolio()
        assert p.cash == 1_000_000.0
        assert p.total_value == 1_000_000.0
        assert p.position_count == 0

    def test_rebalance(self):
        p = Portfolio()
        p.rebalance(
            weights={"000001": 0.6, "000002": 0.4},
            prices={"000001": 10.0, "000002": 20.0},
        )
        assert p.position_count == 2
        assert "000001" in p.positions
        assert "000002" in p.positions

    def test_rebalance_ignores_missing_price(self):
        p = Portfolio()
        p.rebalance(
            weights={"000001": 0.6, "000999": 0.4},
            prices={"000001": 10.0},
        )
        assert p.position_count == 1
        assert "000001" in p.positions
        assert "000999" not in p.positions

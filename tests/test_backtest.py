


from long_earn.tools.backtest import run_backtest


def test_backtest():
    """测试回测机制"""
    # 这里假设run_backtest已经实现
    # 测试基本功能
    strategy_code = """# 策略代码
from qlib import init
from qlib.data import D
from qlib.strategy import Strategy

class CustomStrategy(Strategy):
    def __init__(self):
        super().__init__()
    
    def generate_signals(self, date):
        # 策略逻辑
        return {}
"""

    result = run_backtest(strategy_code)
    assert result is not None
    assert "return" in result

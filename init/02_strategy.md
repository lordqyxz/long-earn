# Qlib 策略基类

## BaseStrategy 基础策略类

```python
from qlib.strategy import BaseStrategy
import pandas as pd

class MyStrategy(BaseStrategy):
    def __init__(self, trade_exchange=None):
        self.trade_exchange = trade_exchange

    def generate_signals(self, date: str) -> pd.Series:
        """
        生成交易信号
        
        Args:
            date: 交易日期，格式为 "YYYY-MM-DD"
            
        Returns:
            pd.Series: 索引为股票代码，值为目标仓位（-1 到 1）
        """
        positions = {}
        # 遍历股票列表生成信号
        for stock in self.stock_list:
            positions[stock] = 0.5  # 50% 仓位
        return pd.Series(positions)
```

## TargetPositionStrategy 目标仓位策略

```python
from qlib.strategy import TargetPositionStrategy

class MyStrategy(TargetPositionStrategy):
    def __init__(self, portfolio_strategy, **kwargs):
        super().__init__(portfolio_strategy, **kwargs)
```

## LongShortStrategy 多空策略

```python
from qlib.strategy import LongShortStrategy

class MyStrategy(LongShortStrategy):
    def __init__(self, portfolio_strategy, **kwargs):
        super().__init__(portfolio_strategy, **kwargs)
```

## 策略类必须实现的方法

1. `__init__`: 初始化策略参数
2. `generate_signals(date)`: 生成交易信号，返回 pd.Series
3. 可选: `get_trade_dates()` 获取交易日期
4. 可选: `get_position_size()` 仓位管理

## 策略开发要点

1. 策略类必须继承 BaseStrategy 或其子类
2. generate_signals 返回值必须是 pd.Series
3. 索引为股票代码，值为仓位权重（-1 到 1）
4. 负值表示做空，正值表示做多

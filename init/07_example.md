# Qlib 策略完整示例

## 动量策略完整代码

```python
import pandas as pd
from qlib.data import D
from qlib.strategy import BaseStrategy


class MomentumStrategy(BaseStrategy):
    """动量策略：买入近期涨幅较大的股票"""
    
    def __init__(
        self,
        trade_exchange=None,
        short_window: int = 5,
        long_window: int = 20,
        top_k: int = 20
    ):
        super().__init__(trade_exchange)
        self.short_window = short_window
        self.long_window = long_window
        self.top_k = top_k
        self.stock_list = None
    
    def generate_signals(self, date: str) -> pd.Series:
        # 获取股票列表
        if self.stock_list is None:
            self.stock_list = D.instruments(market="csi300")
        
        # 获取历史数据
        start_date = pd.Timestamp(date) - pd.Timedelta(days=self.long_window * 2)
        end_date = pd.Timestamp(date)
        
        try:
            close_data = D.features(
                self.stock_list,
                ["$close"],
                start_time=start_date.strftime("%Y-%m-%d"),
                end_time=end_date.strftime("%Y-%m-%d")
            )
            
            signals = {}
            for stock in close_data.columns.get_level_values(0).unique():
                stock_close = close_data[stock]["$close"].dropna()
                
                if len(stock_close) >= self.long_window:
                    returns = stock_close.pct_change(periods=self.short_window).iloc[-1]
                    if returns > 0:
                        signals[stock] = 1.0 / self.top_k
                    else:
                        signals[stock] = 0.0
            
            # 选择 Top-K
            if len(signals) > self.top_k:
                sorted_signals = sorted(signals.items(), key=lambda x: x[1], reverse=True)
                top_signals = dict(sorted_signals[:self.top_k])
                
                # 归一化
                total = sum(top_signals.values())
                if total > 0:
                    top_signals = {k: v / total for k, v in top_signals.items()}
                
                return pd.Series(top_signals)
            
            return pd.Series(signals)
            
        except Exception as e:
            print(f"生成信号失败: {e}")
            return pd.Series({})
```

## Top-K 策略完整代码

```python
import pandas as pd
from qlib.strategy import BaseStrategy


class TopKStrategy(BaseStrategy):
    """选择预测分数最高的 K 只股票"""
    
    def __init__(self, top_k: int = 10, **kwargs):
        super().__init__(**kwargs)
        self.top_k = top_k
    
    def generate_signals(self, pred_score: pd.Series) -> pd.Series:
        # 选取 Top-K
        top_k_stocks = pred_score.nlargest(self.top_k)
        
        # 等权分配
        weight = 1.0 / self.top_k
        signals = {stock: weight for stock in top_k_stocks.index}
        
        return pd.Series(signals)
```

## 策略开发检查清单

- [ ] 继承 BaseStrategy
- [ ] 实现 __init__ 方法
- [ ] 实现 generate_signals 方法
- [ ] 返回 pd.Series 类型
- [ ] 索引为股票代码
- [ ] 仓位值在 [-1, 1] 范围内
- [ ] 添加类型注解
- [ ] 添加必要的注释

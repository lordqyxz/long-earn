# Qlib 信号生成模式

## 模式一：直接返回仓位权重

```python
def generate_signals(self, date: str) -> pd.Series:
    """
    返回目标仓位
    
    Returns:
        pd.Series: 索引为股票代码，值为仓位权重（-1 到 1）
    """
    positions = {}
    for stock in self.stock_list:
        # 计算目标仓位
        positions[stock] = 0.5  # 50% 仓位
    return pd.Series(positions)
```

## 模式二：使用预测分数生成信号

```python
def generate_signals(self, pred_score: pd.DataFrame) -> pd.Series:
    """
    基于预测分数生成信号
    
    Args:
        pred_score: 预测分数 DataFrame
        
    Returns:
        pd.Series: 目标仓位
    """
    # 取 Top-K
    top_k = 10
    top_stocks = pred_score.nlargest(top_k).index
    
    # 等权分配
    positions = {stock: 1.0 / top_k for stock in top_stocks}
    return pd.Series(positions)
```

## 动量策略信号生成

```python
def generate_signals(self, date: str) -> pd.Series:
    # 获取历史数据
    close_data = D.features(
        self.stock_list,
        ["$close"],
        start_time=start_date,
        end_time=date
    )
    
    signals = {}
    for stock in close_data.columns.get_level_values(0).unique():
        stock_close = close_data[stock]["$close"].dropna()
        
        if len(stock_close) >= self.lookback_period:
            # 计算动量信号
            returns = stock_close.pct_change(periods=self.period).iloc[-1]
            
            if returns > 0:
                signals[stock] = 1.0 / self.top_k
            else:
                signals[stock] = 0.0
    
    return pd.Series(signals)
```

## 均线交叉信号生成

```python
def generate_signals(self, date: str) -> pd.Series:
    # 计算短期和长期均线
    short_ma = price_data.rolling(self.short_window).mean()
    long_ma = price_data.rolling(self.long_window).mean()
    
    # 金叉买入，死叉卖出
    if short_ma.iloc[-1] > long_ma.iloc[-1]:
        return pd.Series({stock: 1.0 for stock in self.stock_list})
    else:
        return pd.Series({stock: 0.0 for stock in self.stock_list})
```

## 信号生成规则

1. 返回值必须是 pd.Series 类型
2. 索引为股票代码，值为目标仓位
3. 仓位范围：-1（满仓做空）到 1（满仓做多）
4. 0 表示空仓
5. 所有仓位通常需要归一化处理

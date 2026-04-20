# Qlib 绩效指标计算

## 常用绩效指标

| 指标 | 说明 | 理想值 |
|------|------|--------|
| 年化收益率 | 年度平均收益 | > 10% |
| 夏普比率 | 风险调整收益 | > 0.5 |
| 最大回撤 | 最大亏损幅度 | < 20% |
| 胜率 | 盈利交易比例 | > 50% |
| 卡玛比率 | 收益/最大回撤 | > 1.0 |
| 波动率 | 收益波动程度 | 越低越好 |

## 指标计算代码

```python
import numpy as np
import pandas as pd

def calculate_metrics(daily_returns: pd.Series) -> dict:
    """计算绩效指标"""
    
    # 总收益率
    total_return = (1 + daily_returns).cumprod().iloc[-1] - 1
    
    # 年化收益率
    annual_return = (1 + total_return) ** (252 / len(daily_returns)) - 1
    
    # 夏普比率
    sharpe_ratio = (
        daily_returns.mean() / daily_returns.std() * np.sqrt(252)
        if daily_returns.std() != 0 else 0
    )
    
    # 最大回撤
    cumulative = (1 + daily_returns).cumprod()
    max_drawdown = ((cumulative.cummax() - cumulative) / cumulative.cummax()).max()
    
    # 胜率
    win_rate = (daily_returns > 0).sum() / len(daily_returns)
    
    # 波动率
    volatility = daily_returns.std() * np.sqrt(252)
    
    # 卡玛比率
    calmar_ratio = annual_return / max_drawdown if max_drawdown > 0 else 0
    
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "volatility": volatility,
        "calmar_ratio": calmar_ratio,
    }
```

## 策略评估标准

### 合格策略
- 年化收益率 > 10%
- 夏普比率 > 0.5
- 最大回撤 < 20%

### 优秀策略
- 年化收益率 > 20%
- 夏普比率 > 1.0
- 最大回撤 < 10%
- 卡玛比率 > 2.0

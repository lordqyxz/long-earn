# Qlib 回测配置

## 使用 workflow 进行回测

```python
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, PortAnaRecord

# 开始实验
with R.start(experiment_name="my_strategy"):
    # 生成信号
    sr = SignalRecord(task_id=task_id)
    sr.generate()
    
    # 回测分析
    par = PortAnaRecord(sr.get_pred_graph(), "2020-01-01", "2023-12-31")
    par.generate()
```

## 使用 backtest 模块

```python
from qlib.backtest import backtest, Exchange

# 创建交易所配置
exchange = Exchange(
    start_time="2020-01-01",
    end_time="2023-12-31",
    freq="day",
    benchmark="SH000300"  # 沪深300作为基准
)

# 执行回测
portfolio = backtest(
    strategy=my_strategy,
    exchange=exchange,
    account=100000000  # 1亿资金
)
```

## 回测参数配置

```python
backtest_config = {
    "start_time": "2020-01-01",
    "end_time": "2023-12-31",
    "account": 100000000,  # 1亿
    "benchmark": "SH000300",
    "exchange_kwargs": {
        "freq": "day",
        "limit_threshold": 0.095,  # 涨跌停限制 9.5%
        "deal_price": "close",      # 成交价格用收盘价
        "cancel_threshold": 0,     # 撤单阈值
        "fee": {
            "commission": 0.001,   # 手续费千分之一
            "slippage": 0.0005,    # 滑点万分之五
        }
    }
}
```

## 回测时间范围选择

1. 长周期回测：2020-01-01 至 2023-12-31（4年）
2. 短周期回测：2022-01-01 至 2023-12-31（2年）
3. 牛市测试：2019-01-01 至 2020-12-31
4. 熊市测试：2022-01-01 至 2022-12-31

## 回测注意事项

1. 确保数据覆盖完整，无缺失交易日
2. 设置合理的交易费用和滑点
3. 考虑涨跌停限制对交易的影响
4. 使用合适的基准进行对比

# Qlib 数据获取 - 初始化

## 初始化 Qlib

```python
from qlib import init
from pathlib import Path

qlib_data_path = Path.home() / ".qlib_data"
if qlib_data_path.exists():
    init(provider_uri=str(qlib_data_path), region="cn")
```

## 获取股票列表

```python
from qlib.data import D

# 获取 A 股市场股票列表
instruments = D.instruments(market="csi300")  # CSI300 成分股
instruments = D.instruments(market="csi1000")  # CSI1000 成分股
instruments = D.instruments(market="all")      # 所有 A 股

# 使用正则匹配股票代码
instruments = D.list_instruments(symbols="SH60.*", as_list=True)
```

## 获取历史价格数据

```python
from qlib.data import D
import pandas as pd

# 获取单只股票数据
fields = ["$close", "$open", "$high", "$low", "$volume", "$money"]
data = D.features(
    symbol="SH600519",  # 贵州茅台
    fields=fields,
    start_time="2020-01-01",
    end_time="2023-12-31"
)

# 获取多只股票数据
symbols = ["SH600519", "SH000001", "SZ000002"]
data = D.features(
    symbol=symbols,
    fields=["$close", "$volume"],
    start_time="2020-01-01",
    end_time="2023-12-31"
)

# 获取交易日历
trade_dates = D.calendar(start_time="2020-01-01", end_time="2023-12-31", freq="day")
```

## 常用数据字段

| 字段 | 说明 |
|------|------|
| $close | 收盘价 |
| $open | 开盘价 |
| $high | 最高价 |
| $low | 最低价 |
| $volume | 成交量 |
| $money | 成交额 |
| $change | 涨跌幅 |
| $vwap | 成交量加权平均价 |

## 数据获取最佳实践

1. 预先定义股票池，避免每次查询都获取全市场数据
2. 使用合适的时间范围，减少不必要的数据获取
3. 处理缺失数据，使用 dropna() 或前向填充
4. 注意数据时区，Qlib 使用北京时间

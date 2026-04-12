# 回测服务

基于 qlib 的独立回测服务，通过 HTTP API 提供回测功能。

## 架构说明

```
┌─────────────────┐      HTTP       ┌──────────────────┐
│   主项目         │ ◄─────────────► │  回测服务         │
│  long-earn      │   (port 8001)   │  backtest_service│
│                 │                 │                  │
│ - 策略开发       │                 │ - qlib 初始化     │
│ - Agent 节点     │                 │ - 回测执行        │
│ - LangGraph     │                 │ - 数据获取        │
└─────────────────┘                 └──────────────────┘
```

## 优势

1. **依赖隔离**：回测服务使用独立的虚拟环境和依赖配置，避免 protobuf、setuptools 等包的版本冲突
2. **清晰边界**：主项目专注于 Agent 和策略开发，回测服务专注于数据获取和回测执行
3. **可扩展性**：可以独立升级 qlib 版本或优化回测逻辑，不影响主项目
4. **远程部署**：可以将回测服务部署到远程服务器，通过 HTTP 调用

## 使用方法

### 1. 启动回测服务

```bash
cd backtest_service
uv sync
uv run python -m long_earn_backtest
```

服务将启动在 `http://localhost:8001`

### 2. 在主项目中调用回测

```python
from long_earn.tools.backtest import run_backtest

# 策略代码
strategy_code = """
import pandas as pd
from qlib.data import D

class MyStrategy:
    def __init__(self, stock_list=None):
        self.stock_list = stock_list or []
    
    def generate_signals(self, date: str) -> pd.Series:
        positions = {}
        for stock in self.stock_list:
            positions[stock] = 0.1
        return pd.Series(positions)
"""

# 运行回测
results = run_backtest(
    strategy_code=strategy_code,
    start_date="2023-01-01",
    end_date="2023-12-31",
    stock_list=["600519", "000858", "601318"]
)

if results:
    print(f"总收益率：{results['total_return']:.2%}")
    print(f"夏普比率：{results['sharpe_ratio']:.2f}")
```

### 3. 配置远程服务

如果回测服务运行在远程服务器上：

```bash
export BACKTEST_SERVICE_URL="http://your-server:8001"
```

## API 文档

### POST /api/v1/backtest

运行回测

**请求体**:
```json
{
  "strategy_code": "策略代码字符串",
  "start_date": "2023-01-01",
  "end_date": "2023-12-31",
  "stock_list": ["600519", "000858"]
}
```

**响应**:
```json
{
  "success": true,
  "message": "回测成功",
  "total_return": 0.1523,
  "annual_return": 0.1687,
  "sharpe_ratio": 1.23,
  "max_drawdown": 0.0856,
  "win_rate": 0.62,
  "trading_days": 245
}
```

### GET /health

健康检查

**响应**:
```json
{
  "status": "healthy",
  "qlib": "initialized"
}
```

## 依赖管理

回测服务的依赖在 `backtest_service/pyproject.toml` 中独立管理：

- `pyqlib>=0.9.7` - qlib 量化框架
- `protobuf<4.0` - 与 qlib 兼容的 protobuf 版本
- `setuptools<60` - 包含 pkg_resources 的版本
- `fastapi>=0.104.0` - Web 框架
- `uvicorn>=0.24.0` - ASGI 服务器

## 故障排除

### 无法连接到回测服务

确保服务正在运行：
```bash
cd backtest_service
uv run --active python -m long_earn_backtest
```

检查服务状态：
```bash
curl http://localhost:8001/health
```

### qlib 初始化失败

检查 qlib 数据是否已下载：
```bash
# 下载 qlib 数据（如果尚未下载）
python -c "import qlib; qlib.init()"
```

### 回测返回空结果

查看回测服务日志，确认：
1. 策略代码是否正确加载
2. 是否获取到交易日历
3. 策略是否生成有效信号
4. qlib 数据是否可用

### 依赖冲突

回测服务使用独立的虚拟环境：
```bash
cd backtest_service
uv sync --active
```

## 日志说明

回测服务会输出详细日志：
- **INFO**: 回测请求、进度、结果
- **WARNING**: 空信号、无数据
- **ERROR**: 策略加载失败、数据获取失败

示例日志：
```
2026-03-23 05:03:27,817 - long_earn_backtest.server - INFO - qlib 初始化成功
2026-03-23 05:05:00,123 - long_earn_backtest.server - INFO - 收到回测请求：start_date=2023-01-01, end_date=2023-01-31, stocks=3
2026-03-23 05:05:00,456 - long_earn_backtest.server - INFO - 策略代码已写入临时文件：/var/folders/xxx.py
2026-03-23 05:05:00,789 - long_earn_backtest.server - INFO - 策略模块加载成功
2026-03-23 05:05:00,790 - long_earn_backtest.server - INFO - 找到策略类：TestStrategy
2026-03-23 05:05:00,791 - long_earn_backtest.server - INFO - 策略实例已创建，传入 stock_list: ['600519', '000858', '601318']
2026-03-23 05:05:01,123 - long_earn_backtest.server - INFO - 获取交易日历：2023-01-01 至 2023-01-31
2026-03-23 05:05:01,456 - long_earn_backtest.server - INFO - 获取到 20 个交易日
2026-03-23 05:05:01,789 - long_earn_backtest.server - INFO - 开始执行回测...
2026-03-23 05:05:02,123 - long_earn_backtest.server - INFO - 回测进度：10/20
2026-03-23 05:05:05,456 - long_earn_backtest.server - INFO - 回测完成，有效交易日：16, 错误数：0, 空信号数：4
2026-03-23 05:05:05,789 - long_earn_backtest.server - INFO - 回测成功完成，耗时：5.67 秒
```

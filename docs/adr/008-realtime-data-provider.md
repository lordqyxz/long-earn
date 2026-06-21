# ADR-008: 实时行情数据提供者（订阅 + 轮询降级）

## 状态
Accepted（2026-06-22）

## 上下文

TODO #3.3「实时数据对接」要求将 miniqmt 静态回测扩展到支持近实时的行情监控与预警。

现有数据层（`backtest/data/`）只支持**批量历史数据**获取（`get_price_panel` / `get_financial_panel`），无实时行情能力：

- **miniqmt**：`xtdata.subscribe_quote(stock_code, period, callback)` 支持实时推送，`get_full_tick(code_list)` 支持最新逐笔快照——但需 miniQMT 客户端在线（CI / 无终端环境不可用）。
- **ciccwm**：纯 HTTP API，无长连接订阅能力，但 `fetch_info(code)` 可获取最新价/涨跌幅等 spot 快照——CI 友好（仅需网络 + 凭证）。
- **akshare**：实时接口不稳定，速率受限。

## 决策

新增 **`RealtimeDataProvider` Protocol**（`backtest/data/realtime.py`），与现有 `DataProvider`（历史数据）平行，统一实时行情接口：

```python
class RealtimeDataProvider(Protocol):
    is_available: bool
    def get_latest_quote(self, symbol: str) -> dict[str, Any]: ...   # 单次拉取快照
    def subscribe_quote(self, symbols, callback) -> str: ...          # 订阅推送
    def unsubscribe(self, subscription_id: str) -> None: ...           # 取消订阅
```

### 降级链：miniqmt → ciccwm

| 提供者 | 订阅 | 最新价 | CI 友好 |
|--------|------|--------|---------|
| `MiniQmtRealtimeProvider` | ✅ `xtdata.subscribe_quote` | ✅ `get_full_tick` | ❌ 需 QMT 在线 |
| `CiccwmRealtimeProvider` | ❌ HTTP 无长连接 | ✅ `fetch_info` 轮询 | ✅ 仅需网络 |
| `CompositeRealtimeProvider` | miniqmt 不可用则不订阅 | miniqmt → ciccwm | 自动降级 |

`subscribe_quote` 在 ciccwm fallback 下返回空 ID（不支持订阅），`get_latest_quote` 降级到 HTTP 轮询——与"近实时"诉求一致。

### PriceAlert 预警节点

`monitoring/realtime_alert.py` 提供 `PriceAlert(symbol, threshold, direction)` 数据类 + `check(provider)` 方法，作为独立 demo 模块，**不强制接入主图**（保持解耦，供后续策略研发子图按需调用）。

## 收益

- **统一接口**：上层只依赖 `RealtimeDataProvider` Protocol，不感知底层 miniqmt/ciccwm。
- **CI 友好**：xtquant 不可用时所有方法返回空值不抛（与 `DataProvider` 一致的容错模式）。
- **可降级**：无 miniQMT 环境下仍可用 ciccwm HTTP 轮询实现"近实时"查询。
- **可扩展**：未来可接入其他实时源（如 websocket 推送）只需新增 Protocol 实现。

## 替代方案

- **直接在 `DataProvider` Protocol 加 subscribe 方法**：会污染历史数据接口，违反接口隔离原则。实时与历史是不同关注点，独立 Protocol 更清晰。
- **轮询替代订阅**：ciccwm 本就是轮询；但 miniqmt 有原生 subscribe 能力，不使用浪费。保留 subscribe 接口，ciccwm fallback 仅退化到轮询。
- **引入异步框架（asyncio）**：当前主图是 LangGraph 同步流，引入 async 增加复杂度。`subscribe_quote` 的 callback 是同步的，匹配现有架构。

## 影响

- 新增文件：`backtest/data/realtime.py`（Protocol + 3 实现 + 工厂）、`monitoring/realtime_alert.py`（PriceAlert）。
- 修改：`ciccwm_provider.py` 新增 `get_info(symbol)` 方法（实时快照基础）。
- 新增测试：`tests/unit/test_backtest/test_realtime.py`（18 用例）。
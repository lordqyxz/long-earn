# ADR-011: 增强实时分析能力

## 状态

Proposed

## 上下文

当前系统在分析侧有两个能力缺口，二者同源——都依赖**实时数据**但当前缺失：

### 缺口 1：无实时行情基础设施

数据层（ADR-006/008/009 重构后）的 `DataProvider` Protocol 面向**历史面板**回测：

```
get_price_panel / get_financial_panel / get_merged_panel / get_merged_panel_as_polars
```

所有方法返回 `(date, symbol)` MultiIndex 的 pandas/polars DataFrame，适合批量历史数据喂入回测引擎。但 CLAUDE.md TODO「实时数据对接」要求的能力本质不同：

| 维度 | 历史面板（现有 `DataProvider`） | 实时行情（本 ADR） |
|------|--------------------------------|------------------|
| 数据形态 | 批量 DataFrame（日级） | 单点 dict（tick 级） |
| 调用模式 | 同步拉取 | 订阅-推送 + 同步快照 |
| 时间跨度 | 过去日期范围 | 当前时刻 |
| 消费方 | 回测引擎 | 告警/监控/实时分析 |

强行把实时能力塞进 `DataProvider` 会污染面向回测的接口（返回类型、调用模式都不匹配）。

### 缺口 2：股票分析子图缺资金流向视角

`stock_analysis` 子图当前 4 视角并行（巴菲特/芒格/费雪/林奇）**全部聚焦基本面与估值**，缺失**主力资金博弈与情绪面**维度。CLAUDE.md TODO「增强分析视角：资金流向分析」明确要求补齐。

数据能力其实已就绪：ADR-006 引入的 ciccwm 提供了资金流向独占能力，重构后已抽为第二组接口 `MarketIntelligenceProvider.get_fund_flow`，`RuntimeContext.market_intelligence` 已注入。**数据接口就绪但未被 `stock_analysis` 子图消费**。

### 历史背景

远端 `c08840d`（ralph-completion）曾有 `realtime.py` / `realtime_alert.py` / `fund_flow_analyst.py`，但：
- 用 `import logging`（违反项目 loguru 规范）
- 直接 `CiccwmDataProvider()` 实例化（绕过 DI 容器）
- `getattr(provider, "ciccwm_provider", None)` 访问 ciccwm（旧耦合方式，已被 `MarketIntelligenceProvider` 接口替代）
- 引用 `provider.get_info()` 方法（当前 `CiccwmDataProvider` 无此方法，需走 `ciccwm_client.fetch_info`）

本 ADR 基于重构后的接口重新设计，不直接 cherry-pick 旧代码。

## 决策

**核心洞察**：两个缺口同源——资金流向分析本质上就是实时行情数据在分析视角的应用。合并为一个 ADR：以**实时行情 Provider** 为基础设施，**资金流向分析师** 为第一个消费者，形成"数据层 → 分析层"的完整闭环。

### 三组数据接口对照

本 ADR 新增第三组接口 `RealtimeDataProvider`，与现有两组并列：

| 接口 | 职责 | 降级链 | 返回类型 | ADR |
|------|------|--------|---------|-----|
| `DataProvider` | 历史面板（行情/财务） | DuckDB→miniqmt→ciccwm→akshare | pandas/polars DataFrame | 006/008 |
| `MarketIntelligenceProvider` | 市场情报（资金流向/排行/板块/资讯） | 无降级，ciccwm 独占 | pandas DataFrame / list | 006/009 |
| **`RealtimeDataProvider`** | 实时行情（快照/订阅） | miniqmt→ciccwm | dict（单点 tick） | **本 ADR** |

### 1. 实时行情 Provider（基础设施层）

#### 落点

```
src/long_earn/backtest/data/
└── realtime.py              # RealtimeDataProvider Protocol + MiniQmt/Ciccwm 实现 + Composite
```

#### 接口定义

```python
class RealtimeDataProvider(Protocol):
    """实时行情数据提供者接口（第三组接口）。"""

    @property
    def is_available(self) -> bool: ...

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        """获取最新行情快照（同步）。

        Returns: 行情字典（price/volume/time/open/high/low/preClose/source）；
                 失败返回空 dict。
        """
        ...

    def subscribe_quote(
        self,
        symbols: list[str],
        callback: Callable[[dict[str, Any]], None],
    ) -> str:
        """订阅实时行情推送（异步）。

        Returns: 订阅 ID；不支持订阅时返回空字符串（调用方改用轮询）。
        """
        ...

    def unsubscribe(self, subscription_id: str) -> None: ...
```

#### 降级链

```
CompositeRealtimeProvider
  ├─ miniqmt（xtdata.subscribe_quote + get_full_tick）  — 推送 + 快照
  └─ ciccwm（fetch_info HTTP 轮询）                    — 仅快照，无订阅
```

- miniqmt 可用 → 推送订阅 + `get_full_tick` 快照
- miniqmt 不可用 → 降级到 ciccwm `fetch_info` HTTP 单次查询（`subscribe_quote` 返回空 ID，调用方改轮询）

`MiniQmtClient.get_full_tick` 已存在但未被上层消费；`subscribe_quote` 需在 `MiniQmtClient` 新增封装。

#### DI 集成

`RuntimeContext` 新增可选字段：

```python
@dataclass
class RuntimeContext:
    ...
    data_provider: "DataProvider | None" = None
    market_intelligence: "MarketIntelligenceProvider | None" = None
    realtime_provider: "RealtimeDataProvider | None" = None  # 新增
```

`context_init.py` 构造时注入 `CompositeRealtimeProvider`。

### 2. 价格阈值告警（监控消费者）

作为实时行情的第一个旁路消费者，验证订阅能力：

```
src/long_earn/monitoring/
└── realtime_alert.py        # PriceAlertMonitor：订阅 RealtimeDataProvider，价格突破阈值时回调
```

```python
class PriceAlertMonitor:
    def __init__(self, provider: RealtimeDataProvider): ...
    def add_alert(self, symbol: str, price: float, direction: str = "above") -> None: ...
    def start(self) -> str: ...   # 订阅并启动监控
    def stop(self) -> None: ...   # 取消订阅
```

告警为旁路，不参与回测主流程，失败不影响策略计算。

### 3. 资金流向分析师（分析消费者）

作为实时分析能力的第一个业务落地：补齐 `stock_analysis` 子图第 5 视角。

#### 落点

```
src/long_earn/stock_analysis/agents/
├── fund_flow_analyst.py      # FundFlowAnalyst
└── fund_flow_prompt.md       # 资金流向分析提示词
```

#### Agent 设计

与现有 4 个分析师同构（context 依赖注入 + 单一 `analyze(stock_data)` 入口）：

```python
class FundFlowAnalyst:
    def __init__(self, context: RuntimeContext): ...

    def fetch_fund_flow(self, symbol: str) -> pd.DataFrame:
        """通过 MarketIntelligenceProvider 接口获取资金流向。"""
        mi = self.context.market_intelligence
        if mi is None:
            return pd.DataFrame()  # ciccwm 不可用，prompt 走"数据缺失"占位
        return mi.get_fund_flow(symbol)

    def analyze(self, stock_data: dict[str, Any]) -> str: ...
```

#### 数据获取容错

- `context.market_intelligence` 为 `None` → `fund_flow_data` 为空 DataFrame
- Prompt 约定"数据暂不可用"占位分支，不抛异常、不阻塞其他 4 个分析师
- 与现有 4 视角容错模式一致

#### 子图接入

`stock_analysis/subgraph.py` 从 4 视角并行扩展为 5 视角：

```
START → get_stock_data → [petter, charles_munger, buffett, fiske, fund_flow] → summarize → END
```

#### Prompt 设计

聚焦 4 个维度（不重复基本面，只看主力资金博弈）：

1. **主力净流入/流出方向与强度**（最近 1/3/5/10 日变化趋势）
2. **大单 vs 中小单背离**（机构与散户分歧）
3. **资金流向与价格走势一致性**（量价配合 / 顶背离 / 底背离）
4. **当前阶段判断**（建仓 / 拉升 / 派发 / 出货），附依据

输出格式：markdown 结构，每段 2-4 句，简洁。

## 实施依赖

```
阶段 1：实时行情基础设施          阶段 2：资金流向分析师
─────────────────────────       ─────────────────────────
realtime.py (Provider)    ───►  fund_flow_analyst.py
realtime_alert.py (告警)         fund_flow_prompt.md
RuntimeContext.realtime_provider  stock_analysis/subgraph.py 接入
context_init.py 注入
```

- **阶段 1**（实时行情基础设施）：`realtime.py` + `realtime_alert.py` + DI 注入 + 测试
- **阶段 2**（资金流向分析师）：`fund_flow_analyst.py` + prompt + 子图接入 + 测试

阶段 2 可独立于阶段 1 实施（`FundFlowAnalyst` 只依赖 `MarketIntelligenceProvider`，不依赖 `RealtimeDataProvider`）；但两者共同构成"实时分析能力增强"的完整图景。

## 验收标准

1. `MiniQmtRealtimeProvider` 在 xtquant 可用时 `get_latest_quote` 返回非空 dict
2. `CiccwmRealtimeProvider` 在 ciccwm 可用时 `get_latest_quote` 返回非空 dict；`subscribe_quote` 返回空 ID
3. `CompositeRealtimeProvider` miniqmt 不可用时降级到 ciccwm
4. `PriceAlertMonitor` 价格突破阈值时触发回调
5. `FundFlowAnalyst.analyze()` 在 `market_intelligence` 可用时返回非空分析文本
6. `market_intelligence` 为 `None` 时返回"数据暂不可用"占位（不抛异常）
7. `stock_analysis` 子图编译成功，5 视角并行汇聚到 summarize
8. 单元测试 mock 数据源，不依赖真实 xtquant/ciccwm
9. `ruff check src/` 零错 + `pytest tests/unit/` 全绿

## 后续

- **近实盘策略接入**：将 `RealtimeDataProvider` 喂入事件驱动引擎的 `on_bar`（当前引擎只消费历史面板）
- **实时资金流向监控**：`FundFlowAnalyst` 接入 `RealtimeDataProvider`，从离线分析升级为实时监控
- **多源订阅合并**：当 miniqmt + ciccwm 同时订阅时，去重与源标记
- **与 `strategy_rd` 子图联动**：资金流向信号作为策略因子
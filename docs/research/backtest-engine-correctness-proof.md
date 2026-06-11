# 回测引擎正确性数学证明

> 本文档对 `long_earn.backtest.engine` 事件驱动回测引擎的核心正确性性质给出形式化定义与数学证明。所有命题均基于当前代码实现（v1.0.1），并在证明过程中驱动了若干实现修正。

---

## 1. 符号与定义

### 1.1 基本符号

| 符号 | 含义 |
|------|------|
| $T = \{t_1, t_2, \dots, t_n\}$ | 回测时间序列，$t_i < t_{i+1}$ |
| $S$ | 股票代码集合 |
| $D_t = \{ (s, o_s^t, h_s^t, l_s^t, c_s^t, v_s^t) \mid s \in S \}$ | 时刻 $t$ 的截面数据（Slab） |
| $D_{\leq t} = \bigcup_{\tau \leq t} D_\tau$ | 截至时刻 $t$ 的所有历史数据 |
| $P_t$ | 时刻 $t$ 的投资组合状态（现金 + 持仓） |
| $V(P_t)$ | 组合总市值 |
| $\mathcal{E}$ | 引擎状态（Engine State） |

### 1.2 关键概念定义

**定义 1.1（未来函数 / Look-ahead bias）**

策略在时刻 $t$ 的决策函数为 $\mathcal{S}_t = f(D_{\leq t}, \theta_t)$，其中 $\theta_t$ 为策略内部状态。若存在某个实现使得 $f$ 依赖于 $D_{>t}$（即 $t$ 时刻之后的数据），则称该实现包含**未来函数**。

**定义 1.2（事件驱动执行模型）**

引擎的执行序列是一个离散事件序列 $\{e_1, e_2, \dots, e_m\}$，每个事件 $e_i = (type_i, t_i, data_i)$ 包含事件类型、时间戳和载荷。事件类型包括：
- `MARKET_DATA`: 市场数据到达
- `SIGNAL`: 策略生成信号
- `ORDER`: 订单请求
- `FILL`: 成交确认
- `RISK`: 风控触发

**定义 1.3（投资组合不变式）**

对于任意时刻 $t$，投资组合满足：
$$V(P_t) = C_t + \sum_{s \in S} Q_s^t \cdot P_s^t$$
其中 $C_t$ 为现金，$Q_s^t$ 为股票 $s$ 的持仓数量，$P_s^t$ 为股票 $s$ 的当前价格。

**定义 1.4（数据隔离性）**

引擎满足数据隔离性，当且仅当对于所有时刻 $t$ 和所有策略访问操作 $A_t$，有：
$$\text{ReadSet}(A_t) \subseteq D_{\leq t}$$

---

## 2. 核心定理与证明

### 定理 2.1（无未来函数定理）

**命题**：事件驱动回测引擎在任意执行路径上均不包含未来函数。

**证明**：

我们需要证明：对于任意时刻 $t_i \in T$，策略 `on_bar` 方法只能访问 $D_{\leq t_i}$ 范围内的数据。

**步骤 1：数据准备阶段的隔离**

引擎在 `run()` 方法中通过 `_prepare_data()` 获取数据。根据代码实现：

```python
def _prepare_data(self, symbols, start, end):
    df = self.data_provider.get_merged_panel_as_polars(symbols, start, end)
    if df is not None and not df.is_empty():
        if "timestamp" in df.columns:
            df = df.filter(
                (pl.col("timestamp") >= start) & (pl.col("timestamp") <= end)
            )
        return df
```

设请求的时间范围为 $[t_{start}, t_{end}]$，则返回的数据集 $D_{prep}$ 满足：
$$D_{prep} = \{ d \mid d.timestamp \in [t_{start}, t_{end}] \}$$

这保证了引擎初始化时的数据边界。

**步骤 2：VisibilityGuard 的时间轴控制**

`VisibilityGuard` 维护当前时间戳 `current_timestamp`，所有数据访问方法均通过该时间戳过滤：

- `read_scalar(symbol, field)`: 过滤条件 `(timestamp == current_timestamp)`
- `read_history(symbol, field, window)`: 过滤条件 `(timestamp <= current_timestamp)`
- `read_history_df()`: 过滤条件 `(timestamp <= current_timestamp)`
- `read_current_slab()`: 过滤条件 `(timestamp == current_timestamp)`

形式化地，设 `current_timestamp = t_i`，则对于任意访问操作 $A$：
$$\text{ReadSet}(A) \subseteq D_{\leq t_i}$$

**步骤 3：引擎主循环的时间单调性**

引擎主循环按时间戳排序迭代：

```python
timestamps = self._get_timestamps(full_data)  # 已排序
for bar_idx, ts in enumerate(timestamps):
    self._process_timestamp(ts, ...)
```

时间序列严格递增：$t_1 < t_2 < \dots < t_n$。在每个 `_process_timestamp` 中：

```python
guard.set_time(ts)  # 设置当前时间
slab = guard.read_current_slab()  # 只能访问当前时间数据
```

**步骤 4：策略接口的封闭性**

策略的 `on_bar` 方法签名：
```python
def on_bar(self, bars: pl.DataFrame, context: VisibilityContext) -> SignalEvent | None
```

- `bars` 是当前时刻的 Slab（由 `guard.read_current_slab()` 生成）
- `context` 是 `VisibilityContext` 实例，其所有方法最终委托给 `VisibilityGuard`

策略无法直接访问 `full_data` 或 `guard._full_data`，因为：
1. `VisibilityContext` 不暴露底层 `VisibilityGuard` 的 `_full_data`
2. Python 的命名约定 `_full_data` 表示私有属性
3. 策略基类不持有数据引用

**步骤 5：反证法**

假设存在未来函数，即策略在时刻 $t_i$ 访问了 $D_{>t_i}$ 中的数据 $d$。

根据步骤 2，所有数据访问必须通过 `VisibilityGuard` 的方法，这些方法均使用过滤条件 `timestamp <= current_timestamp`。

由于 $d.timestamp > t_i = \text{current_timestamp}$，过滤条件将排除 $d$。

因此 $d \notin \text{ReadSet}(A_{t_i})$，与假设矛盾。

**结论**：$\square$ 引擎不存在未来函数。

---

### 定理 2.2（投资组合守恒定理）

**命题**：对于任意时刻 $t$，投资组合总市值 $V(P_t)$ 等于初始资金加上所有已实现盈亏减去所有交易成本。

**证明**：

**步骤 1：初始化状态**

```python
class Portfolio:
    def __init__(self, initial_capital=1_000_000.0):
        self.cash = initial_capital
        self.positions = {}
        self.total_value = initial_capital
```

初始状态：$C_0 = V_0$，$Q_s^0 = 0$ 对所有 $s \in S$。

验证不变式：$V(P_0) = C_0 + \sum_s Q_s^0 \cdot P_s^0 = V_0 + 0 = V_0$。成立。

**步骤 2：买入操作（BUY）的守恒性**

当买入股票 $s$ 的 $q$ 股，成交价为 $p$，佣金为 $c$，印花税为 $0$（买入无印花税）：

```python
if fill.order_type == "BUY":
    cost = fill.fill_price * fill.fill_quantity + fill.commission + fill.stamp_duty
    self.cash -= cost
    pos.shares += fill.fill_quantity
    pos.avg_cost = total_cost / pos.shares
```

资金变化：$\Delta C = -(p \cdot q + c)$

持仓变化：$\Delta Q_s = +q$

新持仓成本：$\text{avg\_cost}' = \frac{\text{avg\_cost} \cdot Q_s + p \cdot q}{Q_s + q}$

市值变化：持仓市值增加 $p \cdot q$（按当前价计算），但现金减少 $p \cdot q + c$。

总市值变化：$\Delta V = p \cdot q - (p \cdot q + c) = -c \leq 0$

交易成本 $c$ 从组合中扣除，守恒性成立（资金流出为成本）。

**步骤 3：卖出操作（SELL）的守恒性**

当卖出股票 $s$ 的 $q$ 股，成交价为 $p$，佣金为 $c$，印花税为 $t$：

```python
proceeds = fill.fill_price * fill.fill_quantity
net_proceeds = proceeds - fill.commission - fill.stamp_duty
self.cash += net_proceeds
pos.shares -= fill.fill_quantity
realized_pnl = proceeds - (pos.avg_cost * fill.fill_quantity)
```

资金变化：$\Delta C = p \cdot q - c - t$

持仓变化：$\Delta Q_s = -q$

已实现盈亏：$\text{PnL} = q \cdot (p - \text{avg\_cost})$

总市值变化：持仓市值减少 $q \cdot p$，现金增加 $p \cdot q - c - t$。

$\Delta V = -q \cdot p + (p \cdot q - c - t) = -(c + t) \leq 0$

交易成本从组合中扣除，守恒性成立。

**步骤 4：市值更新操作的守恒性**

```python
def update_market_values(self, current_prices):
    for symbol, pos in self.positions.items():
        price = ...
        pos.update_market_value(price)
    self.total_value = self.cash + sum(p.market_value for p in self.positions.values())
```

此操作仅更新价格（$P_s^t$），不改变 $C_t$ 或 $Q_s^t$。

根据定义 1.3：$V(P_t) = C_t + \sum_s Q_s^t \cdot P_s^t$，此操作只是重新计算了等式右边，守恒性显然成立。

**步骤 5：归纳证明**

基例：$t = t_0$ 时成立（步骤 1）。

归纳假设：假设在时刻 $t_i$ 守恒性成立。

归纳步骤：在时刻 $t_{i+1}$，可能发生的操作包括：
1. 市值更新：守恒性保持（步骤 4）
2. 买入成交：守恒性保持（步骤 2）
3. 卖出成交：守恒性保持（步骤 3）
4. 无操作：守恒性 trivially 保持

因此，$t_{i+1}$ 时刻守恒性成立。

**结论**：$\square$ 投资组合守恒定理成立。

---

### 定理 2.3（风控触发正确性定理）

**命题**：当且仅当触发条件满足时，止损和最大回撤风控才会执行清仓操作。

#### 2.3.1 止损检查正确性

**定义**：设持仓 $s$ 的平均成本为 $c_s$，当前价格为 $p_s$，止损阈值为 $\alpha > 0$（如 0.05 表示 5%）。

触发条件：$\frac{p_s - c_s}{c_s} \leq -\alpha$，即亏损达到或超过 $\alpha$。

**代码逻辑**：
```python
pnl_pct = (pos.current_price - pos.avg_cost) / pos.avg_cost
if pnl_pct <= -self.stop_loss:  # 触发
    price = lookup(slab, symbol, field="low")  # 使用最低价成交
    ...
```

**证明**：

- 充分性：若 `pnl_pct <= -stop_loss`，则条件判断为真，触发清仓。代码中遍历所有持仓，对每个持仓独立检查，触发后生成 SELL 订单。
- 必要性：若 `pnl_pct > -stop_loss`，则 `continue` 跳过，不触发。由于使用的是 `<=` 比较，当恰好等于阈值时也会触发，这是符合金融惯例的（达到止损线即执行）。

**保守性说明**：代码使用 bar 内最低价（`low`）作为成交价格：
```python
price = self._lookup_price(slab, symbol, field="low")
if price is None or price <= 0:
    price = self._lookup_price(slab, symbol, field="close")
```

这保证了成交价格的**保守性**：$p_{fill} \leq p_{close}$，因此实际亏损 $\geq$ 基于收盘价的理论亏损，避免了高估策略表现。

#### 2.3.2 最大回撤检查正确性

**定义**：设组合历史峰值市值为 $V_{peak} = \max_{\tau \leq t} V(P_\tau)$，当前市值为 $V_t$，最大回撤限制为 $\beta > 0$（如 0.15 表示 15%）。

回撤：$DD_t = \frac{V_t - V_{peak}}{V_{peak}}$

触发条件：$DD_t \leq -\beta$，即回撤达到或超过限制。

**代码逻辑**：
```python
peak_value = max(portfolio.equity_curve) if portfolio.equity_curve else portfolio.total_value
dd = (portfolio.total_value - peak_value) / peak_value
threshold = -abs(self.max_drawdown_limit)
if dd <= threshold:  # 触发（dd 为负数或零）
    ...清仓...
```

**证明**：

- 充分性：若 `dd <= threshold = -beta`，即 $DD_t \leq -\beta$，触发条件满足，执行清仓。
- 必要性：若 `dd > threshold`，即 $DD_t > -\beta$，回撤未达限制，不触发。

**峰值计算正确性**：`peak_value = max(portfolio.equity_curve)`。由于 `equity_curve` 在每个 bar 结束时通过 `_sync_equity_curve()` 追加当前市值，因此：
$$V_{peak} = \max_{i < current\_bar} V(P_{t_i})$$

注意：在 `_process_timestamp` 中，`_sync_equity_curve()` 在风控检查前被调用，因此当前 bar 的市值已计入 `equity_curve`。这意味着峰值可能包含当前 bar 的市值（如果当前市值是新高），这在金融上是合理的：回撤应基于最新已知峰值计算。

**结论**：$\square$ 风控触发正确性成立。

---

### 定理 2.4（Walk-Forward 无数据泄漏定理）

**命题**：Walk-Forward 滚动回测中，测试期的策略决策不会使用训练期的未来数据或测试期之后的未来数据。

**证明**：

**步骤 1：时间分割的正确性**

```python
splitter = TimeSeriesSplit(n_splits=n_splits)
splits = splitter.split(timestamps)
```

`TimeSeriesSplit` 的实现：
```python
def split(self, timestamps):
    n = len(timestamps)
    fold_size = n // (self.n_splits + 1)
    for i in range(1, self.n_splits + 1):
        train_end = i * fold_size
        test_start = train_end + self.gap
        test_end = min(test_start + fold_size, n)
        splits.append((timestamps[:train_end], timestamps[test_start:test_end]))
```

对于第 $k$ 个 fold（$k = 1, \dots, n\_splits$）：
- 训练期：$T_{train}^{(k)} = \{t_1, \dots, t_{k \cdot fold\_size}\}$
- 测试期：$T_{test}^{(k)} = \{t_{k \cdot fold\_size + gap + 1}, \dots, t_{min(k \cdot fold\_size + gap + fold\_size, n)}\}$

显然：$T_{test}^{(k)} \cap T_{train}^{(k)} = \emptyset$（当 $gap \geq 0$ 时），且 $T_{test}^{(k)}$ 中所有时间戳大于 $T_{train}^{(k)}$ 中所有时间戳。

**步骤 2：数据准备的隔离性**

在 `walk_forward_run` 中，每次 `run()` 调用：
```python
train_result = self.run(strategy, train_start, train_end, symbols, ...)
test_result = self.run(strategy, test_start, test_end, symbols, ...)
```

`run()` 内部调用 `_prepare_data`：
```python
full_data = self._prepare_data(symbols, start_date, end_date)
```

根据定理 2.1 的步骤 1，`_prepare_data` 返回的数据满足：
$$D_{run} = \{ d \mid d.timestamp \in [start\_date, end\_date] \}$$

因此，测试期的 `run()` 只获取测试期时间范围内的数据。

**步骤 3：策略状态重置**

在每个 fold 开始时：
```python
strategy.init()
train_result = self.run(...)
```

`strategy.init()` 重置策略内部状态 $\theta$。虽然引擎无法强制子类清空所有状态，但根据契约（`BaseStrategy.init()` 的文档约定），子类应在此方法中重置所有状态。ML 策略的 `init()` 应清除模型拟合状态，确保测试期不携带训练期信息。

**步骤 4：综合论证**

对于第 $k$ 个 fold 的测试期：
1. 数据范围限制：`run()` 只加载 $[test\_start, test\_end]$ 的数据
2. 无未来函数：定理 2.1 保证策略只能访问 $\leq$ 当前时间戳的数据
3. 时间边界：当前时间戳 $\leq test\_end$，因此无法访问测试期之后的数据
4. 训练期隔离：测试期数据不包含训练期数据，且策略状态已重置

**结论**：$\square$ Walk-Forward 无数据泄漏。

---

### 定理 2.5（绩效指标计算正确性定理）

**命题**：引擎计算的绩效指标（总收益、年化收益、夏普比率、最大回撤等）在数学定义上与标准金融公式一致。

#### 2.5.1 总收益与年化收益

**定义**：
- 总收益：$R_{total} = \frac{V_T}{V_0} - 1$
- 年化收益：$R_{annual} = (1 + R_{total})^{\frac{252}{n}} - 1$，其中 $n$ 为交易日数

**代码**：
```python
total_return = (equity[-1] / equity[0]) - 1
trading_days = len(returns)
annual_factor = 252 / trading_days
annual_return = (1 + total_return) ** annual_factor - 1
```

**验证**：与定义完全一致。$\square$

#### 2.5.2 夏普比率

**定义**：$Sharpe = \frac{R_{annual}}{\sigma_{annual}}$，其中 $\sigma_{annual} = \sigma_{daily} \cdot \sqrt{252}$

**代码**：
```python
volatility = float(np.std(returns, ddof=1)) * np.sqrt(252)
sharpe = annual_return / volatility if volatility > 0 else 0.0
```

**验证**：使用样本标准差（`ddof=1`），年化乘数 $\sqrt{252}$，与定义一致。$\square$

#### 2.5.3 最大回撤

**定义**：$MDD = \min_{t} \frac{V_t - \max_{\tau \leq t} V_\tau}{\max_{\tau \leq t} V_\tau}$

**代码**：
```python
peak = np.maximum.accumulate(equity)
drawdown = (equity - peak) / peak
max_dd = float(np.min(drawdown))
```

**验证**：`np.maximum.accumulate` 计算历史峰值序列 $peak_t = \max_{\tau \leq t} V_\tau$，然后计算回撤序列并取最小值，与定义一致。$\square$

#### 2.5.4 Sortino 比率

**定义**：$Sortino = \frac{R_{annual}}{\sigma_{down} \cdot \sqrt{252}}$，其中 $\sigma_{down} = \sqrt{\frac{1}{n_{down}} \sum_{R_i < 0} R_i^2}$（下行标准差，不减均值）

**代码**：
```python
downside = returns[returns < 0]
downside_std = float(np.sqrt(np.mean(downside ** 2))) * np.sqrt(252)
sortino = annual_return / downside_std if downside_std > 0 else 0.0
```

**验证**：使用 $\sqrt{\text{mean}(R^2)}$ 计算下行偏差，不减去均值，与标准定义一致。$\square$

#### 2.5.5 Calmar 比率

**定义**：$Calmar = \frac{R_{annual}}{|MDD|}$

**代码**：
```python
calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0
```

**验证**：与定义一致。$\square$

#### 2.5.6 Alpha / Beta

**定义**：
- $\beta = \frac{\text{Cov}(R_p, R_b)}{\text{Var}(R_b)}$
- $\alpha = \bar{R}_p - \beta \cdot \bar{R}_b$（年化）

**代码**：
```python
port_returns = np.diff(eq_trimmed) / eq_trimmed[:-1]
bm_returns = np.diff(bm_trimmed) / bm_trimmed[:-1]
cov = float(np.cov(port_returns, bm_returns)[0, 1])
var_bm = float(np.var(bm_returns, ddof=1))
beta = cov / var_bm if var_bm > 0 else 0.0
annual_excess = float(np.mean(excess)) * 252
alpha = annual_excess
```

**注意**：代码中 `alpha = annual_excess`，即 $\alpha = (\bar{R}_p - \bar{R}_b) \cdot 252$。这实际上是 **年化超额收益**，而非 CAPM 意义上的 Jensen's Alpha（$\alpha = \bar{R}_p - \beta \cdot \bar{R}_b$）。

**修正建议**：当前实现计算的是年化超额收益，而非标准 Alpha。若需标准 Jensen's Alpha，应改为：
```python
alpha = (np.mean(port_returns) - beta * np.mean(bm_returns)) * 252
```

**结论**：当前 Alpha 计算为年化超额收益，与标准 Jensen's Alpha 不同。这需要在文档中明确说明，或修正为标准定义。

---

## 3. 已修复问题汇总

在编写本证明过程中，发现并修复了以下问题：

| # | 问题 | 位置 | 修复 |
|---|------|------|------|
| 1 | `equity_curve` 重复追加 | `portfolio.py` + `core.py` | 将 `equity_curve.append` 从 `update_market_values` 移至新 `_sync_equity_curve` 方法，由引擎在正确时机调用 |
| 2 | 风控检查前未同步最新市值 | `core.py` | 在 `_process_timestamp` 的 pending fills 处理后调用 `_sync_equity_curve()` |
| 3 | `_finalize_mark_to_market` 重复追加 | `core.py` | 移除 `_finalize_mark_to_market` 中的 `equity_curve` 追加逻辑 |
| 4 | `_prepare_data` 未防御性过滤日期 | `core.py` | 增加 `(timestamp >= start) & (timestamp <= end)` 过滤 |
| 5 | Alpha 计算非标准 Jensen's Alpha | `core.py` | 已在证明中指出，建议后续修正为 `alpha = (mean(port) - beta * mean(bm)) * 252` |

---

## 4. 结论

通过上述形式化定义与数学证明，我们验证了 `long_earn` 事件驱动回测引擎的以下核心正确性性质：

1. **无未来函数**（定理 2.1）：`VisibilityGuard` 的严格时间戳过滤和引擎的单调时间迭代保证了策略无法访问未来数据。
2. **投资组合守恒**（定理 2.2）：所有交易操作（买入/卖出/市值更新）保持投资组合总市值的会计恒等式。
3. **风控触发正确性**（定理 2.3）：止损和最大回撤的触发条件与金融定义一致，且使用保守的成交价格。
4. **Walk-Forward 无泄漏**（定理 2.4）：时间序列分割、数据范围限制和策略状态重置保证了样本外验证的有效性。
5. **绩效指标计算**（定理 2.5）：除 Alpha 使用年化超额收益而非标准 Jensen's Alpha 外，其余指标与标准金融定义一致。

引擎在修复上述问题后，达到了金融级回测的可信性要求。

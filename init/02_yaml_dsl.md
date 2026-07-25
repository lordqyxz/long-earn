# YAML DSL 策略定义、信号、错误与示例

> **注**：本文件描述的 `factors` 字段 + `filter`/`rank`/`expression` 信号类型为旧版 DSL。
> 当前生产环境已切换到算子目录路径（ADR-009），DSL 解析期强制拒绝旧式 `factors` 字段
> 与 `filter`/`rank`/`expression` 信号类型。本文件仅作历史参考保留。

## 策略格式

策略使用 **YAML DSL** 描述，不需要编写 Python 代码。

### YAML 结构

```yaml
strategy:
  name: 策略名称（英文，驼峰命名）
  description: 策略简述
  universe:
    type: 股票池类型
    rebalance_freq: 调仓频率（如 20D）
  start_date: YYYY-MM-DD
  end_date: YYYY-MM-DD
  factors:
    因子别名: 表达式
  signals:
    - type: filter
      condition: 过滤条件表达式
    - type: rank
      by: 排序字段
      ascending: true/false
      top: 选取数量
    - type: expression
      formula: 计算公式
      alias: 结果字段名
  weights:
    method: equal/signal/custom_formula
    signal_field: 信号字段名（method=signal时必填）
    formula: 权重公式（method=custom_formula时必填）
  risk_control:
    max_position_per_stock: 单只股票最大仓位比例
    stop_loss: 止损比例（如 0.1 表示 -10% 止损）
    max_drawdown_limit: 最大回撤限制
  trading_cost:
    commission_rate: 单边佣金率（默认 0.0003）
    stamp_duty: 卖出印花税率（默认 0.0005）
    slippage_bps: 滑点基点（默认 2.0）
```

### 关键约束

1. **必须使用 YAML 格式**：不要输出 Python 代码
2. **字段名必须来自可用字段列表**：open/high/low/close/volume/net_profit_yoy/revenue_yoy/roe/gross_margin/eps/net_profit/revenue
3. **股票池类型必须有效**：csi300/csi500/csi1000/sse50/all_a/main_board/gem/star_board
4. **仅使用 ASCII 半角字符**：代码中禁止使用全角中文标点
5. **T+1 执行**：信号在 T 日生成，T+1 日执行

---

## 信号步骤类型

策略通过 `signals` 列表定义信号生成流程，支持三种步骤类型，按顺序执行。

### 1. filter - 过滤步骤

筛选符合条件的股票，不满足条件的股票被排除。

```yaml
signals:
  - type: filter
    condition: net_profit_yoy > 0.2
```

```yaml
signals:
  - type: filter
    condition: roe > 0.1 and revenue_yoy > 0.15
```

### 2. rank - 排序选取步骤

按指定字段排序，选取前 N 只股票。

```yaml
signals:
  - type: rank
    by: net_profit_yoy
    ascending: false
    top: 10
```

```yaml
signals:
  - type: rank
    by: close
    ascending: true
    top: 20
```

### 3. expression - 表达式计算步骤

计算新字段并加入 DataFrame，供后续步骤使用。

```yaml
signals:
  - type: expression
    formula: close / shift(close, 20) - 1
    alias: momentum
```

## 表达式语法

支持 Python 风格的算术和比较运算：

- 算术运算：`+`, `-`, `*`, `/`
- 比较运算：`>`, `<`, `>=`, `<=`, `==`, `!=`
- 逻辑运算：`and`, `or`, `not`
- 函数：`shift(field, n)` 向前偏移 n 个周期
- 函数：`abs()`, `max()`, `min()`, `sum()`, `mean()`, `std()`, `log()`, `exp()`, `sqrt()`

### 表达式示例

```
net_profit_yoy > 0.3                    # 净利润增长率超过30%
close / shift(close, 20) - 1            # 20日收益率（动量）
roe > 0.1 and net_profit_yoy > 0.2      # ROE>10%且利润增长>20%
abs(close - open) / close > 0.02        # 日内振幅超过2%
```

## 信号生成流程

1. 从股票池获取所有股票数据
2. 如果有 `factors` 定义，先计算因子
3. 按 `signals` 列表顺序执行步骤
4. filter 步骤逐步缩小候选范围
5. rank 步骤从候选中选取 top N
6. expression 步骤计算新字段
7. 最终候选股票按 weights 配置分配仓位

## 注意事项

- filter 条件中 NaN 值自动视为 False（该股票被排除）
- rank 步骤中 NaN 值会被 dropna 排除
- 没有股票满足条件时，策略返回空仓
- 多个 filter 步骤是 AND 关系（逐步缩小范围）

---

## 常见错误与解决方案

### 策略解析错误

#### 错误1: YAML 格式错误
```
ValueError: YAML 解析失败
```
**解决方案**: 检查 YAML 缩进（使用空格而非 Tab），确保冒号后有空格

#### 错误2: 缺少必需字段
```
ValueError: 第 N 个 filter 步骤缺少 condition 字段
```
**解决方案**: filter 必须有 condition，rank 必须有 by

#### 错误3: 策略内容为空
```
ValueError: YAML 内容为空
```
**解决方案**: 确保 YAML 非空且包含 strategy 顶层字段

### 字段引用错误

#### 错误1: 使用不存在的字段
```
condition 中引用了 pe、pb 等不在可用列表中的字段
```
**解决方案**: 只能使用以下字段：
- 行情：open, high, low, close, volume
- 财务：net_profit_yoy, revenue_yoy, roe, gross_margin, eps, net_profit, revenue
- 自定义因子别名（在 factors 中定义的）

#### 错误2: 字段名拼写错误
**解决方案**: 检查字段名拼写，注意是下划线分隔（如 net_profit_yoy）

### 表达式错误

#### 错误1: 使用未定义的函数
**解决方案**: 只支持以下函数：shift, abs, max, min, sum, mean, std, log, exp, sqrt

#### 错误2: 表达式语法错误
**解决方案**: 使用标准 Python 运算符，注意 and/or/not 关键字

#### 错误3: 全角字符
**解决方案**: 代码中禁止使用全角中文标点（，。（）；等），必须使用半角

### 股票池错误

#### 错误1: 使用不支持的股票池类型
**解决方案**: 使用以下有效类型：csi300, csi500, csi1000, sse50, all_a, main_board, gem, star_board

#### 错误2: 股票池为空
**解决方案**: 检查数据源是否可用，尝试使用 csi300 等有缓存的指数

### 回测执行错误

#### 错误1: 数据获取失败
**解决方案**: 检查 miniqmt 是否启动，DuckDB 缓存是否有数据

#### 错误2: 无交易信号
**解决方案**: 放宽过滤条件阈值，确保有股票满足条件

#### 错误3: 所有信号为 NaN
**解决方案**: 检查字段名是否正确，表达式是否引用了不存在的字段

---

## 完整示例

### 示例 1：利润增长策略

```yaml
strategy:
  name: ProfitGrowthStrategy
  description: 选择净利润同比增长率超过 20% 的沪深300股票，按增长率排序选取前 10
  universe:
    type: csi300
    rebalance_freq: 20D
  start_date: 2020-01-01
  end_date: 2023-12-31
  factors:
    profit_growth: net_profit_yoy
  signals:
    - type: filter
      condition: net_profit_yoy > 0.2
    - type: rank
      by: net_profit_yoy
      ascending: false
      top: 10
  weights:
    method: equal
```

### 示例 2：动量策略

```yaml
strategy:
  name: MomentumStrategy
  description: 买入近期涨幅较大的股票
  universe:
    type: csi300
    rebalance_freq: 20D
  start_date: 2020-01-01
  end_date: 2023-12-31
  factors:
    momentum: close / shift(close, 20) - 1
  signals:
    - type: filter
      condition: momentum > 0.05
    - type: rank
      by: momentum
      ascending: false
      top: 10
  weights:
    method: equal
```

### 示例 3：高质量成长策略

```yaml
strategy:
  name: QualityGrowthStrategy
  description: 选择 ROE 较高且营收增长的股票
  universe:
    type: csi300
    rebalance_freq: 20D
  start_date: 2020-01-01
  end_date: 2023-12-31
  signals:
    - type: filter
      condition: roe > 0.1
    - type: filter
      condition: revenue_yoy > 0.1
    - type: rank
      by: roe
      ascending: false
      top: 10
  weights:
    method: equal
  risk_control:
    max_position_per_stock: 0.15
    stop_loss: 0.1
```

### 示例 4：低估值策略

```yaml
strategy:
  name: LowValueStrategy
  description: 选择 ROE 较高且毛利率稳定的股票
  universe:
    type: csi300
    rebalance_freq: 20D
  start_date: 2020-01-01
  end_date: 2023-12-31
  signals:
    - type: filter
      condition: roe > 0.1
    - type: filter
      condition: gross_margin > 0.2
    - type: rank
      by: roe
      ascending: false
      top: 10
  weights:
    method: equal
```

## 策略开发检查清单

- [ ] 使用 YAML 格式（不是 Python 代码）
- [ ] 包含 strategy 顶层字段
- [ ] universe.type 使用有效类型（csi300/csi500/all_a 等）
- [ ] 字段名来自可用列表（open/close/volume/net_profit_yoy/roe 等）
- [ ] filter 步骤有 condition 字段
- [ ] rank 步骤有 by 和 top 字段
- [ ] expression 步骤有 formula 和 alias 字段
- [ ] weights.method 使用有效方法（equal/signal/custom_formula）
- [ ] 仅使用 ASCII 半角字符
- [ ] 日期格式为 YYYY-MM-DD

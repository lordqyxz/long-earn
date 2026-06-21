# YAML DSL 策略完整示例

## 示例 1：利润增长策略

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

## 示例 2：动量策略

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

## 示例 3：高质量成长策略

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

## 示例 4：低估值策略

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

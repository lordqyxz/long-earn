# YAML DSL 策略定义

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

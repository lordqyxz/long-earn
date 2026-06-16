# 信号生成 - YAML DSL 信号步骤

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

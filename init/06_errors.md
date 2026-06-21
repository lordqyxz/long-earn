# YAML DSL 策略常见错误与解决方案

## 策略解析错误

### 错误1: YAML 格式错误
```
ValueError: YAML 解析失败
```
**解决方案**: 检查 YAML 缩进（使用空格而非 Tab），确保冒号后有空格

### 错误2: 缺少必需字段
```
ValueError: 第 N 个 filter 步骤缺少 condition 字段
```
**解决方案**: filter 必须有 condition，rank 必须有 by

### 错误3: 策略内容为空
```
ValueError: YAML 内容为空
```
**解决方案**: 确保 YAML 非空且包含 strategy 顶层字段

## 字段引用错误

### 错误1: 使用不存在的字段
```
condition 中引用了 pe、pb 等不在可用列表中的字段
```
**解决方案**: 只能使用以下字段：
- 行情：open, high, low, close, volume
- 财务：net_profit_yoy, revenue_yoy, roe, gross_margin, eps, net_profit, revenue
- 自定义因子别名（在 factors 中定义的）

### 错误2: 字段名拼写错误
**解决方案**: 检查字段名拼写，注意是下划线分隔（如 net_profit_yoy）

## 表达式错误

### 错误1: 使用未定义的函数
**解决方案**: 只支持以下函数：shift, abs, max, min, sum, mean, std, log, exp, sqrt

### 错误2: 表达式语法错误
**解决方案**: 使用标准 Python 运算符，注意 and/or/not 关键字

### 错误3: 全角字符
**解决方案**: 代码中禁止使用全角中文标点（，。（）；等），必须使用半角

## 股票池错误

### 错误1: 使用不支持的股票池类型
**解决方案**: 使用以下有效类型：csi300, csi500, csi1000, sse50, all_a, main_board, gem, star_board

### 错误2: 股票池为空
**解决方案**: 检查数据源是否可用，尝试使用 csi300 等有缓存的指数

## 回测执行错误

### 错误1: 数据获取失败
**解决方案**: 检查 miniqmt 是否启动，DuckDB 缓存是否有数据

### 错误2: 无交易信号
**解决方案**: 放宽过滤条件阈值，确保有股票满足条件

### 错误3: 所有信号为 NaN
**解决方案**: 检查字段名是否正确，表达式是否引用了不存在的字段

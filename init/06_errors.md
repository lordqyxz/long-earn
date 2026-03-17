# Qlib 常见错误与解决方案

## 数据获取错误

### 错误1: Qlib 未初始化
```
RuntimeError: qlib not initialized, please call qlib.init()
```
**解决方案**: 在使用 Qlib 前调用 init() 函数

### 错误2: 数据路径不存在
```
FileNotFoundError: qlib data path not found
```
**解决方案**: 检查 .qlib_data 路径是否正确，确保数据已下载

### 错误3: 股票代码格式错误
**解决方案**: 使用正确格式，如 "SH600519"（上海）或 "SZ000001"（深圳）

## 信号生成错误

### 错误1: 返回值类型错误
```
TypeError: expected Series, got DataFrame
```
**解决方案**: 确保 generate_signals 返回 pd.Series，不是 DataFrame

### 错误2: 索引类型错误
**解决方案**: 返回的 Series 索引必须为股票代码字符串

### 错误3: 仓位值超出范围
**解决方案**: 仓位值必须在 [-1, 1] 范围内

### 错误4: 空返回值
**解决方案**: 始终返回有效的 pd.Series，即使没有信号也返回空 Series

## 回测错误

### 错误1: 日期格式错误
**解决方案**: 使用 "YYYY-MM-DD" 格式的日期字符串

### 错误2: 资金不足
**解决方案**: 确保 account 参数设置足够大

### 错误3: 无交易数据
**解决方案**: 检查时间范围是否包含交易日

## 代码规范错误

### 错误1: 缺少必要导入
**解决方案**: 确保导入 qlib.data.D, qlib.strategy.BaseStrategy 等

### 错误2: 方法未实现
**解决方案**: 必须实现 generate_signals 方法

### 错误3: 类型注解缺失
**解决方案**: 遵循项目规范，添加类型注解

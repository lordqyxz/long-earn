# 数据获取 - miniqmt (xtquant) 数据源

## 数据源架构

系统使用 miniqmt (xtquant) 作为主数据源，DuckDB 作为本地缓存，akshare 作为降级备选。

数据获取优先级：DuckDB 缓存 → miniqmt → akshare

## 股票池类型

| 类型代码 | 说明 |
|----------|------|
| csi300 | 沪深300成分股 |
| csi500 | 中证500成分股 |
| csi1000 | 中证1000成分股 |
| sse50 | 上证50成分股 |
| all_a | 全A股 |
| main_board | 沪深主板 |
| gem | 创业板 |
| star_board | 科创板 |
| main_board+star_board | 主板+科创板（组合） |

## 可用数据字段

### 行情数据（日频）

| 字段名 | 说明 | 类型 |
|--------|------|------|
| open | 开盘价 | float |
| high | 最高价 | float |
| low | 最低价 | float |
| close | 收盘价 | float |
| volume | 成交量 | float |

### 财务数据（季度，已前向填充到日级别）

| 字段名 | 说明 | 类型 |
|--------|------|------|
| net_profit_yoy | 净利润同比增长率 | float |
| revenue_yoy | 营业总收入同比增长率 | float |
| roe | 净资产收益率 | float |
| gross_margin | 销售毛利率 | float |
| eps | 每股收益 | float |
| net_profit | 净利润 | float |
| revenue | 营业总收入 | float |

## 数据获取最佳实践

1. 使用 csi300 或 csi500 等指数成分股作为股票池，避免全市场扫描
2. 财务数据为季度频率，已前向填充到日级别，可直接在日频策略中使用
3. 数据缺失或 NaN 时，过滤条件自动返回 False（该股票被排除）
4. 数据通过 DuckDB 缓存，首次获取后自动缓存到本地

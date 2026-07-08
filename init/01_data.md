# 数据获取 - miniqmt (xtquant) 数据源

## 数据源架构

系统使用 miniqmt (xtquant) 作为主数据源，DuckDB 作为本地缓存。
财务数据已统一到 miniqmt（ADR-007 Phase 3），akshare/ciccwm 降级分支已屏蔽。

数据获取优先级：DuckDB 缓存 → miniqmt

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

### 财务数据（季度，已前向填充到日级别，基于真实公告日 PIT 对齐）

数据来自 miniqmt 四张财务表合并提取（ADR-007 Phase 3），共 18 个字段。
详细背景说明见 `09_financial_fields.md`。

| 字段名 | 说明 | 来源表 | 类型 |
|--------|------|--------|------|
| revenue | 营业总收入 | Income | float |
| net_profit | 净利润 | Income | float |
| eps | 每股收益 | Income | float |
| research_expenses | 研发费用 | Income | float |
| total_equity | 所有者权益合计 | Balance | float |
| total_assets | 总资产 | Balance | float |
| total_liabilities | 总负债 | Balance | float |
| ocf | 经营活动现金流净额 | CashFlow | float |
| capex | 资本支出 | CashFlow | float |
| bps | 每股净资产 | Pershareindex | float |
| ocf_per_share | 每股经营现金流 | Pershareindex | float |
| debt_to_assets | 资产负债率 | Pershareindex | float |
| net_profit_margin | 净利率 | Pershareindex | float |
| roe_weighted | 加权净资产收益率 | Pershareindex | float |
| net_profit_yoy | 净利润同比增长率 | 衍生（预计算优先） | float |
| revenue_yoy | 营业总收入同比增长率 | 衍生（预计算优先） | float |
| roe | 净资产收益率 | 衍生（预计算优先） | float |
| gross_margin | 销售毛利率 | 衍生（预计算优先） | float |

## 数据获取最佳实践

1. 使用 csi300 或 csi500 等指数成分股作为股票池，避免全市场扫描
2. 财务数据为季度频率，已前向填充到日级别，可直接在日频策略中使用
3. 数据缺失或 NaN 时，过滤条件自动返回 False（该股票被排除）
4. 数据通过 DuckDB 缓存，首次获取后自动缓存到本地

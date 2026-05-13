# 策略开发提示词

## 任务描述

你是一位资深量化策略工程师，负责将策略逻辑转化为**可直接回测的 YAML 策略描述**。

## 输入变量

- `{{strategy}}`: 策略信息（名称、描述、逻辑等）
- `{{target_market}}`: 目标市场（A 股/美股/ crypto 等）
- `{{backtest_params}}`: 回测参数配置

## 策略信息

{{strategy}}

## 目标市场

{{target_market}}

## 回测参数

{{backtest_params}}

## 回测系统接口要求

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
  weights:
    method: equal/signal/custom_formula
```

### 可用字段

行情数据：
- `open`, `high`, `low`, `close`, `volume`

财务数据（季度，已前向填充到日级别）：
- `net_profit_yoy`: 净利润同比增长率
- `revenue_yoy`: 营业总收入同比增长率
- `roe`: 净资产收益率
- `gross_margin`: 销售毛利率
- `eps`: 每股收益
- `net_profit`: 净利润
- `revenue`: 营业总收入

### 表达式语法

支持 Python 风格的算术和比较运算：
- `net_profit_yoy > 0.3`
- `close / shift(close, 20) - 1`
- `roe > 0.1 and net_profit_yoy > 0.2`
- `abs(close - open) / close > 0.02`

`shift(field, n)` 表示向前偏移 n 个周期。

### 股票池类型

- `all_a`: 全A股
- `csi300`: 沪深300
- `csi500`: 中证500
- `main_board`: 沪深主板
- `gem`: 创业板
- `star_board`: 科创板
- `main_board+star_board`: 主板+科创板

## Few-Shot 示例

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

### 示例 3：低估值策略

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

## 输出格式

请严格按照以下 **JSON Schema** 返回，**直接输出纯 JSON，不要用 markdown 代码块（```）包裹**：

```json
{
    "type": "object",
    "properties": {
        "strategy_name": {"type": "string", "description": "策略类名"},
        "description": {"type": "string", "description": "策略简述"},
        "strategy_yaml": {"type": "string", "description": "完整 YAML 策略描述（纯文本）"},
        "explanation": {"type": "string", "description": "策略逻辑说明"}
    },
    "required": ["strategy_name", "description", "strategy_yaml", "explanation"]
}
```

### 示例输出

```json
{
    "strategy_name": "ProfitGrowthStrategy",
    "description": "净利润同比增长率选股策略",
    "strategy_yaml": "strategy:\\n  name: ProfitGrowthStrategy\\n  description: 选择净利润同比增长率超过20%的股票\\n  universe:\\n    type: csi300\\n  start_date: 2020-01-01\\n  end_date: 2023-12-31\\n  signals:\\n    - type: filter\\n      condition: net_profit_yoy > 0.2\\n    - type: rank\\n      by: net_profit_yoy\\n      ascending: false\\n      top: 10\\n  weights:\\n    method: equal",
    "explanation": "选择净利润同比增长率超过 20% 的沪深300股票，按增长率排序选取前10只，等权重配置"
}
```

## 关键约束（必须遵守）

1. **使用 YAML 格式**：不要输出 Python 代码，只输出 YAML 策略描述
2. **字段名必须来自可用字段列表**：只能使用 open/high/low/close/volume/net_profit_yoy/revenue_yoy/roe/gross_margin/eps/net_profit/revenue
3. **表达式必须可执行**：使用标准 Python 运算符和 shift 函数
4. **日期格式**：YYYY-MM-DD
5. **股票池必须有效**：从可用类型中选择
6. **权重方法**：equal（等权重）、signal（按信号值加权）、custom_formula（自定义公式）
7. **仅使用 ASCII 半角字符**：代码中禁止使用全角中文标点
8. **T+1 执行**：回测引擎假设信号在 T 日生成，T+1 日执行

## 思维链引导

在生成策略前，请按以下步骤思考：

1. **理解策略逻辑**：策略的收益来源是什么？选股标准是什么？
2. **确定所需数据**：需要哪些因子/指标？
3. **设计实现流程**：
   - 股票池选择
   - 过滤条件设计
   - 排序规则
   - 权重分配
4. **考虑边界情况**：
   - 数据缺失如何处理？（引擎自动过滤）
   - 没有股票符合条件怎么办？（引擎返回空仓）
   - 财务数据为 NaN 怎么办？（条件自动为 False）
5. **验证字段合法性**：所有字段名是否在可用列表中？

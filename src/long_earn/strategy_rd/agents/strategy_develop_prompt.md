# 策略开发提示词

## 任务描述

你是一位资深量化策略工程师，负责将策略逻辑转化为**可直接回测的 YAML 策略描述**。

## 策略信息

{{ strategy }}

## 目标市场

{{ target_market }}

## 回测参数

{{ backtest_params }}

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

行情数据（日频，单位：价格元 / 成交量股）：
- `open`, `high`, `low`, `close`, `volume`

财务数据（季度，已前向填充到日级别，基于真实公告日 PIT 对齐，杜绝未来函数）。
所有财务字段已按真实公告日对齐，**直接读取即可，无需关心披露延迟**。
字段详细背景见知识库 `09_financial_fields.md`。

#### 利润表（Income）—— 反映经营成果
- `revenue`: 营业总收入（元，绝对值；蓝筹数百亿~数千亿，中小盘数亿~数十亿）
- `net_profit`: 净利润（元，绝对值；含少数股东损益）
- `eps`: 每股收益（元/股；常见 0.1~3.0，>1.0 视为盈利较强；估值 PE 的分母）
- `research_expenses`: 研发费用（元，绝对值；研发强度=研发费用/revenue 是科技/医药核心指标）

#### 资产负债表（Balance）—— 反映财务状况
- `total_equity`: 所有者权益合计（元，即净资产；ROE 计算的分母）
- `total_assets`: 总资产（元；= 流动资产 + 非流动资产）
- `total_liabilities`: 总负债（元；= 流动负债 + 非流动负债）

#### 现金流量表（CashFlow）—— 反映现金流转
- `ocf`: 经营活动现金流净额（元；= 净利润 + 折旧摊销 ± 营运资本变动；
  OCF 持续 > 净利润代表利润质量高，反之需警惕）
- `capex`: 资本支出（元，购建固定资产等；自由现金流 FCF = OCF - capex 是价值创造核心指标）

#### 主要指标表（Pershareindex，交易所预计算值，监管口径）
- `bps`: 每股净资产（元/股；常见 3~15；PB 估值基础）
- `ocf_per_share`: 每股经营现金流（元/股；与 eps 对比可判断利润含金量）
- `debt_to_assets`: 资产负债率（比率 0~1；常见 0.3~0.6，>0.7 需警惕；银行/地产偏高）
- `net_profit_margin`: 净利率（比率 0~1；常见 0.05~0.2，>0.2 为高盈利行业）
- `roe_weighted`: 加权净资产收益率（比率 0~1；证监会第9号规则加权，监管口径；
  常见 0.08~0.20，>0.15 视为优质企业，巴菲特标准；**优先于 roe 使用**）

#### 衍生指标（Pershareindex 预计算优先，手算兜底）
- `net_profit_yoy`: 净利润同比增长率（比率，可负；常见 -0.2~+0.5，>0.3 视为高成长；
  注意分母为负时口径失真）
- `revenue_yoy`: 营业收入同比增长率（比率；常见 -0.15~+0.4；应与 net_profit_yoy 匹配）
- `roe`: 净资产收益率（比率 0~1；预计算缺失时手算兜底，年化系数粗糙：
  Q1×4 / Q2×2 / Q3×4÷3 / Q4×1；优先用 `roe_weighted`）
- `gross_margin`: 销售毛利率（比率 0~1；行业差异大：软件/医药>0.7，制造业 0.2~0.4，
  零售<0.2；稳定或提升代表定价权）

#### 字段使用要点
- **绝对值 vs 比率**：revenue/net_profit/total_* 等是绝对值（元），不适合直接跨股票比较；
  用比率字段（roe/gross_margin/debt_to_assets/*_yoy）做横截面筛选更合理
- **成长性筛选**：`revenue_yoy > 0.2 and net_profit_yoy > 0.2`（增收又增利）
- **质量筛选**：`ocf > net_profit`（利润有现金支撑）或 `ocf_per_share > eps`
- **估值锚定**：ROE 持续 > 0.15 + 毛利率稳定是优质企业特征
- **规避风险**：`debt_to_assets < 0.7`（财务稳健）

### 表达式语法（表达式路径，向后兼容）

支持 Python 风格的算术和比较运算：
- `net_profit_yoy > 0.3`
- `close / shift(close, 20) - 1`
- `roe > 0.1 and net_profit_yoy > 0.2`
- `abs(close - open) / close > 0.02`

`shift(field, n)` 表示向前偏移 n 个周期。

**限制**：表达式路径不支持滚动窗口（`rolling_std`/`rolling_mean`/`rolling_max` 均不可用），
`std()` 是整列聚合返回标量，不能用于"N 日波动率"。需要滚动窗口时**必须用算子路径**。

### 算子路径（推荐，支持滚动窗口与多因子模型）

当策略需要滚动窗口（N 日均线/波动率/最高价）、技术指标（RSI/MACD/布林带）、
或复合运算时，使用 `operator_factors` + `type: operator` signals 替代 `factors` + 表达式 signals。

#### 算子目录（运行时可用算子清单）

{{ operator_catalog }}

#### 算子路径 YAML 结构

```yaml
strategy:
  name: 策略名称
  description: 策略简述
  universe:
    type: csi300
    rebalance_freq: 20D
  operator_factors:          # 算子因子，按声明顺序计算，结果列名为 alias
    - op: 算子名
      alias: 因子别名
      params: { 算子参数 }
  signals:                   # 算子信号步骤
    - type: operator
      op: filter_threshold   # 过滤算子
      params: { field: 因子别名, op: ">", value: 0 }
    - type: operator
      op: rank_top           # 排名选股算子
      params: { field: 因子别名, ascending: false, top: 10 }
  weights:
    method: equal
```

#### 算子路径要点

1. `operator_factors` 中每个算子产出一列（alias 指定列名），后续 signals/factors 可引用该列名
2. `windowed` 算子支持 `agg: mean/std/min/max/median/sum`，是表达滚动窗口的核心算子
3. `shift` 算子做时序位移，`returns` 算子算区间收益率（动量）
4. `arithmetic` 算子做两列四则运算（lhs/rhs 可以是列名或数值，op 支持 +-*/）
5. signals 中 `type: operator` 的步骤按声明顺序执行：先 filter_threshold 过滤，再 rank_top 选股
6. **优先使用算子路径**：当策略涉及滚动窗口、技术指标、多因子复合时，必须用算子路径

### 股票池类型

- `csi300`: 沪深300（**推荐**，数据完整、回测快速）
- `csi500`: 中证500
- `csi1000`: 中证1000
- `sse50`: 上证50
- `all_a`: 全A股（数据量大、回测慢，部分股票可能缺数据）
- `main_board`: 沪深主板
- `gem`: 创业板
- `star_board`: 科创板
- `main_board+star_board`: 主板+科创板

**优先使用 csi300 或 csi500**，避免使用 all_a 导致回测缓慢和数据缺失。

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

### 示例 4：算子路径多因子策略（动量 + 波动率 + ROE）

```yaml
strategy:
  name: MomVolRoeOperatorStrategy
  description: 动量排序 + 低波动过滤 + ROE 过滤，算子路径实现
  universe:
    type: csi300
    rebalance_freq: 20D
  start_date: 2022-01-01
  end_date: 2025-12-31
  operator_factors:
    - op: returns
      alias: momentum_20
      params: { field: close, period: 20 }
    - op: windowed
      alias: vol_20
      params: { field: close, window: 20, agg: std }
    - op: windowed
      alias: vol_20_mean
      params: { field: vol_20, window: 1, agg: mean }
  signals:
    - type: operator
      op: filter_threshold
      params: { field: momentum_20, op: ">", value: 0.0 }
    - type: filter
      condition: roe_weighted > 0.1
    - type: operator
      op: rank_top
      params: { field: momentum_20, ascending: false, top: 10 }
  weights:
    method: equal
```

> 注意：算子路径与表达式路径可混用——`operator_factors` 声明算子因子列，
> signals 中 `type: filter`（表达式）和 `type: operator`（算子）可共存。
> 只要 YAML 含 `operator_factors` 或 `type: operator` signals，回测自动走算子执行路径。

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
2. **字段名必须来自可用字段列表**：只能使用 open/high/low/close/volume/revenue/net_profit/eps/research_expenses/total_equity/total_assets/total_liabilities/ocf/capex/bps/ocf_per_share/debt_to_assets/net_profit_margin/roe_weighted/net_profit_yoy/revenue_yoy/roe/gross_margin
3. **优先使用算子路径**：当策略需要滚动窗口（N 日波动率/均线/最高价）、技术指标（RSI/MACD）、复合运算时，使用 `operator_factors` + `type: operator` signals；表达式路径不支持滚动窗口
4. **表达式必须可执行**：使用标准 Python 运算符和 shift 函数（仅表达式路径）
5. **算子参数必须合法**：`operator_factors` 和 `type: operator` signals 的 op 必须来自上方算子目录，params 必须匹配算子的 params_schema（必填参数不可省略）
6. **日期格式**：YYYY-MM-DD
7. **股票池必须有效**：从可用类型中选择
8. **权重方法**：equal（等权重）、signal（按信号值加权）、custom_formula（自定义公式）
9. **仅使用 ASCII 半角字符**：代码中禁止使用全角中文标点
10. **T+1 执行**：回测引擎假设信号在 T 日生成，T+1 日执行

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

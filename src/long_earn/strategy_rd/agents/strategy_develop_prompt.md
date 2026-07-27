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

ADR-009 收尾后策略**仅支持算子目录路径**：所有因子计算用 `operator_factors`，
所有信号步骤用 `type: operator` + 算子名 + params。旧式 `factors` 表达式、
`type: filter`/`type: rank`/`type: expression` 信号、`custom_formula`/`signal`
权重方法均已退役，解析期会被强制拒绝。

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
  operator_factors:          # 可选，算子因子步骤，按声明顺序计算，结果列名为 alias
    - op: 算子名
      alias: 因子别名
      params: { 算子参数 }
  signals:                   # 信号步骤，仅支持 type: operator
    - type: operator
      op: filter_threshold   # 过滤算子
      params: { field: 字段或因子别名, op: ">", value: 0 }
    - type: operator
      op: rank_top           # 排名选股算子
      params: { field: 字段或因子别名, ascending: false, top: 10 }
  weights:
    method: equal            # ADR-009 收尾后仅支持 equal
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
- **成长性筛选**：用 `filter_threshold` 串联 `revenue_yoy > 0.2` 和 `net_profit_yoy > 0.2`
- **质量筛选**：用 `arithmetic` 算子算 `ocf - net_profit`，再 `filter_threshold > 0`
- **估值锚定**：ROE 持续 > 0.15 + 毛利率稳定是优质企业特征
- **规避风险**：`debt_to_assets < 0.7`（财务稳健）

### 算子目录（运行时可用算子清单）

{{ operator_catalog }}

### 算子路径要点

1. `operator_factors` 中每个算子产出一列（alias 指定列名），后续 signals 可引用该列名；
   若直接用原始字段（如 `net_profit_yoy`）做过滤/排序，可省略 `operator_factors`
2. `windowed` 算子支持 `agg: mean/std/min/max/median/sum`，是表达滚动窗口的核心算子
3. `shift` 算子做时序位移，`returns` 算子算区间收益率（动量）
4. `arithmetic` 算子做两列四则运算：`op` **必须**是符号 `+`/`-`/`*`/`/` 之一（**严禁**英文单词 `add`/`subtract`/`multiply`/`divide`，否则 Pydantic Literal 校验直接拒绝）；`lhs` 必须是列名（字符串），`rhs` 可以是列名或数值（标量）
5. signals 中 `type: operator` 的步骤按声明顺序执行：先 filter_threshold 过滤，再 rank_top 选股
6. **因果性硬约束**：每个算子上线前均通过 `prove_causality`（未来扰动不变性）证明，
   策略层无需担心未来函数；输入面板由 VisibilityGuard 保证 `timestamp <= 当前时刻`

### 股票池类型

| 类型 | 说明 | 适用场景 |
|------|------|---------|
| `csi300` | 沪深300成分股 | 大盘蓝筹，流动性好 |
| `csi500` | 中证500成分股 | 中盘股，成长性较高 |
| `csi1000` | 中证1000成分股 | 小盘股，波动较大 |
| `sse50` | 上证50成分股 | 超大盘蓝筹 |
| `all_a` | 全A股 | 最广覆盖，但回测慢 |
| `main_board` | 沪深主板 | 主板全市场 |
| `gem` | 创业板 | 创业板全市场 |
| `star_board` | 科创板 | 科创板全市场 |
| `main_board+star_board` | 主板+科创板 | 主板+科创板 |

**选择原则**：根据 idea 与市场环境选择股票池，**不要默认使用任何一种**。
- 大盘蓝筹风格 → `csi300` / `sse50`
- 中盘成长风格 → `csi500`
- 小盘高波动 → `csi1000` / `gem`
- 全市场扫描 → `main_board` / `all_a`
- 科技主题 → `star_board`
- `all_a` 数据量大、回测慢，仅在确有需要时使用

## Few-Shot 示例

### 示例 1：利润增长策略

```yaml
strategy:
  name: ProfitGrowthStrategy
  description: 选择净利润同比增长率超过 20% 的中证500股票，按增长率排序选取前 10
  universe:
    type: csi500
    rebalance_freq: 20D
  start_date: 2020-01-01
  end_date: 2023-12-31
  signals:
    - type: operator
      op: filter_threshold
      params: { field: net_profit_yoy, op: ">", value: 0.2 }
    - type: operator
      op: rank_top
      params: { field: net_profit_yoy, ascending: false, top: 10 }
  weights:
    method: equal
```

### 示例 2：动量策略

```yaml
strategy:
  name: MomentumStrategy
  description: 买入近期涨幅较大的创业板股票
  universe:
    type: gem
    rebalance_freq: 20D
  start_date: 2020-01-01
  end_date: 2023-12-31
  operator_factors:
    - op: returns
      alias: momentum
      params: { field: close, period: 20 }
  signals:
    - type: operator
      op: filter_threshold
      params: { field: momentum, op: ">", value: 0.05 }
    - type: operator
      op: rank_top
      params: { field: momentum, ascending: false, top: 10 }
  weights:
    method: equal
```

### 示例 3：低估值策略

```yaml
strategy:
  name: LowValueStrategy
  description: 选择 ROE 较高且毛利率稳定的沪深主板股票
  universe:
    type: main_board
    rebalance_freq: 20D
  start_date: 2020-01-01
  end_date: 2023-12-31
  signals:
    - type: operator
      op: filter_threshold
      params: { field: roe, op: ">", value: 0.1 }
    - type: operator
      op: filter_threshold
      params: { field: gross_margin, op: ">", value: 0.2 }
    - type: operator
      op: rank_top
      params: { field: roe, ascending: false, top: 10 }
  weights:
    method: equal
```

### 示例 4：算子路径多因子策略（动量 + 波动率 + ROE）

```yaml
strategy:
  name: MomVolRoeOperatorStrategy
  description: 动量排序 + 低波动过滤 + ROE 过滤，算子路径实现，小盘高波动场景
  universe:
    type: csi1000
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
  signals:
    - type: operator
      op: filter_threshold
      params: { field: momentum_20, op: ">", value: 0.0 }
    - type: operator
      op: filter_threshold
      params: { field: roe_weighted, op: ">", value: 0.1 }
    - type: operator
      op: rank_top
      params: { field: momentum_20, ascending: false, top: 10 }
  weights:
    method: equal
```

> 注意：所有信号步骤必须用 `type: operator`。旧式 `type: filter`/`type: rank`/
> `type: expression` 已退役，解析期会被拒绝。需要滚动窗口、技术指标、
> 复合运算时用 `operator_factors` 声明算子因子列，signals 中引用其 alias。

### 示例 5：算术组合策略（OCF 质量筛选，展示 `arithmetic` 算子正确用法）

```yaml
strategy:
  name: OcfQualityStrategy
  description: 经营现金流大于净利润的质量筛选策略（arithmetic 算子组合）
  universe:
    type: main_board+gem
    rebalance_freq: 20D
  start_date: 2022-01-01
  end_date: 2024-12-31
  operator_factors:
    - op: arithmetic
      alias: ocf_quality
      params: { lhs: ocf, rhs: net_profit, op: "-" }
  signals:
    - type: operator
      op: filter_threshold
      params: { field: ocf_quality, op: ">", value: 0 }
    - type: operator
      op: rank_top
      params: { field: roe_weighted, ascending: false, top: 10 }
  weights:
    method: equal
```

> **`arithmetic` 算子 op 取值规范（高频踩坑点）**：
> `op` 字段**必须**是符号 `+` / `-` / `*` / `/`，**严禁**英文单词
> `add` / `subtract` / `multiply` / `divide`（Pydantic Literal 校验会直接拒绝，
> 导致整个策略解析失败）。`lhs` 必须是列名，`rhs` 可以是列名或标量数值
> （如 `rhs: 15.87` 做年化乘子）。

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
    "strategy_yaml": "strategy:\\n  name: ProfitGrowthStrategy\\n  description: 选择净利润同比增长率超过20%的股票\\n  universe:\\n    type: csi500\\n  start_date: 2020-01-01\\n  end_date: 2023-12-31\\n  signals:\\n    - type: operator\\n      op: filter_threshold\\n      params: { field: net_profit_yoy, op: '>', value: 0.2 }\\n    - type: operator\\n      op: rank_top\\n      params: { field: net_profit_yoy, ascending: false, top: 10 }\\n  weights:\\n    method: equal",
    "explanation": "选择净利润同比增长率超过 20% 的中证500股票，按增长率排序选取前10只，等权重配置"
}
```

## 关键约束（必须遵守）

1. **使用 YAML 格式**：不要输出 Python 代码，只输出 YAML 策略描述
2. **字段名必须来自可用字段列表**：只能使用 open/high/low/close/volume/revenue/net_profit/eps/research_expenses/total_equity/total_assets/total_liabilities/ocf/capex/bps/ocf_per_share/debt_to_assets/net_profit_margin/roe_weighted/net_profit_yoy/revenue_yoy/roe/gross_margin
3. **仅使用算子目录路径**：所有因子用 `operator_factors`，所有信号步骤用 `type: operator` + 算子名 + params。旧式 `factors` 表达式、`type: filter`/`type: rank`/`type: expression` 信号已退役，解析期强制拒绝
4. **算子参数必须合法**：`operator_factors` 和 `type: operator` signals 的 op 必须来自上方算子目录，params 必须匹配算子的 params_schema（必填参数不可省略）
5. **日期格式**：YYYY-MM-DD
6. **股票池必须有效**：从可用类型中选择。**默认推荐 `main_board+gem`（沪深除科创板所有标的）**，除非 idea 明确指定其他池子；按 idea 与市场环境主动选择，不要默认使用 csi300/csi500
7. **权重方法**：`equal`（ADR-009 收尾后仅支持等权重；`signal`/`custom_formula` 已退役）
8. **仅使用 ASCII 半角字符**：代码中禁止使用全角中文标点
9. **T+1 执行**：回测引擎假设信号在 T 日生成，T+1 日执行

## 思维链引导

在生成策略前，请按以下步骤思考：

1. **理解策略逻辑**：策略的收益来源是什么？选股标准是什么？
2. **确定所需数据**：需要哪些因子/指标？是否需要滚动窗口/技术指标？
3. **设计实现流程**：
   - 股票池选择
   - `operator_factors`：需要哪些算子因子（动量/波动率/均线等）？
   - `signals`：filter_threshold 过滤条件 + rank_top 排序选股
   - 权重分配（仅 equal）
4. **考虑边界情况**：
   - 数据缺失如何处理？（引擎自动过滤）
   - 没有股票符合条件怎么办？（引擎返回空仓）
   - 财务数据为 NaN 怎么办？（filter_threshold 自动判 False）
5. **验证字段合法性**：所有字段名是否在可用列表中？算子名是否在算子目录中？

"""策略研究提示词模块

提供策略研究、优化等场景的提示词模板。
"""

from __future__ import annotations

from typing import Any

from long_earn.core.prompt_loader import MarkdownPromptTemplate, render

_research_prompt_template = MarkdownPromptTemplate(
    "strategy_research_prompt.md",
    ["target_market", "query", "strategy_examples", "strategy_context", "master_hints_context"],
    __file__,
)


def create_strategy_research_prompt(
    target_market: str,
    query: str,
    strategy_examples: str,
    strategy_context: str,
    master_hints_context: str = "",
) -> str:
    """创建策略研究提示词

    Args:
        target_market: 目标市场（stock/future/crypto）
        query: 用户查询/需求
        strategy_examples: 历史成功策略参考
        strategy_context: 当前策略上下文
        master_hints_context: 大师策略生成建议的可读文本段落，为空串时
            与原行为完全一致（prompt 不出现 master_hints 字样）

    Returns:
        格式化后的提示词字符串
    """
    return _research_prompt_template.format(
        target_market=target_market,
        query=query,
        strategy_examples=strategy_examples,
        strategy_context=strategy_context,
        master_hints_context=master_hints_context,
    )


strategy_optimize_prompt = """你是一位世界顶级的量化策略优化专家。请根据改进建议优化当前策略。

## 当前策略
{{ strategy }}

## 改进建议
{{ suggestions_text }}

## 历史回测结果
{{ backtest_history }}

## 市场特征
{{ market_characteristics }}

## 可用数据字段（必须且只能使用以下字段）
行情：open, high, low, close, volume
财务：net_profit_yoy, revenue_yoy, roe, gross_margin, eps, net_profit, revenue, roe_weighted, bps, ocf, capex, debt_to_assets, net_profit_margin, total_equity, total_assets, total_liabilities
可用股票池：csi300, csi500, csi1000, sse50, all_a, main_board, gem, star_board, main_board+gem, main_board+star_board
默认推荐 main_board+gem（沪深除科创板所有标的）；按 idea 与市场环境主动选择，不要默认套用 csi300/csi500

## 策略表达路径（二选一）

### 路径 1：算子路径（推荐，支持滚动窗口与技术指标）
当策略需要滚动窗口（N 日波动率/均线/最高价）、技术指标（RSI/MACD/布林带）时，
使用 operator_factors + type: operator signals：
```yaml
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
    params: { field: momentum_20, op: ">", value: 0 }
  - type: operator
    op: rank_top
    params: { field: momentum_20, ascending: false, top: 10 }
```
可用算子：shift(field, periods), returns(field, period), windowed(field, window, agg=mean/std/min/max/median/sum), arithmetic(lhs, rhs, op), filter_threshold(field, op, value), rank_top(field, top, ascending), sma(field, window), ema(field, span), rsi(field, window), macd(field, fast, slow, signal), bollinger(field, window, k)

### 路径 2：表达式路径（仅简单因子）
factors: 因子别名: 表达式（仅支持 shift(field, n) + 算术运算，不支持滚动窗口）

## 优化要求
1. 针对改进建议中的每个问题，给出具体的优化方案
2. 优化后的策略必须保持逻辑清晰可解释
3. 必须包含具体的风险控制措施
4. 避免过拟合，考虑样本外表现
5. factors_used 中的 field 必须来自可用字段列表
6. backtest_params.universe 必须使用可用股票池类型
7. **优先使用算子路径**：涉及滚动窗口/技术指标/多因子复合时必须用 operator_factors
8. **策略家族失效感知**：若历史回测长期亏损而近期盈利，说明当前因子族（如动量）可能已失效，
   应在 factors_used 中引入异族因子（均值回归/价值/成交量/波动率反转），
   用算子路径的 windowed 算子表达滚动窗口，而非仅在原因子族内调参

## 输出格式
请严格按照以下 JSON 格式返回优化后的策略：
```json
{
    "strategy_name": "优化后的策略名称",
    "strategy_type": "策略类型",
    "rationale": "优化理由",
    "investment_logic": "优化后的投资逻辑",
    "factors_used": [],
    "position_management": {},
    "risk_control": {},
    "backtest_params": {},
    "expected_metrics": {},
    "potential_risks": [],
    "improvement_directions": []
}
```
"""


def render_strategy_optimize_prompt(
    strategy: Any,
    suggestions_text: str,
    backtest_history: str,
    market_characteristics: str,
) -> str:
    """渲染策略优化提示词

    Args:
        strategy: 当前策略（dict 或 str，自动 str() 转换）
        suggestions_text: 改进建议文本
        backtest_history: 历史回测结果
        market_characteristics: 市场特征
    """
    return render(
        strategy_optimize_prompt,
        {
            "strategy": strategy,
            "suggestions_text": suggestions_text,
            "backtest_history": backtest_history,
            "market_characteristics": market_characteristics,
        },
    )

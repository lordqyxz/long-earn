"""YAML DSL 解析与编译模块

将 LLM 生成的 YAML 策略描述解析为可执行的数据结构。
"""

import datetime
import logging
import re
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from long_earn.backtest.engine.broker import (
    TradingCostConfig as BrokerTradingCostConfig,
)

logger = logging.getLogger(__name__)


class SignalFilter(BaseModel):
    """信号过滤条件"""

    type: str = Field(default="filter")
    condition: str = Field(..., description="过滤条件表达式，如 'net_profit_yoy > 0.3'")


class SignalRank(BaseModel):
    """信号排序条件"""

    type: str = Field(default="rank")
    by: str = Field(..., description="排序字段")
    ascending: bool = Field(default=False, description="是否升序")
    top: int = Field(default=10, description="选取前 N 个")


class SignalExpression(BaseModel):
    """信号表达式"""

    type: str = Field(default="expression")
    formula: str = Field(..., description="计算公式，如 'close / shift(close, 20) - 1'")
    alias: str = Field(..., description="结果字段名")


SignalStep = SignalFilter | SignalRank | SignalExpression


class TradingCostConfig(BaseModel):
    """交易成本配置 (默认 A 股参数)

    Pydantic 版本，用于 YAML DSL 解析。运行时通过 to_broker_config() 转换为
    broker 层的 dataclass 版本，确保类型一致。
    """

    commission_rate: float = Field(
        default=0.0003, description="单边佣金率，如 0.0003 表示万三"
    )
    stamp_duty: float = Field(
        default=0.0005, description="卖出印花税率，如 0.0005 表示万五"
    )
    slippage_bps: float = Field(
        default=2.0, description="滑点基点，2.0 表示 2bps = 0.0002"
    )

    def to_broker_config(self) -> BrokerTradingCostConfig:
        """转换为 broker 层的 dataclass 版本"""
        return BrokerTradingCostConfig(
            commission_rate=self.commission_rate,
            stamp_duty=self.stamp_duty,
            slippage_bps=self.slippage_bps,
        )


class WeightConfig(BaseModel):
    """权重配置"""

    method: str = Field(
        default="equal",
        description="权重方法: equal, market_cap, custom_formula, signal",
    )
    formula: str | None = Field(
        default=None, description="自定义权重公式（method=custom_formula 时必填）"
    )
    signal_field: str | None = Field(
        default=None, description="使用哪个信号字段作为权重（method=signal 时必填）"
    )


class RiskControlConfig(BaseModel):
    """风控配置"""

    max_position_per_stock: float = Field(
        default=1.0, description="单只股票最大仓位比例"
    )
    max_turnover: float | None = Field(
        default=None, description="最大换手率限制（单次调仓）"
    )
    stop_loss: float | None = Field(
        default=None, description="止损比例，如 0.1 表示 -10% 止损"
    )
    max_drawdown_limit: float | None = Field(
        default=None, description="最大回撤限制，超过则清仓"
    )


class UniverseConfig(BaseModel):
    """股票池配置"""

    type: str = Field(
        default="csi300",
        description="股票池类型: all_a, csi300, csi500, main_board, gem, star_board, main_board+star_board",
    )
    rebalance_freq: str = Field(
        default="20D", description="股票池再平衡频率，如 20D（20个交易日）"
    )


class StrategyDSL(BaseModel):
    """策略 DSL 模型"""

    name: str = Field(default="Strategy", description="策略名称")
    description: str = Field(default="", description="策略描述")
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    start_date: str | None = Field(default=None, description="回测开始日期")
    end_date: str | None = Field(default=None, description="回测结束日期")
    factors: dict[str, str] = Field(
        default_factory=dict, description="因子定义，{alias: expression}"
    )
    signals: list[dict[str, Any]] = Field(
        default_factory=list, description="信号生成步骤列表"
    )
    weights: WeightConfig = Field(default_factory=WeightConfig)
    risk_control: RiskControlConfig = Field(default_factory=RiskControlConfig)
    trading_cost: TradingCostConfig = Field(default_factory=TradingCostConfig)

    @field_validator("signals", mode="before")
    @classmethod
    def validate_signals(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """校验信号步骤"""
        for i, step in enumerate(v):
            if "type" not in step:
                raise ValueError(f"第 {i} 个信号步骤缺少 type 字段")
            if step["type"] == "filter" and "condition" not in step:
                raise ValueError(f"第 {i} 个 filter 步骤缺少 condition 字段")
            if step["type"] == "rank" and "by" not in step:
                raise ValueError(f"第 {i} 个 rank 步骤缺少 by 字段")
        return v


def _convert_dates(obj: Any) -> Any:
    """递归将 datetime.date 转换为字符串"""

    if isinstance(obj, datetime.date) and not isinstance(obj, datetime.datetime):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, dict):
        return {k: _convert_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_dates(v) for v in obj]
    return obj


def parse_strategy_yaml(yaml_str: str) -> StrategyDSL:
    """解析策略 YAML 字符串

    Args:
        yaml_str: YAML 格式的策略描述

    Returns:
        解析后的 StrategyDSL 对象

    Raises:
        ValueError: YAML 格式错误或必填字段缺失
    """
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML 解析失败: {e}") from e

    if data is None:
        raise ValueError("YAML 内容为空")

    # 支持顶层直接是 strategy 对象，或者包含 strategy 字段
    if "strategy" in data:
        data = data["strategy"]

    # 转换日期对象为字符串
    data = _convert_dates(data)

    try:
        strategy = StrategyDSL.model_validate(data)
    except Exception as e:
        raise ValueError(f"策略参数校验失败: {e}") from e

    logger.info(f"策略解析成功: {strategy.name}")
    return strategy


def validate_fields(strategy: StrategyDSL, available_fields: list[str]) -> list[str]:
    """校验策略中引用的字段是否在可用字段列表中"""
    used_fields = set()

    # 1. 首先收集所有定义的别名 (Aliases)
    defined_aliases = set()
    for step in strategy.signals:
        if step.get("type") == "expression":
            alias = step.get("alias")
            if alias:
                defined_aliases.add(alias)

    # 从 factors 中提取字段
    for expr in strategy.factors.values():
        used_fields.update(_extract_field_names(expr))

    # 从 signals 中提取字段
    for step in strategy.signals:
        if step["type"] == "filter":
            used_fields.update(_extract_field_names(step.get("condition", "")))
        elif step["type"] == "rank":
            used_fields.add(step.get("by", ""))
        elif step["type"] == "expression":
            used_fields.update(_extract_field_names(step.get("formula", "")))

    # 从 weights 中提取字段
    if strategy.weights.formula:
        used_fields.update(_extract_field_names(strategy.weights.formula))
    if strategy.weights.signal_field:
        used_fields.add(strategy.weights.signal_field)

    # 过滤出缺失的字段（factors 的别名和 signals 定义的 alias 都是合法字段）
    valid_fields = (
        set(available_fields) | set(strategy.factors.keys()) | defined_aliases
    )
    missing = (
        used_fields
        - valid_fields
        - {
            "",
            "shift",
            "rank",
            "sum",
            "mean",
            "std",
            "abs",
            "max",
            "min",
        }
    )

    return sorted(missing)


def _extract_field_names(expression: str) -> set[str]:
    """从表达式中提取字段名"""
    # 移除字符串常量
    expr = re.sub(r"'[^']*'|\"[^\"]*\"", "", expression)
    # 移除数字
    expr = re.sub(r"\b\d+\.?\d*\b", "", expr)
    # 移除常见函数名、运算符及 Python 逻辑关键字
    funcs = (
        r"\b(shift|rank|sum|mean|std|abs|max|min|where|clip|log|exp|sqrt|and|or|not)\b"
    )
    expr = re.sub(funcs, "", expr)
    # 移除比较运算符和逻辑运算符
    expr = re.sub(r"[><=!&|+\-*/()\[\],\.\:\%\^]", " ", expr)
    # 提取剩余标识符
    tokens = re.findall(r"\b[a-zA-Z_]\w*\b", expr)
    return set(tokens)

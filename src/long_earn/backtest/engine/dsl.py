"""YAML DSL 解析与编译模块

将 LLM 生成的 YAML 策略描述解析为可执行的数据结构。

ADR-009 收尾：旧式 ``factors`` + ``filter``/``rank``/``expression`` 信号路径已退役，
所有策略必须使用算子目录（``operator_factors`` + ``type: operator`` 信号步骤）。
"""

import datetime
from typing import Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from long_earn.backtest.engine.broker import (
    TradingCostConfig as BrokerTradingCostConfig,
)


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
    """权重配置（ADR-009 收尾：仅支持 equal，custom_formula/signal 已退役）"""

    method: str = Field(
        default="equal",
        description="权重方法: equal（ADR-009 收尾后仅支持 equal）",
    )

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        if v != "equal":
            raise ValueError(
                f"weights.method='{v}' 不被支持，"
                f"ADR-009 收尾后仅支持 'equal'（旧式 custom_formula/signal 已退役）"
            )
        return v


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
        default="main_board+gem",
        description=(
            "股票池类型: all_a, csi300, csi500, csi1000, sse50, "
            "main_board, gem, star_board, main_board+gem, main_board+star_board。"
            "默认 main_board+gem（沪深除科创板所有标的）"
        ),
    )
    rebalance_freq: str = Field(
        default="20D", description="股票池再平衡频率，如 20D（20个交易日）"
    )


class StrategyDSL(BaseModel):
    """策略 DSL 模型（ADR-009 收尾：仅支持算子目录路径）

    旧式 ``factors`` 字段、``filter``/``rank``/``expression`` 信号类型、
    ``custom_formula``/``signal`` 权重方法已退役，解析期强制拒绝。
    所有策略必须使用 ``operator_factors`` + ``type: operator`` 信号步骤，
    走算子目录执行路径（因果性由 ``prove_causality`` 保证）。
    """

    name: str = Field(default="Strategy", description="策略名称")
    description: str = Field(default="", description="策略描述")
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    start_date: str | None = Field(default=None, description="回测开始日期")
    end_date: str | None = Field(default=None, description="回测结束日期")
    operator_factors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="算子目录因子步骤，[{op, alias, params}]",
    )
    signals: list[dict[str, Any]] = Field(
        default_factory=list, description="信号生成步骤列表（仅支持 type=operator）"
    )
    weights: WeightConfig = Field(default_factory=WeightConfig)
    risk_control: RiskControlConfig = Field(default_factory=RiskControlConfig)
    trading_cost: TradingCostConfig = Field(default_factory=TradingCostConfig)

    @field_validator("signals", mode="before")
    @classmethod
    def validate_signals(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """校验信号步骤 — 仅允许 type=operator（ADR-009 收尾）"""
        for i, step in enumerate(v):
            if "type" not in step:
                raise ValueError(f"第 {i} 个信号步骤缺少 type 字段")
            if step["type"] != "operator":
                raise ValueError(
                    f"第 {i} 个信号步骤 type='{step['type']}' 不被支持，"
                    f"ADR-009 收尾后仅支持 type='operator'"
                    f"（旧式 filter/rank/expression 已退役）"
                )
            if "op" not in step:
                raise ValueError(f"第 {i} 个 operator 信号步骤缺少 op 字段")
        return v

    def has_operator_steps(self) -> bool:
        """是否含算子目录步骤（factor 或 signal）。

        ADR-009 收尾后所有策略均走算子路径，此函数保留为 True 兜底
        （兼容调用方旧分支判断逻辑）。
        """
        return bool(self.operator_factors) or any(
            s.get("type") == "operator" for s in self.signals
        )


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

    # ADR-009 收尾：旧式 factors 字段强制拒绝
    if "factors" in data:
        raise ValueError(
            "旧式 factors 字段已退役（ADR-009 收尾），"
            "请改用 operator_factors 声明算子目录步骤"
        )

    try:
        strategy = StrategyDSL.model_validate(data)
    except Exception as e:
        raise ValueError(f"策略参数校验失败: {e}") from e

    # 算子目录步骤解析期校验：op 存在 + params 符合 params_cls。
    # 失败在此抛 ValueError（被 backtest_service 归为 client_error，跳过 refine 循环），
    # 而非拖到回测期才暴露——这是新 DSL 消灭 refine 循环的关键。
    _validate_operator_steps(strategy)

    logger.info(f"策略解析成功: {strategy.name}")
    return strategy


def _validate_operator_steps(strategy: StrategyDSL) -> None:
    """解析期校验算子因子 / 信号步骤：op 在目录、params 合法。"""
    from long_earn.backtest.engine.operator_executor import (  # noqa: PLC0415
        resolve_factor_step,
        resolve_signal_step,
    )

    for i, step in enumerate(strategy.operator_factors):
        try:
            resolve_factor_step(step)
        except ValueError as exc:
            raise ValueError(f"第 {i} 个 operator_factors 步骤非法: {exc}") from exc
    for i, step in enumerate(strategy.signals):
        if step.get("type") != "operator":
            continue
        try:
            resolve_signal_step(step)
        except ValueError as exc:
            raise ValueError(f"第 {i} 个 operator 信号步骤非法: {exc}") from exc

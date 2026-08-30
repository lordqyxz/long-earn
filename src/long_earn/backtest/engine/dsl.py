"""YAML DSL 解析与编译模块

将 LLM 生成的 YAML 策略描述解析为可执行的数据结构。

ADR-009 收尾：旧式 ``factors`` + ``filter``/``rank``/``expression`` 信号路径已退役，
所有策略必须使用算子目录（``operator_factors`` + ``type: operator`` 信号步骤）。
"""

import datetime
from typing import Any, Literal

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from long_earn.backtest.engine.broker import (
    TradingCostConfig as BrokerTradingCostConfig,
)
from long_earn.backtest.operators.causality import TEMPORAL_PARAMETER_NAMES


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


class RegimeConfig(BaseModel):
    """牛熊状态门控配置（哑铃策略）

    以基准指数判定市场状态，熊市切换防守腿（低波红利 ETF / 国债 ETF，
    空列表表示熊市空仓持币）。三种门控模式：

    - ``absolute``：指数收盘价 vs 长期均线（经典 Faber 择时）。防市场级
      崩盘（2022-2023 型），防不了指数横盘期的风格崩盘
    - ``relative``：股票池动量 vs 指数动量（池相对强度）。池落后指数超
      margin 即判熊——防风格崩盘（2025Q1 型：指数横盘但池内策略股崩盘）
    - ``combined``：两者任一触发即熊市（OR 逻辑，最保守）

    门控在 ``DSLStrategy.on_bar`` 内实现（增量收盘价追踪，O(截面)/bar），
    要求预取面板包含 benchmark 与防守腿标的（并行路径由 ``parallel.py``
    自动并入，单进程路径由引擎拉数时并入）。
    """

    benchmark: str = Field(description="牛熊判定基准指数代码，如 000300.SH")
    window: int = Field(
        default=250,
        description="绝对模式均线窗口（交易日）。窗口不足时视为牛市（不门控）",
    )
    defensive_assets: list[str] = Field(
        default_factory=list,
        description="熊市防守腿标的（如低波红利 ETF 512890.SH）；空列表=熊市空仓",
    )
    mode: Literal["absolute", "relative", "combined"] = Field(
        default="absolute",
        description="门控模式：absolute/relative/combined（见类 docstring）",
    )
    rel_window: int = Field(
        default=20,
        description="相对强度动量窗口（交易日），仅 relative/combined 模式生效",
    )
    rel_margin: float = Field(
        default=0.0,
        description="池动量落后指数动量超过此幅度才触发熊市（0.05=落后5个百分点）",
    )

    @property
    def uses_relative(self) -> bool:
        """是否启用池相对强度分支。"""
        return self.mode in ("relative", "combined")

    def non_pool_symbols(self) -> list[str]:
        """股票池之外需进入预取面板的标的（benchmark + 防守腿）。"""
        return [self.benchmark, *self.defensive_assets]


class StrategyDSL(BaseModel):
    """策略 DSL 模型（ADR-009 收尾：仅支持算子目录路径）

    旧式 ``factors`` 字段、``filter``/``rank``/``expression`` 信号类型、
    ``custom_formula``/``signal`` 权重方法已退役，解析期强制拒绝。
    所有策略必须使用 ``operator_factors`` + ``type: operator`` 信号步骤，
    走算子目录执行路径（因果性由 ``prove_causality`` 保证）。
    """

    name: str = Field(default="Strategy", description="策略名称")
    description: str = Field(default="", description="策略描述")
    kind: Literal["research", "production"] = Field(
        default="research",
        description="策略用途标记：research=研发/测试（默认，引擎自动打 test 标签"
        "可被审计清理）；production=生产（引擎自动打 prod 标签，审计清理豁免）。"
        "测试/冒烟策略即使不显式传 tags 也会被引擎按 kind 自动标记。",
    )
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    regime: RegimeConfig | None = Field(
        default=None,
        description="牛熊状态门控（可选）。配置后熊市切换防守腿，牛市正常选股",
    )
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


def lookback_profile(dsl: StrategyDSL) -> tuple[int, int]:
    """扫描 DSL 算子参数，返回 (最大有限回溯窗口 bars, 最大 ewm span)。

    供两处消费：compute_warmup_days（转日历日）与 DSLStrategy 历史
    截断窗口（有限窗口 + 4×span ewm 收敛余量）。回溯参数键与注册因果
    证明共用单一事实源
    :data:`operators.causality.TEMPORAL_PARAMETER_NAMES`（含 compose
    算子的 ``low_vol_lookback``/``momentum_lookback``/``momentum_window``/
    ``quality_window``/``min_obs`` 等）——此前本函数私有清单落后于该表，
    compose 算子 warmup 被低估为 0（ADR-013 T6 同族缺陷复发）。
    扫描覆盖 ``operator_factors`` + ``signals``(type=operator) 全部算子
    步骤 + 牛熊门控窗口。
    """
    span_keys = ("span", "fast", "slow", "signal")
    max_window = 0
    max_span = 0
    operator_steps: list[dict[str, Any]] = list(dsl.operator_factors)
    for step in dsl.signals:
        if step.get("type") == "operator":
            operator_steps.append(step)
    for step in operator_steps:
        params = step.get("params") or {}
        for key in TEMPORAL_PARAMETER_NAMES:
            val = params.get(key) or 0
            # 防御非数值参数值（如列表形态的 periods）：仅数值参与窗口推断
            if isinstance(val, bool) or not isinstance(val, int | float):
                continue
            max_window = max(max_window, int(val))
        for key in span_keys:
            val = params.get(key, 0) or 0
            max_span = max(max_span, int(val))
    max_window = max(max_window, max_span)
    # 牛熊门控窗口同样需要回溯（数据不足时门控退化为牛市，熊市不触发）
    if dsl.regime is not None:
        max_window = max(max_window, dsl.regime.window)
        if dsl.regime.uses_relative:
            max_window = max(max_window, dsl.regime.rel_window)
    return max_window, max_span


def compute_warmup_days(dsl: StrategyDSL) -> int:
    """从 DSL 算子参数推断所需预热期（日历日）。

    扫描 ``operator_factors`` 与 ``signals``（type=operator）全部算子步骤，
    取最大回溯窗口（时序参数键全集见
    :data:`operators.causality.TEMPORAL_PARAMETER_NAMES`），转换为日历日
    （交易日 × 1.5 + 30 天 buffer）。0 表示无时序算子，不需要 warmup。

    关键 bug 修复背景（ADR-013 T6，2026-08）：原实现只扫 ``operator_factors``
    的 ``period``/``window``/``span`` 三键，遗漏 ``shift.periods``（复数）、
    ``macd.fast``/``slow``/``signal``，且不扫 ``signals`` 里的算子步骤。结果
    预取区间短于真实回溯需求，因子前若干 bar 全 NaN，``rank_top`` 选不出股票，
    整轮回测 ``trade_count=0``。修复后与因果证明共用时序参数键单一事实源 +
    signal 步骤。

    来源：自 ``services/backtest_service.py`` 迁入（``_compute_warmup_days``
    改名公开函数），以恢复依赖方向。
    """
    max_period, _ = lookback_profile(dsl)
    if max_period <= 0:
        return 0
    # 交易日 -> 日历日：约 7/5 倍；加 30 天 buffer 防节假日
    return int(max_period * 1.5 + 30)

"""DSL 策略适配器

把 YAML DSL 描述的 ``StrategyDSL`` 适配为可运行的 ``BaseStrategy``，
走算子目录执行路径（ADR-009 收尾：仅支持算子目录，旧式 factors /
filter / rank / expression 路径已退役）。

来源：自 ``services/backtest_service.py`` 迁入，以恢复依赖方向
（backtest.engine 属领域层，不得反向依赖 services）。
"""

import re
from collections import deque
from typing import Any

import polars as pl

from long_earn.backtest.domain.entities import SignalEvent
from long_earn.backtest.engine.dsl import RegimeConfig, StrategyDSL
from long_earn.backtest.engine.strategy import BaseStrategy

# rebalance_freq 合法格式："<N>D"（N 个交易日，如 "20D"）
_REBALANCE_FREQ_RE = re.compile(r"^(\d+)D$")


def parse_rebalance_days(freq: str) -> int:
    """解析 ``rebalance_freq`` 为交易日数。

    非法值（空串/格式错/非正数）退化为 1（每日调仓），并保持向后兼容：
    历史策略未声明或声明非法时行为与修复前一致。
    """
    m = _REBALANCE_FREQ_RE.match(freq.strip())
    if not m:
        return 1
    n = int(m.group(1))
    return n if n >= 1 else 1


class DSLStrategy(BaseStrategy):
    """从 YAML DSL 自动生成的状态化策略（ADR-009 收尾：仅算子目录路径）

    旧式 factors + filter/rank/expression 信号路径已退役，所有策略必须含
    operator_factors 或 type=operator 信号步骤（DSL 解析期强制校验）。
    因果性由算子目录（每个算子过 prove_causality）+ VisibilityGuard
    （history 仅含 timestamp <= 当前时刻）共同保证。
    """

    def __init__(
        self, strategy_id: str, dsl_strategy: StrategyDSL, config: dict | None = None
    ):
        super().__init__(strategy_id, config)
        self.dsl = dsl_strategy
        self._rebalance_days = parse_rebalance_days(
            dsl_strategy.universe.rebalance_freq
        )
        # 交易日计数（on_bar 调用次数）：0 表示尚未开仓
        self._bar_count = 0
        # 牛熊状态：None=未配置门控；"bull"/"bear"=当前状态
        self._regime_state: str | None = None
        # benchmark 行缺失只告警一次（避免每 bar 累积诊断）
        self._regime_benchmark_warned = False
        # 牛熊门控增量数据通道（O(截面)/bar，替代每 bar 全面板扫描）：
        # benchmark 收盘价 deque + 股票池每 symbol 定长 deque（仅相对强度模式）
        cfg = dsl_strategy.regime
        bench_len = (
            max(cfg.window, cfg.rel_window + 1) if cfg is not None else None
        )
        self._bench_closes: deque[float] = deque(maxlen=bench_len)
        self._pool_deque_maxlen = (
            cfg.rel_window + 1
            if cfg is not None and cfg.uses_relative
            else None
        )
        self._pool_closes: dict[str, deque[float]] = {}
        # 静默吞异常的诊断窗口：上层可读取这两个列表判断策略是否真在工作，
        # 还是只是退化成"什么都不做"而被错误标记 success=True。
        # factor_failures 保留为空列表（旧字段，向后兼容诊断逻辑），算子路径不写入。
        self.factor_failures: list[dict[str, str]] = []
        self.step_failures: list[dict[str, str]] = []

    @property
    def regime_spec(self) -> Any:
        """暴露 regime 配置给引擎（拉数时并入 benchmark/防守腿标的）。

        鸭子类型探针：引擎用 getattr(strategy, "regime_spec", None) 探测，
        不引入对 DSLStrategy 的类型依赖。
        """
        return self.dsl.regime

    def init(self) -> None:
        """每 run 重置调仓相位与门控追踪（walk-forward 复用实例时防跨 fold 泄漏）。"""
        self._bar_count = 0
        self._regime_state = None
        self._regime_benchmark_warned = False
        self._bench_closes.clear()
        self._pool_closes.clear()

    def _should_rebalance(self) -> bool:
        """调仓频率门控：首个交易日建仓，之后每 N 个交易日调仓一次。"""
        if self._rebalance_days <= 1:
            return True
        return self._bar_count % self._rebalance_days == 0

    def _build_operator_executor(self):
        """惰性构造算子目录执行器。

        把算子目录接入策略执行路径：算子因子/信号步骤经此执行器跑在算子目录上。
        解析期已校验过 op/params，这里直接 resolve。
        """
        from long_earn.backtest.engine.operator_executor import (  # noqa: PLC0415
            OperatorStrategyExecutor,
            resolve_factor_step,
            resolve_signal_step,
        )

        factor_specs = [resolve_factor_step(s) for s in self.dsl.operator_factors]
        signal_specs = [
            resolve_signal_step(s)
            for s in self.dsl.signals
            if s.get("type") == "operator"
        ]
        return OperatorStrategyExecutor(factor_specs, signal_specs)

    def on_bar(self, bars: pl.DataFrame, context) -> Any:
        """算子目录执行路径：在 polars 历史面板上跑算子链 → 选中标的 → 等权信号。

        ADR-009 收尾：旧式 factors + expression 路径已退役，所有策略必须含
        operator_factors 或 operator 信号步骤（DSL 解析期强制校验）。
        ``bars`` 参数为 BaseStrategy.on_bar 契约要求：当日全截面行情，
        兼作牛熊门控的增量数据通道；算子链改用 history 面板（仅调仓日获取）。

        调仓频率门控：非调仓日返回 None（持仓保持），首个交易日建仓，
        之后每 ``rebalance_freq`` 个交易日调仓一次。风控（止损/清仓）在
        引擎层独立运行，不受此门控影响。

        牛熊门控（配置 ``regime`` 时）：absolute=指数 vs 均线，
        relative=池动量 vs 指数动量，combined=任一触发；熊市切换防守腿
        等权信号（空列表=空仓），牛市走算子链；状态切换日强制调仓
        （不等调仓周期，防熊市延迟入场）。
        """
        self._track_closes(bars)

        regime_state = self._update_regime_state()
        regime_flipped = self._consume_regime_flip(regime_state)

        do_rebalance = self._should_rebalance() or regime_flipped
        self._bar_count += 1
        if not do_rebalance:
            return None

        if regime_state == "bear":
            return self._defensive_signal(context, bars)

        if not hasattr(self, "_op_executor"):
            self._op_executor = self._build_operator_executor()

        history_pl = self._fetch_history(context)
        if history_pl is None:
            return None

        try:
            pool_history = self._strip_non_pool(history_pl)
            selected, rationale = self._op_executor.execute_with_rationale(
                pool_history, context.current_timestamp
            )
        except Exception as exc:
            self.step_failures.append(
                {
                    "type": "operator_execute",
                    "step": "operator_executor",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return None

        final_weights = self._equal_weights(selected)
        if not final_weights:
            return None

        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"op_{context.current_timestamp.isoformat()}",
            event_id=f"op_{context.current_timestamp.isoformat()}",
            signals=final_weights,
            strategy_id=self.strategy_id,
            metadata={"rationale": self._rationale_with_weights(rationale)},
        )

    def _fetch_history(self, context) -> pl.DataFrame | None:
        """取 VisibilityGuard 历史面板，失败记诊断并返回 None。"""
        try:
            return context.get_history_df()
        except Exception as exc:
            self.step_failures.append(
                {
                    "type": "history_fetch",
                    "step": "on_bar history",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return None

    def _track_closes(self, bars: pl.DataFrame) -> None:
        """增量记录 benchmark 与股票池收盘价（牛熊门控数据通道）。

        每 bar O(截面规模)：benchmark 收盘价进定长 deque（覆盖最大窗口），
        池内 symbol 各一条定长 deque（rel_window+1，历史不足的 symbol 自动
        跳过）。替代每 bar 全面板 filter/group_by（O(面板行数)，大股票池
        下不可承受）。benchmark/防守腿不参与池动量，全部排除。
        """
        cfg = self.dsl.regime
        if cfg is None or bars.height == 0:
            return
        track_pool = cfg.uses_relative
        defensive = set(cfg.defensive_assets)
        pool_maxlen = self._pool_deque_maxlen
        for sym, close in zip(bars["symbol"], bars["close"], strict=True):
            if close is None:
                continue
            if sym == cfg.benchmark:
                self._bench_closes.append(float(close))
            elif track_pool and sym not in defensive:
                dq = self._pool_closes.get(sym)
                if dq is None:
                    dq = deque(maxlen=pool_maxlen)
                    self._pool_closes[sym] = dq
                dq.append(float(close))

    def _update_regime_state(self) -> str | None:
        """按 mode 计算当日牛熊状态（数据源：增量收盘价追踪）。

        Returns:
            None=未配置门控；"bull"/"bear"=当日状态。
            benchmark 数据不足或池样本不足时视为牛市（不门控）。
        """
        cfg = self.dsl.regime
        if cfg is None:
            return None

        if not self._bench_closes and not self._regime_benchmark_warned:
            self._regime_benchmark_warned = True
            self.step_failures.append(
                {
                    "type": "regime_benchmark_missing",
                    "step": "regime gate",
                    "error": (
                        f"行情截面无 benchmark {cfg.benchmark} 行，"
                        f"门控退化为始终牛市；拉数时需并入该标的"
                    ),
                }
            )

        abs_state = self._absolute_state(cfg)
        if cfg.mode == "absolute":
            return abs_state
        rel_state = self._relative_state(cfg)
        if cfg.mode == "relative":
            return rel_state
        return "bear" if "bear" in (abs_state, rel_state) else "bull"

    def _absolute_state(self, cfg: RegimeConfig) -> str:
        """绝对模式：benchmark 收盘价 vs ``window`` 日均线。"""
        closes = list(self._bench_closes)
        if len(closes) < cfg.window:
            return "bull"
        ma = sum(closes[-cfg.window:]) / cfg.window
        return "bull" if closes[-1] > ma else "bear"

    def _relative_state(self, cfg: RegimeConfig) -> str:
        """相对强度模式：股票池动量 vs benchmark 动量。

        池动量 = 池内样本股 ``rel_window`` 期收益的截面均值；
        池落后基准超过 ``rel_margin`` 判熊（风格崩盘信号：
        指数可能横盘，但策略所在风格的股票池在崩）。
        """
        need = cfg.rel_window + 1
        bench = list(self._bench_closes)
        if len(bench) < need or bench[-need] == 0:
            return "bull"
        bench_ret = bench[-1] / bench[-need] - 1.0

        rets = [
            dq[-1] / dq[0] - 1.0
            for dq in self._pool_closes.values()
            if len(dq) == dq.maxlen and dq[0] > 0
        ]
        if not rets:
            return "bull"
        pool_ret = sum(rets) / len(rets)
        return "bear" if pool_ret < bench_ret - cfg.rel_margin else "bull"

    def _consume_regime_flip(self, state: str | None) -> bool:
        """消费状态翻转：返回是否发生 bull↔bear 切换并更新内部状态。"""
        if state is None:
            return False
        prev = self._regime_state
        self._regime_state = state
        return prev is not None and prev != state

    def _strip_non_pool(self, history_pl: pl.DataFrame) -> pl.DataFrame:
        """剥离股票池之外的行（benchmark/防守腿），防其混入算子选股候选。"""
        cfg = self.dsl.regime
        if cfg is None:
            return history_pl
        non_pool = pl.Series("symbol", cfg.non_pool_symbols(), dtype=pl.String)
        return history_pl.filter(~pl.col("symbol").is_in(non_pool))

    def _defensive_signal(self, context, bars: pl.DataFrame):
        """熊市防守腿信号：等权买入可交易的防守标的（当日无行情的剔除）。

        防守腿全缺失或列表为空时返回 None（空仓持币，本身即防守）。
        """
        cfg = self.dsl.regime
        assert cfg is not None  # 调用方保证
        tradable = [a for a in cfg.defensive_assets if a in set(bars["symbol"])]
        if not tradable:
            return None
        weight = 1.0 / len(tradable)
        ts = context.current_timestamp
        return SignalEvent(
            timestamp=ts,
            trace_id=f"rg_{ts.isoformat()}",
            event_id=f"rg_{ts.isoformat()}",
            signals=dict.fromkeys(tradable, weight),
            strategy_id=self.strategy_id,
            metadata={
                "rationale": {
                    "formula": (
                        f"benchmark({cfg.benchmark}) 下穿 {cfg.window} 日均线 → "
                        f"防守腿等权: {'+'.join(tradable)}"
                    ),
                    "weights": {"method": "equal"},
                }
            },
        )

    def _rationale_with_weights(self, rationale: dict[str, Any]) -> dict[str, Any]:
        """给执行器的决策依据补上权重口径与人类可读公式（供审计归因展示）。"""
        weights = getattr(self.dsl, "weights", None)
        method = getattr(weights, "method", "") if weights is not None else ""
        criteria = rationale.get("criteria", [])
        formula = "；".join(c.get("desc", "") for c in criteria)
        if method == "equal":
            formula = f"{formula}；等权"
        rationale["formula"] = formula
        rationale["weights"] = {"method": method} if method else {}
        return rationale

    def _equal_weights(self, selected: list) -> dict[str, float]:
        if not selected:
            self.step_failures.append(
                {
                    "type": "weights",
                    "step": "method=equal",
                    "error": "selected 为空：信号步骤未选出任何标的",
                }
            )
            return {}
        weight = 1.0 / len(selected)
        return dict.fromkeys(selected, weight)

"""策略研究循环服务 — 多轮 Reflexion 研发的核心业务逻辑。

从初始交易策略或交易思路出发，反复运行策略研发子图，评估近三个月收益率，
直到收益率无法进一步提升。

本服务与 CLI / Web 等入口解耦：仅负责业务编排与结果落盘，
参数解析、启动横幅、交互确认等表现层由入口（cli.py / scripts）负责。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from long_earn.strategy_rd.htr_subgraph import (
    create_htr_subgraph as create_strategy_rd_subgraph,
)

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext

from long_earn.core.storage import best_strategy_path, strategy_results_path

_DEFAULT_IDEA = "研究一个基于净利润增长和ROE的选股策略，要求近三个月收益率最大化"

# 家族切换：连续无改善轮数达阈值后改写 idea 换策略家族
_FAMILY_PIVOT_THRESHOLD = 2

# 策略家族 idea 候选池（按动量→均值回归→价值→成交量→多因子顺序）
_IDEA_FAMILY_POOL = [
    "研究一个基于20日价格动量的选股策略，选近20日收益率最高的股票，要求近六个月收益率最大化",
    "研究一个均值回归选股策略，选择近期跌幅过大、偏离20日均线较远但基本面稳健的股票，用 RSI 超卖信号过滤，要求近六个月收益率最大化",
    "研究一个价值成长选股策略，选择 ROE>0.12 且净利润同比增长>20% 且毛利率稳定的股票，要求近六个月收益率最大化",
    "研究一个成交量异动选股策略，选择近5日成交量放大且价格突破20日均线、波动率适中的股票，要求近六个月收益率最大化",
    "研究一个多因子复合选股策略，结合动量、低波动率、高ROE和成交量放大，用算子路径实现滚动窗口因子，要求近六个月收益率最大化",
]


@dataclass
class RoundResult:
    """单轮子图执行结果。"""

    strategy_yaml: str
    backtest_result: dict[str, Any]
    reflection: str
    elapsed: float


@dataclass
class RoundMetrics:
    """单轮评估指标汇总。"""

    round: int
    recent_return: float
    recent_sharpe: float
    recent_drawdown: float
    history_return: float
    strategy_yaml: str
    reflection: str
    elapsed: float


@dataclass
class ResearchLoopSummary:
    """研究循环最终汇总。"""

    idea: str
    best_recent_return: float
    best_round: int
    best_history_return: float
    best_strategy_yaml: str
    recent_eval_window: str
    history_eval_window: str
    rounds: list[dict[str, Any]] = field(default_factory=list)


class StrategyResearchService:
    """策略研究循环服务。

    持有运行时上下文与回测服务，提供单轮研究、双窗口评估、
    多轮循环编排与结果落盘能力。无状态：每次 ``run_loop`` 独立。
    """

    def __init__(self, ctx: RuntimeContext) -> None:
        self.ctx = ctx
        self.backtest_service = ctx.require_backtest()
        self.logger = ctx.logger

        config = ctx.config
        self.history_start = config.train_start_date
        self.history_end = config.test_end_date
        self.recent_start = config.validation_start_date
        self.recent_end = config.validation_end_date

    # ── 单轮子图执行 ──────────────────────────────────────────────

    def run_round(  # noqa: PLR0913
        self,
        idea: str,
        max_iterations: int,
        history_return: float = 0.0,
        round_history: list[dict[str, Any]] | None = None,
        *,
        checkpointer: Any = None,
        thread_id: str | None = None,
    ) -> RoundResult:
        """运行一轮策略研发子图。

        Args:
            idea: 策略思路
            max_iterations: 子图内部最大迭代次数
            history_return: 上一轮历史窗口收益率（家族失效检测信号）
            round_history: 跨轮历史序列（recent_return/history_return 列表）
            checkpointer: LangGraph checkpointer（如 ``SqliteSaver``），
                启用后子图状态会持久化，支持中断恢复。None 时不持久化。
            thread_id: 当 ``checkpointer`` 非空时的研究线程 ID。同一
                ``thread_id`` 可从中断处续跑；新一轮须用新 ID 避免状态污染。
        """
        config = self.ctx.config
        config.max_iterations = max_iterations

        subgraph = create_strategy_rd_subgraph(
            self.ctx, checkpointer=checkpointer
        )
        self.logger.info(f"[循环] 启动策略研发子图，idea='{idea}'")
        if checkpointer is not None:
            self.logger.info(
                f"[循环] checkpoint 已启用，thread_id={thread_id}"
            )
        t0 = time.time()
        invoke_input: dict[str, Any] = {"query": idea}
        if history_return != 0.0:
            invoke_input["history_return"] = history_return
        if round_history:
            invoke_input["round_history"] = round_history

        invoke_config: dict[str, Any] | None = None
        if checkpointer is not None and thread_id:
            invoke_config = {"configurable": {"thread_id": thread_id}}

        # 启用 checkpointer 时，若该 thread 已有完成态则直接取最终状态；
        # 否则正常 invoke（首跑或中断后续跑传 None 即可，但这里首跑必须传 input）
        if (
            checkpointer is not None
            and invoke_config is not None
            and self._thread_already_completed(subgraph, invoke_config)
        ):
            self.logger.info(
                f"[循环] thread_id={thread_id} 已有完成态，直接复用结果"
            )
            result = subgraph.get_state(invoke_config).values
        else:
            result = subgraph.invoke(invoke_input, config=invoke_config)
        elapsed = time.time() - t0
        self.logger.info(f"[循环] 子图完成，耗时 {elapsed:.1f}s")

        backtest_result = result.get("backtest_result", {}) or {}
        strategy_yaml = (
            result.get("strategy_yaml", "")
            or result.get("optimized_strategy_yaml", "")
            or ""
        )
        reflection = result.get("reflection", "") or ""

        return RoundResult(
            strategy_yaml=strategy_yaml,
            backtest_result=backtest_result,
            reflection=reflection,
            elapsed=elapsed,
        )

    @staticmethod
    def _thread_already_completed(subgraph: Any, invoke_config: dict) -> bool:
        """检查 thread 是否已存在完成态（避免重跑）。"""
        try:
            snapshot = subgraph.get_state(invoke_config)
        except Exception:
            return False
        # next 为空元组/None 表示该线程已运行到 END
        next_nodes = getattr(snapshot, "next", None)
        if not next_nodes:
            # 还需确认 values 非空，否则可能是首次创建空快照
            values = getattr(snapshot, "values", None) or {}
            return bool(values)
        return False

    # ── 双窗口评估 ────────────────────────────────────────────────

    def evaluate_recent(self, strategy_yaml: str) -> dict[str, Any]:
        """评估策略在近三个月（验证窗口）的表现。"""
        if not strategy_yaml:
            return {"error": "策略 YAML 为空"}
        return self.backtest_service.run(
            strategy_yaml=strategy_yaml,
            start_date=self.recent_start,
            end_date=self.recent_end,
        )

    def evaluate_history(self, strategy_yaml: str) -> dict[str, Any]:
        """评估策略在历史窗口的表现（过拟合检测）。"""
        if not strategy_yaml:
            return {"error": "策略 YAML 为空"}
        return self.backtest_service.run(
            strategy_yaml=strategy_yaml,
            start_date=self.history_start,
            end_date=self.history_end,
        )

    @staticmethod
    def extract_metric(report: dict[str, Any], key: str) -> float:
        """安全提取回测指标。"""
        if "error" in report:
            return -999.0
        try:
            return float(report.get(key, -999.0))
        except (TypeError, ValueError):
            return -999.0

    # ── 单轮编排（子图 + 双窗口评估 + 改善判定）──────────────────

    def run_single_round(  # noqa: PLR0913
        self,
        idea: str,
        max_iterations: int,
        round_num: int,
        best_recent_return: float,
        min_improvement: float,
        round_history: list[dict[str, Any]] | None = None,
        *,
        checkpointer: Any = None,
        thread_id: str | None = None,
    ) -> tuple[RoundMetrics | None, float, str, dict[str, Any], bool]:
        """运行单轮研究并返回 (metrics, best_return, best_yaml, best_round, should_stop)。

        metrics 为 None 表示该轮未生成策略（跳过）。
        round_history 为前序轮次的 recent/history 收益序列，传给子图供家族失效检测。
        """
        # 优先用上一轮的历史收益作为家族失效信号传给子图；
        # 无历史时默认 0.0（不触发家族切换打分）
        prev_history_return = (
            round_history[-1].get("history_return", 0.0)
            if round_history
            else 0.0
        )

        round_result = self.run_round(
            idea,
            max_iterations,
            history_return=prev_history_return,
            round_history=round_history,
            checkpointer=checkpointer,
            thread_id=thread_id,
        )
        strategy_yaml = round_result.strategy_yaml

        if not strategy_yaml:
            self.logger.warning(f"[第{round_num}轮] 未生成策略 YAML，跳过")
            return None, best_recent_return, "", {}, False

        self.logger.info(
            f"[第{round_num}轮] 评估近三个月表现 "
            f"({self.recent_start}~{self.recent_end})..."
        )
        recent_report = self.evaluate_recent(strategy_yaml)
        recent_return = self.extract_metric(recent_report, "total_return")
        recent_sharpe = self.extract_metric(recent_report, "sharpe_ratio")
        recent_drawdown = self.extract_metric(recent_report, "max_drawdown")

        self.logger.info(
            f"[第{round_num}轮] 评估历史表现 "
            f"({self.history_start}~{self.history_end})..."
        )
        history_report = self.evaluate_history(strategy_yaml)
        history_return = self.extract_metric(history_report, "total_return")

        self.logger.info(
            f"[第{round_num}轮] 近三个月: return={recent_return:.4f}, "
            f"sharpe={recent_sharpe:.2f}, drawdown={recent_drawdown:.4f}"
        )
        self.logger.info(f"[第{round_num}轮] 历史: return={history_return:.4f}")

        metrics = RoundMetrics(
            round=round_num,
            recent_return=recent_return,
            recent_sharpe=recent_sharpe,
            recent_drawdown=recent_drawdown,
            history_return=history_return,
            strategy_yaml=strategy_yaml[:500],
            reflection=round_result.reflection[:500],
            elapsed=round_result.elapsed,
        )
        round_info = self._metrics_to_dict(metrics)

        improvement = recent_return - best_recent_return
        new_best = best_recent_return
        best_yaml = ""
        best_round: dict[str, Any] = {}
        should_stop = False

        if recent_return > best_recent_return and improvement > min_improvement:
            new_best = recent_return
            best_yaml = strategy_yaml
            best_round = round_info
            self.logger.info(
                f"[第{round_num}轮] 新最佳! 近三个月收益率 {recent_return:.4f} "
                f"(提升 {improvement:.4f})"
            )
        else:
            self.logger.info(
                f"[第{round_num}轮] 无显著改善 (提升 {improvement:.4f}, "
                f"阈值 {min_improvement})"
            )
            if round_num > 1 and improvement <= min_improvement:
                self.logger.info("[循环] 近三个月收益率无法进一步提升，停止迭代")
                self.logger.info(
                    f"[循环] 最佳近三个月收益率: {best_recent_return:.4f}"
                )
                should_stop = True

        return metrics, new_best, best_yaml, best_round, should_stop

    # ── 多轮循环编排 ──────────────────────────────────────────────

    def run_loop(  # noqa: PLR0913
        self,
        idea: str,
        max_rounds: int = 5,
        max_iterations: int = 2,
        min_improvement: float = 0.005,
        *,
        checkpointer: Any = None,
        thread_id_prefix: str = "research",
    ) -> ResearchLoopSummary:
        """运行完整策略研究循环。

        Args:
            idea: 初始交易策略或交易思路
            max_rounds: 最大研究轮次
            max_iterations: 每轮子图内部最大迭代次数
            min_improvement: 近三个月收益率最小改善幅度
            checkpointer: LangGraph checkpointer（如 ``SqliteSaver``）。
                启用后每轮状态持久化，重跑时已完成的轮次直接复用结果，
                未完成的轮次可从中断处续跑。None 时不持久化。
            thread_id_prefix: checkpointer 启用时，每轮 thread_id 为
                ``"{prefix}-round{N}-family{F}"``。

        家族切换机制：连续 ``_FAMILY_PIVOT_THRESHOLD`` 轮无改善时，
        从 ``_IDEA_FAMILY_POOL`` 取下一个家族 idea 继续研发，
        而非直接停止。池耗尽后才真正停止。
        """
        best_recent_return = -999.0
        best_strategy_yaml = ""
        best_round_info: dict[str, Any] = {}
        all_results: list[dict[str, Any]] = []
        round_history: list[dict[str, Any]] = []
        stagnation_count = 0
        family_idx = 0
        current_idea = idea

        for round_num in range(1, max_rounds + 1):
            self.logger.info("")
            self.logger.info("#" * 60)
            self.logger.info(f"# 第 {round_num}/{max_rounds} 轮 (家族索引 {family_idx})")
            self.logger.info("#" * 60)

            thread_id = (
                f"{thread_id_prefix}-round{round_num}-family{family_idx}"
                if checkpointer is not None
                else None
            )

            metrics, new_best, best_yaml, best_round, should_stop = (
                self.run_single_round(
                    idea=current_idea,
                    max_iterations=max_iterations,
                    round_num=round_num,
                    best_recent_return=best_recent_return,
                    min_improvement=min_improvement,
                    round_history=round_history,
                    checkpointer=checkpointer,
                    thread_id=thread_id,
                )
            )

            if metrics is None:
                all_results.append(
                    {
                        "round": round_num,
                        "status": "no_strategy",
                        "strategy_yaml": "",
                        "backtest_result": {},
                        "reflection": "",
                        "elapsed": 0.0,
                    }
                )
                stagnation_count += 1
            else:
                all_results.append(self._metrics_to_dict(metrics))
                round_history.append(
                    {
                        "round": round_num,
                        "recent_return": metrics.recent_return,
                        "history_return": metrics.history_return,
                    }
                )

                if best_yaml:
                    best_recent_return = new_best
                    best_strategy_yaml = best_yaml
                    best_round_info = best_round
                    stagnation_count = 0
                else:
                    stagnation_count += 1

            # 家族切换判定：连续无改善达阈值 → 换家族 idea 继续
            if should_stop or stagnation_count >= _FAMILY_PIVOT_THRESHOLD:
                if family_idx + 1 < len(_IDEA_FAMILY_POOL):
                    family_idx += 1
                    current_idea = _IDEA_FAMILY_POOL[family_idx]
                    stagnation_count = 0
                    self.logger.info("")
                    self.logger.info("=" * 60)
                    self.logger.info(
                        f"[家族切换] 连续 {stagnation_count} 轮无改善，"
                        f"切换到策略家族 #{family_idx}: {current_idea[:60]}..."
                    )
                    self.logger.info("=" * 60)
                    continue
                else:
                    self.logger.info("[循环] 策略家族池已耗尽，停止迭代")
                    break

        summary = ResearchLoopSummary(
            idea=idea,
            best_recent_return=best_recent_return,
            best_round=best_round_info.get("round", 0),
            best_history_return=best_round_info.get("history_return", 0.0),
            best_strategy_yaml=best_strategy_yaml,
            recent_eval_window=f"{self.recent_start}~{self.recent_end}",
            history_eval_window=f"{self.history_start}~{self.history_end}",
            rounds=all_results,
        )

        self.save_results(summary)
        return summary

    # ── 结果落盘 ──────────────────────────────────────────────────

    def save_results(self, summary: ResearchLoopSummary) -> None:
        """保存最佳策略与详细结果到磁盘。"""
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("策略研究循环完成")
        self.logger.info("=" * 60)

        if summary.best_strategy_yaml:
            self.logger.info(
                f"最佳近三个月收益率: {summary.best_recent_return:.4f}"
            )
            self.logger.info(
                f"最佳策略所在轮次: 第{summary.best_round}轮"
            )
            self.logger.info(
                f"最佳策略历史收益率: {summary.best_history_return:.4f}"
            )
            best_path = best_strategy_path()
            best_path.write_text(
                summary.best_strategy_yaml, encoding="utf-8"
            )
            self.logger.info(f"最佳策略已保存到: {best_path}")
        else:
            self.logger.info("未能生成有效策略")

        payload = {
            "idea": summary.idea,
            "best_recent_return": summary.best_recent_return,
            "best_round": summary.best_round,
            "recent_eval_window": summary.recent_eval_window,
            "history_eval_window": summary.history_eval_window,
            "rounds": summary.rounds,
        }
        results_path = strategy_results_path()
        results_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        self.logger.info(f"详细结果已保存到: {results_path}")

    # ── 内部工具 ──────────────────────────────────────────────────

    @staticmethod
    def _metrics_to_dict(m: RoundMetrics) -> dict[str, Any]:
        return {
            "round": m.round,
            "recent_return": m.recent_return,
            "recent_sharpe": m.recent_sharpe,
            "recent_drawdown": m.recent_drawdown,
            "history_return": m.history_return,
            "strategy_yaml": m.strategy_yaml,
            "reflection": m.reflection,
            "elapsed": m.elapsed,
        }

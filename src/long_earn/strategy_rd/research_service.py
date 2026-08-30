"""策略研究循环服务 — 多轮 ToG 研发的核心业务逻辑（ADR-018）。

从初始交易策略或交易思路出发，每轮委托 ``ResearchAgent.invoke`` 探索并产出策略 YAML，
再用训练集近窗与全窗回测评估收益率，直至无法进一步提升或家族池耗尽。

本服务与 CLI / Web 等入口解耦：仅负责业务编排与结果落盘，
参数解析、启动横幅、交互确认等表现层由入口（cli.py / scripts）负责。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from long_earn.strategy_rd.research_agent import ResearchAgent, _strategy_fingerprint

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

_STRATEGY_YAML_TOOLS = frozenset(
    {
        "run_backtest",
        "run_oos_gates",
        "compile_strategy_yaml",
        "record_path_outcome",
    }
)

_YAML_FENCE_RE = re.compile(r"```(?:yaml)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class RoundResult:
    """单轮 ResearchAgent 执行结果。"""

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
        # 铁律 #1/#2/#3：开发阶段只允许使用训练集。
        # - history = 完整训练集（train_start ~ train_end）
        # - recent = 训练集最后 6 个月（开发期不得触碰测试集/验证集）
        self.history_start = config.train_start_date
        self.history_end = config.train_end_date
        train_end_date = date.fromisoformat(config.train_end_date)
        recent_start_date = train_end_date - timedelta(days=183)
        self.recent_start = recent_start_date.isoformat()
        self.recent_end = config.train_end_date

    # ── 单轮 ResearchAgent 执行 ───────────────────────────────────

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
        """运行一轮 ResearchAgent ToG 探索。

        Args:
            idea: 策略思路
            max_iterations: 每轮探索深度提示（传入 constraints）
            history_return: 保留参数（家族切换信号，当前仅作日志参考）
            round_history: 保留参数（前序轮次收益，当前未传入 agent）
            checkpointer: LangGraph checkpointer（如 ``SqliteSaver``）
            thread_id: checkpointer 启用时的研究线程 ID
        """
        del round_history  # 不再依赖 HTR 树状态
        round_ctx = replace(
            self.ctx,
            config=replace(self.ctx.config, max_iterations=max_iterations),
        )

        agent = ResearchAgent(round_ctx, checkpointer=checkpointer)
        self.logger.info(f"[循环] 启动 ResearchAgent，idea='{idea}'")
        if checkpointer is not None:
            self.logger.info(f"[循环] checkpoint 已启用，thread_id={thread_id}")
        if history_return != 0.0:
            self.logger.debug(
                f"[循环] 上轮历史收益信号 history_return={history_return:.4f}"
            )

        constraints_parts: list[str] = []
        if max_iterations > 0:
            constraints_parts.append(f"探索深度约 {max_iterations} 轮迭代")
        constraints = "；".join(constraints_parts)

        thread_id_str = thread_id or ""
        invoke_config: RunnableConfig | None = None
        if checkpointer is not None and thread_id_str:
            invoke_config = {"configurable": {"thread_id": thread_id_str}}

        t0 = time.time()
        if (
            checkpointer is not None
            and invoke_config is not None
            and self._thread_already_completed(agent._agent, invoke_config)
        ):
            self.logger.info(
                f"[循环] thread_id={thread_id_str} 已有完成态，直接复用结果"
            )
            snapshot = agent._agent.get_state(invoke_config)
            messages = (snapshot.values or {}).get("messages", [])
            result = self._messages_to_invoke_result(messages)
        else:
            result = agent.invoke(
                idea,
                constraints=constraints,
                thread_id=thread_id_str,
            )
        elapsed = time.time() - t0
        self.logger.info(f"[循环] ResearchAgent 完成，耗时 {elapsed:.1f}s")

        strategy_yaml, reflection, backtest_result = self._extract_round_output(
            agent, result
        )
        if not strategy_yaml:
            self.logger.warning("[循环] 未从 invoke 结果提取到策略 YAML")

        return RoundResult(
            strategy_yaml=strategy_yaml,
            backtest_result=backtest_result,
            reflection=reflection,
            elapsed=elapsed,
        )

    @staticmethod
    def _messages_to_invoke_result(messages: list[Any]) -> dict[str, Any]:
        """将 checkpoint 中的 messages 转为 invoke 结果形状。"""
        summary = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                summary = str(msg.content)
                break
        return {
            "summary": summary or "策略研发完成（checkpoint 复用）",
            "messages": messages,
            "beam_paths": [],
        }

    @staticmethod
    def _extract_strategy_yaml(messages: list[Any], summary: str) -> str:
        """从 messages 或 summary 文本提取策略 YAML。"""
        for msg in reversed(messages):
            if not isinstance(msg, AIMessage):
                continue
            for tc in reversed(msg.tool_calls or []):
                name = tc.get("name", "")
                if name in _STRATEGY_YAML_TOOLS:
                    yaml_text = (tc.get("args") or {}).get("strategy_yaml", "")
                    if isinstance(yaml_text, str) and yaml_text.strip():
                        return yaml_text.strip()
            content = str(msg.content or "")
            for match in reversed(list(_YAML_FENCE_RE.finditer(content))):
                block = match.group(1).strip()
                if block.startswith("name:") or "universe:" in block:
                    return block

        for match in reversed(list(_YAML_FENCE_RE.finditer(summary))):
            block = match.group(1).strip()
            if block.startswith("name:") or "universe:" in block:
                return block
        return ""

    def _extract_round_output(
        self, agent: ResearchAgent, result: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]]:
        """从 invoke 结果提取 YAML、反思摘要与 agent 内缓存回测证据。"""
        summary = str(result.get("summary") or "")
        messages = result.get("messages") or []
        strategy_yaml = self._extract_strategy_yaml(messages, summary)
        reflection = summary or "未生成策略摘要"

        backtest_result: dict[str, Any] = {}
        if strategy_yaml:
            fp = _strategy_fingerprint(strategy_yaml)
            ev = agent._evidence_cache.get(fp)
            if ev and ev.backtest_metrics:
                backtest_result = dict(ev.backtest_metrics)

        return strategy_yaml, reflection, backtest_result

    @staticmethod
    def _thread_already_completed(
        graph: Any, invoke_config: RunnableConfig | None
    ) -> bool:
        """检查 thread 是否已存在完成态（避免重跑）。"""
        try:
            snapshot = graph.get_state(invoke_config)
        except Exception:
            return False
        next_nodes = getattr(snapshot, "next", None)
        if not next_nodes:
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

    # ── 单轮编排（ResearchAgent + 双窗口评估 + 改善判定）──────────

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
        """
        prev_history_return = (
            round_history[-1].get("history_return", 0.0) if round_history else 0.0
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
            self.logger.warning(
                f"[第{round_num}轮] 未生成策略 YAML，跳过"
                + (
                    f"（reflection: {round_result.reflection[:120]}）"
                    if round_result.reflection
                    else ""
                )
            )
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
                self.logger.info(f"[循环] 最佳近三个月收益率: {best_recent_return:.4f}")
                should_stop = True

        return metrics, new_best, best_yaml, best_round, should_stop

    # ── 多轮循环编排 ──────────────────────────────────────────────

    def run_loop(  # noqa: PLR0913, PLR0915
        self,
        idea: str,
        max_rounds: int = 5,
        max_iterations: int = 2,
        min_improvement: float = 0.005,
        *,
        checkpointer: Any = None,
        thread_id_prefix: str = "research",
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ResearchLoopSummary:
        """运行完整策略研究循环。

        Args:
            idea: 初始交易策略或交易思路
            max_rounds: 最大研究轮次
            max_iterations: 每轮 ResearchAgent 探索深度提示
            min_improvement: 近三个月收益率最小改善幅度
            checkpointer: LangGraph checkpointer（如 ``SqliteSaver``）
            thread_id_prefix: checkpointer 启用时，每轮 thread_id 为
                ``"{prefix}-round{N}-family{F}"``。

        家族切换：连续 ``_FAMILY_PIVOT_THRESHOLD`` 轮无改善时，
        从 ``_IDEA_FAMILY_POOL`` 取下一个家族 idea 继续研发。
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
            self.logger.info(
                f"# 第 {round_num}/{max_rounds} 轮 (家族索引 {family_idx})"
            )
            self.logger.info("#" * 60)

            if progress_callback:
                progress_callback(
                    {
                        "type": "round_start",
                        "round": round_num,
                        "total_rounds": max_rounds,
                        "family_idx": family_idx,
                        "idea": current_idea[:120],
                    }
                )

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

            if progress_callback:
                progress_callback(
                    {
                        "type": "round_complete",
                        "round": round_num,
                        "total_rounds": max_rounds,
                        "improved": bool(best_yaml),
                        "metrics": (
                            self._metrics_to_dict(metrics)
                            if metrics
                            else {"round": round_num, "status": "no_strategy"}
                        ),
                        "best_recent_return": best_recent_return,
                        "stagnation_count": stagnation_count,
                    }
                )

            if should_stop or stagnation_count >= _FAMILY_PIVOT_THRESHOLD:
                pivot_streak = stagnation_count
                if family_idx + 1 < len(_IDEA_FAMILY_POOL):
                    family_idx += 1
                    current_idea = _IDEA_FAMILY_POOL[family_idx]
                    stagnation_count = 0
                    self.logger.info("")
                    self.logger.info("=" * 60)
                    self.logger.info(
                        f"[家族切换] 连续 {pivot_streak} 轮无改善，"
                        f"切换到策略家族 #{family_idx}: {current_idea[:60]}..."
                    )
                    self.logger.info("=" * 60)
                    if progress_callback:
                        progress_callback(
                            {
                                "type": "family_switch",
                                "family_idx": family_idx,
                                "idea": current_idea[:120],
                                "total_families": len(_IDEA_FAMILY_POOL),
                            }
                        )
                    continue
                self.logger.info("[循环] 策略家族池已耗尽，停止迭代")
                break

        if progress_callback:
            progress_callback(
                {
                    "type": "research_complete",
                    "best_recent_return": best_recent_return,
                    "best_round": best_round_info.get("round", 0),
                    "best_history_return": best_round_info.get("history_return", 0.0),
                    "total_rounds_completed": len(all_results),
                }
            )

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
            self.logger.info(f"最佳近三个月收益率: {summary.best_recent_return:.4f}")
            self.logger.info(f"最佳策略所在轮次: 第{summary.best_round}轮")
            self.logger.info(f"最佳策略历史收益率: {summary.best_history_return:.4f}")
            best_path = best_strategy_path()
            best_path.write_text(summary.best_strategy_yaml, encoding="utf-8")
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

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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import BacktestService

# 项目根目录（本文件位于 src/long_earn/services/，向上 4 级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RESULTS_FILE = _PROJECT_ROOT / "strategy_research_results.json"
BEST_STRATEGY_FILE = _PROJECT_ROOT / "best_strategy.yaml"

_DEFAULT_IDEA = "研究一个基于净利润增长和ROE的选股策略，要求近三个月收益率最大化"


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

    def __init__(self, ctx: "RuntimeContext") -> None:
        self.ctx = ctx
        self.backtest_service = ctx.require_backtest()
        self.logger = ctx.logger

        config = ctx.config
        self.history_start = config.train_start_date
        self.history_end = config.test_end_date
        self.recent_start = config.validation_start_date
        self.recent_end = config.validation_end_date

    # ── 单轮子图执行 ──────────────────────────────────────────────

    def run_round(self, idea: str, max_iterations: int) -> RoundResult:
        """运行一轮策略研发子图。"""
        config = self.ctx.config
        config.max_iterations = max_iterations

        subgraph = create_strategy_rd_subgraph(self.ctx)
        self.logger.info(f"[循环] 启动策略研发子图，idea='{idea}'")
        t0 = time.time()
        result = subgraph.invoke({"query": idea})
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

    def run_single_round(
        self,
        idea: str,
        max_iterations: int,
        round_num: int,
        best_recent_return: float,
        min_improvement: float,
    ) -> tuple[RoundMetrics | None, float, str, dict[str, Any], bool]:
        """运行单轮研究并返回 (metrics, best_return, best_yaml, best_round, should_stop)。

        metrics 为 None 表示该轮未生成策略（跳过）。
        """
        round_result = self.run_round(idea, max_iterations)
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

    def run_loop(
        self,
        idea: str,
        max_rounds: int = 5,
        max_iterations: int = 2,
        min_improvement: float = 0.005,
    ) -> ResearchLoopSummary:
        """运行完整策略研究循环。

        Args:
            idea: 初始交易策略或交易思路
            max_rounds: 最大研究轮次
            max_iterations: 每轮子图内部最大迭代次数
            min_improvement: 近三个月收益率最小改善幅度
        """
        best_recent_return = -999.0
        best_strategy_yaml = ""
        best_round_info: dict[str, Any] = {}
        all_results: list[dict[str, Any]] = []

        for round_num in range(1, max_rounds + 1):
            self.logger.info("")
            self.logger.info("#" * 60)
            self.logger.info(f"# 第 {round_num}/{max_rounds} 轮")
            self.logger.info("#" * 60)

            metrics, new_best, best_yaml, best_round, should_stop = (
                self.run_single_round(
                    idea=idea,
                    max_iterations=max_iterations,
                    round_num=round_num,
                    best_recent_return=best_recent_return,
                    min_improvement=min_improvement,
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
                continue

            all_results.append(self._metrics_to_dict(metrics))

            if best_yaml:
                best_recent_return = new_best
                best_strategy_yaml = best_yaml
                best_round_info = best_round

            if should_stop:
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
            BEST_STRATEGY_FILE.write_text(
                summary.best_strategy_yaml, encoding="utf-8"
            )
            self.logger.info(f"最佳策略已保存到: {BEST_STRATEGY_FILE}")
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
        RESULTS_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        self.logger.info(f"详细结果已保存到: {RESULTS_FILE}")

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

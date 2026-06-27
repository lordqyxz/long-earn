#!/usr/bin/env python3
"""自主策略研究循环。

从宽泛的 prompt 出发，反复运行策略研发子图，评估近三个月收益率，
直到近三个月收益率无法进一步提升。

用法:
    uv run python scripts/strategy_research_loop.py
    uv run python scripts/strategy_research_loop.py --max-rounds 5 --query "基于净利润增长的选股策略"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.services.backtest_service import BacktestServiceImpl  # noqa: E402
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph  # noqa: E402

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    ),
)

# 量化数据分割（从 config 读取，默认值见 AppConfig）
RECENT_START = ""  # 验证集起始（initialize 时从 config 读）
RECENT_END = ""
HISTORY_START = ""  # 训练集起始
HISTORY_END = ""

RESULTS_FILE = project_root / "strategy_research_results.json"


def run_research_round(
    ctx: Any,
    query: str,
    max_iterations: int,
) -> dict[str, Any]:
    """运行一轮策略研发子图。"""
    config = ctx.config
    config.max_iterations = max_iterations

    subgraph = create_strategy_rd_subgraph(ctx)
    logger.info(f"[循环] 启动策略研发子图，query='{query}'")
    t0 = time.time()
    result = subgraph.invoke({"query": query})
    elapsed = time.time() - t0
    logger.info(f"[循环] 子图完成，耗时 {elapsed:.1f}s")

    # 提取关键结果
    backtest_result = result.get("backtest_result", {}) or {}
    strategy_yaml = result.get("strategy_yaml", "") or result.get(
        "optimized_strategy_yaml", ""
    ) or ""
    reflection = result.get("reflection", "") or ""

    return {
        "strategy_yaml": strategy_yaml,
        "backtest_result": backtest_result,
        "reflection": reflection,
        "elapsed": elapsed,
    }


def evaluate_recent_performance(
    backtest_service: BacktestServiceImpl,
    strategy_yaml: str,
) -> dict[str, Any]:
    """评估策略在近三个月的表现。"""
    if not strategy_yaml:
        return {"error": "策略 YAML 为空"}

    report = backtest_service.run(
        strategy_yaml=strategy_yaml,
        start_date=RECENT_START,
        end_date=RECENT_END,
    )
    return report


def evaluate_history_performance(
    backtest_service: BacktestServiceImpl,
    strategy_yaml: str,
) -> dict[str, Any]:
    """评估策略在历史窗口的表现（用于过拟合检测）。"""
    if not strategy_yaml:
        return {"error": "策略 YAML 为空"}

    report = backtest_service.run(
        strategy_yaml=strategy_yaml,
        start_date=HISTORY_START,
        end_date=HISTORY_END,
    )
    return report


def extract_metric(report: dict[str, Any], key: str) -> float:
    """安全提取回测指标。"""
    if "error" in report:
        return -999.0
    return float(report.get(key, -999.0))


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="自主策略研究循环")
    parser.add_argument(
        "--query",
        default="研究一个基于净利润增长和ROE的选股策略，要求近三个月收益率最大化",
        help="策略研究查询 prompt",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=5,
        help="最大研究轮次",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=2,
        help="每轮子图内部最大迭代次数",
    )
    parser.add_argument(
        "--min-improvement",
        type=float,
        default=0.005,
        help="近三个月收益率最小改善幅度（低于此值视为无提升）",
    )
    return parser.parse_args()




@dataclass
class RoundConfig:
    """单轮研究的配置参数。"""

    ctx: Any
    backtest_service: BacktestServiceImpl
    query: str
    max_iterations: int
    round_num: int
    best_recent_return: float
    min_improvement: float


def _run_single_round(
    cfg: RoundConfig,
) -> tuple[dict[str, Any] | None, float, str, dict[str, Any], bool]:
    """运行单轮研究并返回 (round_info, best_return, best_yaml, best_round, should_stop)。

    返回 None 的 round_info 表示该轮未生成策略（跳过）。
    """
    round_result = run_research_round(
        cfg.ctx, cfg.query, cfg.max_iterations
    )
    strategy_yaml = round_result["strategy_yaml"]

    if not strategy_yaml:
        logger.warning(f"[第{cfg.round_num}轮] 未生成策略 YAML，跳过")
        return None, cfg.best_recent_return, "", {}, False

    logger.info(f"[第{cfg.round_num}轮] 评估近三个月表现 ({RECENT_START}~{RECENT_END})...")
    recent_report = evaluate_recent_performance(
        cfg.backtest_service, strategy_yaml
    )
    recent_return = extract_metric(recent_report, "total_return")
    recent_sharpe = extract_metric(recent_report, "sharpe_ratio")
    recent_drawdown = extract_metric(recent_report, "max_drawdown")

    logger.info(f"[第{cfg.round_num}轮] 评估历史表现 ({HISTORY_START}~{HISTORY_END})...")
    history_report = evaluate_history_performance(
        cfg.backtest_service, strategy_yaml
    )
    history_return = extract_metric(history_report, "total_return")

    logger.info(
        f"[第{cfg.round_num}轮] 近三个月: return={recent_return:.4f}, "
        f"sharpe={recent_sharpe:.2f}, drawdown={recent_drawdown:.4f}"
    )
    logger.info(f"[第{cfg.round_num}轮] 历史: return={history_return:.4f}")

    round_info = {
        "round": cfg.round_num,
        "recent_return": recent_return,
        "recent_sharpe": recent_sharpe,
        "recent_drawdown": recent_drawdown,
        "history_return": history_return,
        "strategy_yaml": strategy_yaml[:500],
        "reflection": round_result["reflection"][:500],
        "elapsed": round_result["elapsed"],
    }

    improvement = recent_return - cfg.best_recent_return
    new_best = cfg.best_recent_return
    best_yaml = ""
    best_round: dict[str, Any] = {}
    should_stop = False

    if recent_return > cfg.best_recent_return and improvement > cfg.min_improvement:
        new_best = recent_return
        best_yaml = strategy_yaml
        best_round = round_info
        logger.info(
            f"[第{cfg.round_num}轮] 新最佳! 近三个月收益率 {recent_return:.4f} "
            f"(提升 {improvement:.4f})"
        )
    else:
        logger.info(
            f"[第{cfg.round_num}轮] 无显著改善 (提升 {improvement:.4f}, "
            f"阈值 {cfg.min_improvement})"
        )
        if cfg.round_num > 1 and improvement <= cfg.min_improvement:
            logger.info("[循环] 近三个月收益率无法进一步提升，停止迭代")
            logger.info(
                f"[循环] 最佳近三个月收益率: {cfg.best_recent_return:.4f}"
            )
            should_stop = True

    return round_info, new_best, best_yaml, best_round, should_stop


def _save_results(
    args: argparse.Namespace,
    best_recent_return: float,
    best_strategy_yaml: str,
    best_round_info: dict[str, Any],
    all_results: list[dict[str, Any]],
) -> None:
    """保存最终结果。"""
    logger.info("")
    logger.info("=" * 60)
    logger.info("策略研究循环完成")
    logger.info("=" * 60)
    if best_strategy_yaml:
        logger.info(f"最佳近三个月收益率: {best_recent_return:.4f}")
        logger.info(f"最佳策略所在轮次: 第{best_round_info.get('round', 0)}轮")
        logger.info(
            f"最佳策略历史收益率: {best_round_info.get('history_return', 0):.4f}"
        )
        best_file = project_root / "best_strategy.yaml"
        best_file.write_text(best_strategy_yaml, encoding="utf-8")
        logger.info(f"最佳策略已保存到: {best_file}")
    else:
        logger.info("未能生成有效策略")

    summary = {
        "query": args.query,
        "best_recent_return": best_recent_return,
        "best_round": best_round_info.get("round", 0),
        "recent_eval_window": f"{RECENT_START}~{RECENT_END}",
        "history_eval_window": f"{HISTORY_START}~{HISTORY_END}",
        "rounds": all_results,
    }
    RESULTS_FILE.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info(f"详细结果已保存到: {RESULTS_FILE}")


def main() -> None:
    """主函数。"""
    global RECENT_START, RECENT_END, HISTORY_START, HISTORY_END  # noqa: PLW0603
    args = _parse_args()

    config = AppConfig.from_env()
    # 从 config 读取量化数据分割日期
    HISTORY_START = config.train_start_date
    HISTORY_END = config.test_end_date
    RECENT_START = config.validation_start_date
    RECENT_END = config.validation_end_date

    logger.info("=" * 60)
    logger.info("自主策略研究循环")
    logger.info(f"查询: {args.query}")
    logger.info(f"最大轮次: {args.max_rounds}")
    logger.info(f"训练区间: {HISTORY_START} ~ {HISTORY_END}")
    logger.info(f"验证区间: {RECENT_START} ~ {RECENT_END}")
    logger.info("=" * 60)

    config.backtest_start_date = HISTORY_START
    config.backtest_end_date = HISTORY_END
    ctx = initialize_context(config)
    backtest_service = ctx.require_backtest()

    best_recent_return = -999.0
    best_strategy_yaml = ""
    best_round_info: dict[str, Any] = {}
    all_results: list[dict[str, Any]] = []

    for round_num in range(1, args.max_rounds + 1):
        logger.info("")
        logger.info("#" * 60)
        logger.info(f"# 第 {round_num}/{args.max_rounds} 轮")
        logger.info("#" * 60)

        round_info, new_best, best_yaml, best_round, should_stop = _run_single_round(
            RoundConfig(
                ctx=ctx,
                backtest_service=backtest_service,
                query=args.query,
                max_iterations=args.max_iterations,
                round_num=round_num,
                best_recent_return=best_recent_return,
                min_improvement=args.min_improvement,
            )
        )

        if round_info is None:
            all_results.append({
                "round": round_num,
                "status": "no_strategy",
                "strategy_yaml": "",
                "backtest_result": {},
                "reflection": "",
                "elapsed": 0.0,
            })
            continue

        all_results.append(round_info)

        if best_yaml:
            best_recent_return = new_best
            best_strategy_yaml = best_yaml
            best_round_info = best_round

        if should_stop:
            break

    _save_results(
        args,
        best_recent_return,
        best_strategy_yaml,
        best_round_info,
        all_results,
    )


if __name__ == "__main__":
    main()

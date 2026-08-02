#!/usr/bin/env python3
"""ToG ResearchAgent + SqliteSaver checkpoint 探索跑。

修复算子 markdown 围栏等 bug 后，用 UTF-8 stdio + 持久化 checkpoint
重跑策略探索；中断后可用同一 thread_id 续跑。

用法:
    uv run python scripts/tog_research_checkpoint.py
    uv run python scripts/tog_research_checkpoint.py --reset-checkpoint
    uv run python scripts/tog_research_checkpoint.py --thread-id tog-20260803
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from long_earn.core.stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

from loguru import logger  # noqa: E402

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

_DEFAULT_IDEA = (
    "探索比现有净利润同比增长选股更稳健的新策略："
    "可考虑质量因子(ROE/毛利率稳定性)、低波动+动量、或财务质量与价格动量的组合；"
    "请走 prepare_context→图探索→算子清单→编译YAML→训练集回测→run_oos_gates，"
    "仅在OOS门通过后记录成功路径；目标是提升夏普并控制回撤，"
    "产出可回测的最佳候选策略YAML与证据摘要。"
)


def main() -> None:
    from langgraph.checkpoint.sqlite import SqliteSaver

    from long_earn.config import AppConfig
    from long_earn.context_init import initialize_context
    from long_earn.core.storage import best_strategy_path, checkpoint_db_path
    from long_earn.strategy_rd.research_agent import ResearchAgent

    idea = _DEFAULT_IDEA
    thread_id = "tog-research"
    reset = "--reset-checkpoint" in sys.argv
    if "--thread-id" in sys.argv:
        idx = sys.argv.index("--thread-id")
        thread_id = sys.argv[idx + 1]
    if "--idea" in sys.argv:
        idx = sys.argv.index("--idea")
        idea = sys.argv[idx + 1]

    ckpt_path = checkpoint_db_path()
    if reset and ckpt_path.exists():
        # Sqlite WAL 旁路文件一并清掉，避免半截连接
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(ckpt_path) + suffix) if suffix else ckpt_path
            if p.exists():
                p.unlink()
                logger.info(f"已清除 checkpoint: {p}")

    config = AppConfig.from_env()
    config.backtest_start_date = config.train_start_date
    config.backtest_end_date = config.train_end_date
    ctx = initialize_context(config)

    conn = sqlite3.connect(str(ckpt_path), check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    logger.info("=" * 64)
    logger.info("ToG ResearchAgent + SqliteSaver")
    logger.info(f"  thread_id : {thread_id}")
    logger.info(f"  checkpoint: {ckpt_path}")
    logger.info(f"  train     : {config.train_start_date} ~ {config.train_end_date}")
    logger.info(f"  test(OOS) : {config.test_start_date} ~ {config.test_end_date}")
    logger.info(f"  idea      : {idea[:80]}...")
    logger.info("=" * 64)

    agent = ResearchAgent(ctx, checkpointer=checkpointer)
    try:
        result = agent.invoke(idea, thread_id=thread_id)
    finally:
        conn.close()

    print("\n" + "=" * 60)
    print("ToG 结果摘要")
    print("=" * 60)
    print(result.get("summary", "无结果"))
    beams = result.get("beam_paths") or []
    if beams:
        print(f"\nbeam_paths ({len(beams)}):")
        for i, b in enumerate(beams[:5], 1):
            print(f"  [{i}] {str(b)[:200]}")
    print(f"\n最佳策略路径: {best_strategy_path()}")


if __name__ == "__main__":
    main()

"""统一命令行入口 — 基于 typer 的多入口架构。

子命令:
    research   策略研究循环（多轮 Reflexion 研发）
    download   下载行情与财务数据到 DuckDB 缓存
    agent      主 Agent 调用（意图路由到子图）
    web        启动回测可视化 Web 服务

用法:
    long-earn research "基于净利润增长和ROE的选股策略"
    long-earn download --universe all_a
    long-earn agent "分析净利润增长策略"
    long-earn web --port 8090

也可通过 scripts/ 下的薄入口调用，等价于对应子命令。
"""

from __future__ import annotations

import sys
from typing import Optional

import typer
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

# 统一日志格式（与原 scripts 一致）
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

app = typer.Typer(
    name="long-earn",
    help="自我进化的量化交易系统 — CLI 多入口",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


# ── research: 策略研究循环 ────────────────────────────────────────


_DEFAULT_IDEA = "研究一个基于净利润增长和ROE的选股策略，要求近三个月收益率最大化"


@app.command()
def research(
    idea: Optional[str] = typer.Argument(
        None,
        help="初始交易策略或交易思路（缺省时使用默认思路）",
    ),
    max_rounds: int = typer.Option(
        5, "--max-rounds", help="最大研究轮次"
    ),
    max_iterations: int = typer.Option(
        2, "--max-iterations", help="每轮子图内部最大迭代次数"
    ),
    min_improvement: float = typer.Option(
        0.005, "--min-improvement", help="近三个月收益率最小改善幅度"
    ),
    yes: bool = typer.Option(
        False, "-y", "--yes", help="跳过启动确认提示，直接开始研究"
    ),
) -> None:
    """策略研究循环 —— 以初始交易思路驱动的多轮研发与回测。"""
    from long_earn.config import AppConfig
    from long_earn.context_init import initialize_context
    from long_earn.core.storage import best_strategy_path, strategy_results_path
    from long_earn.services.strategy_research_service import StrategyResearchService

    idea_str = idea or _DEFAULT_IDEA
    config = AppConfig.from_env()

    history_window = f"{config.train_start_date} ~ {config.test_end_date}"
    recent_window = f"{config.validation_start_date} ~ {config.validation_end_date}"

    _print_research_banner(idea_str, config, history_window, recent_window)

    if not _confirm_start(yes):
        typer.echo("已取消。")
        raise typer.Exit()

    config.backtest_start_date = config.train_start_date
    config.backtest_end_date = config.test_end_date
    ctx = initialize_context(config)

    service = StrategyResearchService(ctx)
    service.run_loop(
        idea=idea_str,
        max_rounds=max_rounds,
        max_iterations=max_iterations,
        min_improvement=min_improvement,
    )

    typer.echo(f"\n结果文件: {strategy_results_path()}")
    typer.echo(f"最佳策略: {best_strategy_path()}")


def _print_research_banner(
    idea: str,
    config: "object",
    history_window: str,
    recent_window: str,
) -> None:
    """打印 research 子命令启动横幅。"""
    from long_earn.core.storage import best_strategy_path, strategy_results_path

    results_file = strategy_results_path()
    best_file = best_strategy_path()

    width = 64
    idea_display = idea
    if len(idea_display) > width - 6:
        idea_display = idea_display[: width - 9] + "..."

    typer.echo("\n" + "=" * width)
    typer.echo(
        " " * ((width - 28) // 2) + "策略研究循环 / Strategy Research Loop"
    )
    typer.echo("=" * width)
    typer.echo(f"  研究思路 : {idea_display}")
    typer.echo(f"  LLM 模型 : {config.llm_type} / {config.llm_model}")
    typer.echo(f"  训练区间 : {history_window}")
    typer.echo(f"  验证区间 : {recent_window}")
    typer.echo("-" * width)
    typer.echo(f"  结果文件 : {results_file}")
    typer.echo(f"  最佳策略 : {best_file}")
    typer.echo("=" * width + "\n")


def _confirm_start(yes: bool) -> bool:
    """启动前确认提示。``-y`` 或非交互终端跳过。"""
    if yes or not sys.stdin.isatty():
        return True
    try:
        answer = typer.prompt(
            "按 Enter 开始，输入 q 退出",
            default="",
            show_default=False,
        )
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() != "q"


# ── download: 数据下载 ───────────────────────────────────────────


@app.command()
def download(
    universe: str = typer.Option(
        "all",
        "--universe",
        "-u",
        help="股票池类型（all/all_a/etf/csi300/csi500/sse50/csi1000）",
    ),
    start: str = typer.Option(
        "", "--start", help="起始日期 YYYY-MM-DD，空=最长历史"
    ),
    end: str = typer.Option(
        "", "--end", help="结束日期 YYYY-MM-DD，空=今天"
    ),
    skip_financial: bool = typer.Option(
        False, "--skip-financial", help="跳过财务数据下载"
    ),
    batch_size: int = typer.Option(
        0, "--batch-size", help="分批下载每批数量（0=自动：行情200/财务100）"
    ),
    max_workers: int = typer.Option(
        4, "--max-workers", help="并发下载子进程数（1-8，默认4）"
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="强制全量重下（默认智能增量：只下载缺失/过期的数据）",
    ),
) -> None:
    """下载行情与财务数据到 DuckDB 缓存。默认智能增量，--full 强制全量。"""
    from long_earn.services.data_ingestion_service import DataIngestionService

    service = DataIngestionService(logger=logger)
    result = service.run(
        universe=universe,
        start_date=start,
        end_date=end,
        skip_financial=skip_financial,
        batch_size=batch_size,
        max_workers=max_workers,
        full=full,
    )

    if result.get("status") != "ok":
        raise typer.Exit(code=1)


# ── agent: 主 Agent 调用 ─────────────────────────────────────────


@app.command()
def agent(
    query: str = typer.Argument(
        "分析净利润增长策略", help="用户查询（意图路由到策略/股票/事件子图）"
    ),
) -> None:
    """主 Agent —— 分析用户查询并路由到子图。"""
    from long_earn.agent import create_main_agent
    from long_earn.context_init import initialize_context

    ctx = initialize_context()
    main_agent = create_main_agent(ctx)

    ctx.logger.info(f"开始处理用户查询: {query}")
    typer.echo(f"正在处理: {query}\n")

    try:
        result = main_agent.invoke({"user_query": query})
        typer.echo("\n" + "=" * 60)
        typer.echo("分析结果:")
        typer.echo("=" * 60)
        typer.echo(result.get("summary", "无结果"))
        ctx.monitoring.log_report(ctx.logger)
    except Exception as e:
        ctx.logger.error(f"执行异常: {e}")
        typer.echo(f"\n执行过程中出现错误: {e}", err=True)
        raise typer.Exit(code=1) from e


# ── web: 可视化服务 ──────────────────────────────────────────────


@app.command()
def web(
    host: str = typer.Option("0.0.0.0", "--host", help="监听地址"),
    port: int = typer.Option(8090, "--port", help="监听端口"),
    db: str = typer.Option("", "--db", help="DuckDB 审计数据库路径"),
    substances: str = typer.Option(
        "", "--substances", help="SubstanceStore JSONL 路径（事件流端点）"
    ),
) -> None:
    """启动回测可视化 Web 服务。"""
    from long_earn.dashboard.api import serve_visualization

    serve_visualization(
        host=host,
        port=port,
        db_path=db,
        substances_path=substances,
    )


if __name__ == "__main__":
    app()

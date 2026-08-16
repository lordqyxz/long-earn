"""统一命令行入口 — 基于 typer 的多入口架构。

子命令:
    research   策略研究循环（多轮 Reflexion 研发）
    optimize   离线策略优化（AcceptanceGate 验收，ADR-009 收尾）
    sync       从 miniQMT 增量同步行情与财务到 DuckDB 主数据层
    agent      主 Agent 调用（意图路由到子图）
    web        启动回测可视化 Web 服务

用法:
    long-earn research "基于净利润增长和ROE的选股策略"
    long-earn optimize --suggestions "增加波动率过滤,降低换手率"
    long-earn download --universe all_a
    long-earn agent "分析净利润增长策略"
    long-earn web --port 8090

也可通过 scripts/ 下的薄入口调用，等价于对应子命令。
"""

from __future__ import annotations

import sys

import typer
from dotenv import load_dotenv
from loguru import logger

from long_earn.core.stdio import ensure_utf8_stdio

load_dotenv()

# Windows 控制台/stdio 尽早切 UTF-8，避免中文日志乱码
ensure_utf8_stdio()

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
    idea: str | None = typer.Argument(
        None,
        help="初始交易策略或交易思路（缺省时使用默认思路）",
    ),
    max_rounds: int = typer.Option(5, "--max-rounds", help="最大研究轮次"),
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
    from long_earn.strategy_rd.research_service import StrategyResearchService

    idea_str = idea or _DEFAULT_IDEA
    config = AppConfig.from_env()

    history_window = f"{config.train_start_date} ~ {config.train_end_date}"
    recent_window = f"训练集内近 6 个月（截止 {config.train_end_date}）"

    _print_research_banner(idea_str, config, history_window, recent_window)

    if not _confirm_start(yes):
        typer.echo("已取消。")
        raise typer.Exit()

    config.backtest_start_date = config.train_start_date
    config.backtest_end_date = config.train_end_date
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
    config: object,
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
    typer.echo(" " * ((width - 28) // 2) + "策略研究循环 / Strategy Research Loop")
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


# ── optimize: 离线策略优化 ───────────────────────────────────────


@app.command()
def optimize(
    strategy_yaml: str = typer.Option(
        "best_strategy.yaml",
        "--strategy-yaml",
        help="基线策略 YAML 文件路径（默认 best_strategy.yaml）",
    ),
    suggestions: str = typer.Option(
        "",
        "--suggestions",
        "-s",
        help="优化建议，逗号分隔（如 '增加波动率过滤,降低换手率'），空则用默认建议",
    ),
    max_iterations: int = typer.Option(
        1,
        "--max-iterations",
        help="最大优化迭代次数（每次迭代 = optimize + backtest + gate）",
    ),
    yes: bool = typer.Option(
        False, "-y", "--yes", help="跳过启动确认提示，直接开始优化"
    ),
) -> None:
    """离线策略优化 —— 对已有策略跑 optimize→backtest→AcceptanceGate 验收循环。

    ADR-009 收尾：暴露 OptimizationPipeline 给研究员手动驱动，
    无需走完整 HTR 循环。AcceptanceGate 严格校验 sharpe 提升，未通过则保留原策略。

    ADR-018：策略研发主入口已迁移至 ResearchAgent（ToG），
    推荐使用 ``python -m long_earn research`` 或直接调用 ResearchAgent.invoke()。
    """
    from pathlib import Path

    from long_earn.config import AppConfig
    from long_earn.context_init import initialize_context
    from long_earn.core.storage import best_strategy_path
    from long_earn.strategy_optimization import (
        AcceptanceGate,
        LLMStrategyOptimizer,
        OptimizationPipeline,
    )
    from long_earn.strategy_rd.agents.strategy_develop_agent import (
        StrategyDevelopAgent,
    )
    from long_earn.strategy_rd.agents.strategy_research_agent import (
        StrategyResearchAgent,
    )

    yaml_path = Path(strategy_yaml)
    if not yaml_path.exists():
        typer.echo(f"策略文件不存在: {yaml_path}", err=True)
        raise typer.Exit(code=1)

    base_yaml = yaml_path.read_text(encoding="utf-8")
    suggestion_list = (
        [s.strip() for s in suggestions.split(",") if s.strip()]
        if suggestions
        else ["在保留主逻辑前提下提升 sharpe"]
    )

    typer.echo("\n" + "=" * 60)
    typer.echo(" 离线策略优化 / Offline Strategy Optimization")
    typer.echo("=" * 60)
    typer.echo(f"  基线策略 : {yaml_path}")
    typer.echo(f"  优化建议 : {suggestion_list}")
    typer.echo(f"  迭代次数 : {max_iterations}")
    typer.echo("=" * 60 + "\n")

    if not _confirm_start(yes):
        typer.echo("已取消。")
        raise typer.Exit()

    config = AppConfig.from_env()
    config.backtest_start_date = config.train_start_date
    config.backtest_end_date = config.train_end_date
    ctx = initialize_context(config)

    optimizer = LLMStrategyOptimizer(StrategyResearchAgent(context=ctx))
    pipeline = OptimizationPipeline(
        optimizer=optimizer,
        backtest_service=ctx.backtest_service,
        gate=AcceptanceGate(),
        logger=ctx.logger,
    )
    develop_agent = StrategyDevelopAgent(context=ctx)

    # 离线优化基线策略字典（minimal — optimizer 内部会读取完整 strategy）
    base_strategy_dict = {"name": "baseline", "source_yaml": str(yaml_path)}
    baseline_backtest: dict | None = None

    for i in range(max_iterations):
        ctx.logger.info(f"[optimize] 第 {i + 1}/{max_iterations} 轮迭代")
        outcome = pipeline.run(
            base_strategy=base_strategy_dict,
            base_strategy_yaml=base_yaml,
            improvement_suggestions=suggestion_list,
            baseline_backtest=baseline_backtest,
        )

        if outcome.accepted and outcome.optimized_strategy:
            # 通过验收：用 develop_agent 把优化版 strategy dict 编译为 YAML 并落盘
            optimized_yaml = develop_agent.develop_strategy(outcome.optimized_strategy)
            ctx.logger.info(
                f"[optimize] 第 {i + 1} 轮通过 AcceptanceGate: "
                f"{outcome.acceptance.baseline_sharpe} → {outcome.acceptance.optimized_sharpe}"
            )
            best_path = best_strategy_path()
            best_path.write_text(optimized_yaml, encoding="utf-8")
            typer.echo(f"第 {i + 1} 轮优化通过，已落盘到 {best_path}")
            base_yaml = optimized_yaml
            baseline_backtest = outcome.optimized_backtest
        else:
            ctx.logger.warning(
                f"[optimize] 第 {i + 1} 轮未通过 AcceptanceGate: {outcome.acceptance.reason}"
            )
            typer.echo(f"第 {i + 1} 轮未通过验收：{outcome.acceptance.reason}")

    typer.echo("\n优化结束。")


# ── sync: miniQMT 增量同步 ───────────────────────────────────────


def _run_sync(
    universe: str = typer.Option(
        "all",
        "--universe",
        "-u",
        help="股票池类型（all/all_a/etf/csi300/csi500/sse50/csi1000）",
    ),
    start: str = typer.Option("", "--start", help="起始日期 YYYY-MM-DD，空=最长历史"),
    end: str = typer.Option("", "--end", help="结束日期 YYYY-MM-DD，空=今天"),
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
    """从 miniQMT 同步行情与财务到 DuckDB 主数据层。"""
    from long_earn.services.incremental_sync import IncrementalSyncService

    service = IncrementalSyncService(logger=logger)
    report = service.sync(
        universe=universe,
        start_date=start,
        end_date=end,
        skip_financial=skip_financial,
        batch_size=batch_size,
        max_workers=max_workers,
        full=full,
    )

    if report.status != "ok":
        raise typer.Exit(code=1)


app.command(name="sync")(_run_sync)
app.command(name="download", hidden=True)(_run_sync)


# ── agent: 主 Agent 调用 ─────────────────────────────────────────


@app.command()
def agent(
    query: str = typer.Argument(
        "分析净利润增长策略", help="用户查询（主智能体 ReAct 调度）"
    ),
) -> None:
    """主智能体 —— ReAct 任务分解 + 工具调度（ADR-016）。"""
    from long_earn.context_init import initialize_context
    from long_earn.master_agent import MasterAgent

    ctx = initialize_context()
    master_agent = MasterAgent(ctx)

    ctx.logger.info(f"开始处理用户查询: {query}")
    typer.echo(f"正在处理: {query}\n")

    try:
        result = master_agent.invoke(query)
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
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(8090, "--port", help="监听端口"),
    db: str = typer.Option("", "--db", help="DuckDB 审计数据库路径"),
    substances: str = typer.Option(
        "", "--substances", help="SubstanceStore JSONL 路径（事件流端点）"
    ),
    fastapi: bool = typer.Option(
        True, "--fastapi/--no-fastapi", help="使用 FastAPI + WebSocket（默认启用）"
    ),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="明确允许绑定非本机地址；远程部署仍需认证和网络访问控制",
    ),
) -> None:
    """启动回测可视化 Web 服务。

    默认使用 FastAPI + Uvicorn，支持 WebSocket 实时事件流推送。
    使用 --no-fastapi 回退到 stdlib http.server 旧版。
    """
    if fastapi:
        from long_earn.app.app import serve_visualization_fastapi

        serve_visualization_fastapi(
            host=host,
            port=port,
            db_path=db,
            substances_path=substances,
            allow_remote=allow_remote,
        )
    else:
        serve_visualization_fastapi(
            host=host,
            port=port,
            db_path=db,
            substances_path=substances,
            allow_remote=allow_remote,
        )


if __name__ == "__main__":
    app()

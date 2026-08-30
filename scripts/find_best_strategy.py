#!/usr/bin/env python3
"""寻找最近6个月收益率最佳的策略 — 尊重 config 三段式数据分割规范。

流程：
1. 查询 PostgreSQL 缓存数据实际覆盖范围（仅作日志展示，不覆盖 config 日期）。
2. 使用 AppConfig.from_env() 默认值（或环境变量 TRAIN_START/TEST_END/VALIDATION_*
   等）作为三段式数据分割窗口，**严格遵守量化数据分割规范**，不得交叉使用。
3. 根据财务表是否非空，自动选择 idea（纯量价 vs 量价+基本面）。
4. 启动策略研发循环。
5. 循环产出 best_strategy.yaml + strategy_research_results.json。

可选：``--auto-window`` 标志根据数据最新日动态推导窗口（覆盖 config），
仅用于探索性分析，默认关闭以遵守数据分割规范。

checkpoint 机制（默认启用）：
- 用 LangGraph ``SqliteSaver`` 持久化每轮子图状态到 ``checkpoints.sqlite``。
- 同一 ``thread_id`` 已完成时直接复用结果，未完成时从中断处续跑。
- 加 ``--no-checkpoint`` 禁用，加 ``--reset-checkpoint`` 清空旧 checkpoint。

用法:
    uv run python scripts/find_best_strategy.py
    uv run python scripts/find_best_strategy.py --max-rounds 3 -y
    uv run python scripts/find_best_strategy.py --max-iterations 8 -y  # HTR 循环上限=8
    uv run python scripts/find_best_strategy.py --auto-window -y  # 动态窗口（覆盖 config）
    uv run python scripts/find_best_strategy.py --reset-checkpoint -y  # 清旧 checkpoint 重跑
    uv run python scripts/find_best_strategy.py --final-validation -y
    # ↑ 仅限最终评估场景：显式消耗验证集的唯一一次触碰（铁律 #3），默认关闭

验证集触碰纪律（铁律 #3）：研发循环默认**不触碰**验证集。双段前瞻验证仅在
显式传入 ``--final-validation`` 旗标时执行一次；开发阶段严禁使用该旗标，
否则验证集业绩失去对外报告效力。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# 关键：加载 .env，让 HTR_MAX_CYCLES / LONG_EARN_SKIP_CACHE_SYNC / HTR_MAX_SELECT
# 等环境变量生效。缺失此调用会导致 .env 配置被忽略，HTR 用默认值或卡在数据同步。
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# Windows 中文乱码修复：脚本入口先切 UTF-8（spawn worker 子进程不继承主进程编码）
from long_earn.core.stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


def probe_data_coverage() -> dict:
    """查询 PostgreSQL 缓存数据的实际覆盖范围。

    财务数据自 ADR-014 阶段 B 起从单表 ``financial_quarterly`` 拆为 8 张细表
    （income_stmt / balance_sheet / cashflow_stmt / pershareindex 等）。
    这里以 ``pershareindex``（含 ROE/毛利率等衍生指标）作为"是否有财务数据"
    的代理判断；表不存在时视作财务数据未下载。

    PG 全量迁移后：直接从 PostgreSQL 读取（core.pg 裁决连接参数）。
    """
    from long_earn.core.pg import pg_connect

    conn = pg_connect(read_only=True)
    try:
        r = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*), COUNT(DISTINCT symbol) "
            "FROM price_daily"
        ).fetchone()
        price_min, price_max, price_rows, price_symbols = r

        fin_rows = 0
        fin_symbols = 0
        # 优雅降级：pershareindex 表可能尚未创建（新装环境）
        try:
            f = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM pershareindex"
            ).fetchone()
            fin_rows, fin_symbols = int(f[0]), int(f[1])
        except Exception:
            pass

        return {
            "price_min": str(price_min),
            "price_max": str(price_max),
            "price_rows": int(price_rows),
            "price_symbols": int(price_symbols),
            "fin_rows": fin_rows,
            "fin_symbols": fin_symbols,
        }
    finally:
        conn.close()


def derive_windows(coverage: dict) -> dict:
    """根据数据实际覆盖，程序性推导训练集与评估窗口。

    逻辑（非硬编码默认，全部基于数据实际最新日）：
    - recent_end = 数据最新交易日
    - recent_start = recent_end 向前 6 个月（约 183 天）
    - train_end = recent_start 向前推 1 个交易日留 gap（防止边界泄漏），简化为向前 1 天
    - train_start = train_end 向前 3 年
    """
    latest = date.fromisoformat(coverage["price_max"])
    recent_end = latest
    recent_start = recent_end - timedelta(days=183)
    train_end = recent_start - timedelta(days=1)
    train_start = train_end.replace(year=train_end.year - 3)

    return {
        "train_start": train_start.isoformat(),
        "train_end": train_end.isoformat(),
        "recent_start": recent_start.isoformat(),
        "recent_end": recent_end.isoformat(),
    }


def validate_best_strategy_dual_quarter(
    strategy_yaml: str,
    ctx: RuntimeContext,
    q1_start: str | None = None,
    q1_end: str | None = None,
    q2_start: str | None = None,
    q2_end: str | None = None,
    min_return_threshold: float = 0.0,
) -> dict:
    """对最佳策略做双段前瞻验证（验证集触碰，仅限最终评估场景调用）。

    铁律 #3：验证集仅最终评估时触碰一次。本函数默认不再随研发循环自动执行，
    仅当调用方显式要求（CLI ``--final-validation`` 旗标）时调用。
    窗口默认从 config.validation_start_date / validation_end_date 对半拆分
    为前后两段；两个窗口的 total_return 都需 > min_return_threshold 才算通过。

    Args:
        strategy_yaml: 最佳策略 YAML
        ctx: RuntimeContext（提供 backtest_service 与 config）
        q1_start/q1_end: 前半验证窗口；None 时从 config 验证集区间对半派生
        q2_start/q2_end: 后半验证窗口；None 时从 config 验证集区间对半派生
        min_return_threshold: 收益阈值（默认 0.0，即不亏损）

    Returns:
        dict: {
            "q1_return": float, "q1_sharpe": float, "q1_drawdown": float,
            "q2_return": float, "q2_sharpe": float, "q2_drawdown": float,
            "passed": bool,  # 两个窗口都达标才 True
            "reason": str,
        }
        其中 q1_* 为前半段结果、q2_* 为后半段结果（键名保留历史兼容）。
    """
    # 窗口派生：默认从 config 验证集区间对半拆分（不硬编码日历季度）
    config = ctx.config
    val_start = date.fromisoformat(config.validation_start_date)
    val_end = date.fromisoformat(config.validation_end_date)
    half_days = ((val_end - val_start).days + 1) // 2
    q1_start = q1_start or config.validation_start_date
    q1_end = q1_end or (val_start + timedelta(days=half_days - 1)).isoformat()
    q2_start = q2_start or (val_start + timedelta(days=half_days)).isoformat()
    q2_end = q2_end or config.validation_end_date

    backtest = ctx.require_backtest()

    print()
    print("=" * 64)
    print("双段前瞻验证（验证集对半拆分：前半段 + 后半段）")
    print("=" * 64)
    print(f"  前半段窗口: {q1_start} ~ {q1_end}")
    print(f"  后半段窗口: {q2_start} ~ {q2_end}")
    print(f"  收益阈值: {min_return_threshold:.4f}")
    print("-" * 64)

    def _run_half(name: str, start: str, end: str) -> dict:
        print(f"  正在回测 {name} ({start} ~ {end})...")
        report = backtest.run(
            strategy_yaml=strategy_yaml,
            start_date=start,
            end_date=end,
        )
        if "error" in report:
            print(f"  {name} 回测失败: {report['error']}")
            return {"return": -999.0, "sharpe": -999.0, "drawdown": -999.0}
        ret = float(report.get("total_return", -999.0))
        sharpe = float(report.get("sharpe_ratio", -999.0))
        drawdown = float(report.get("max_drawdown", -999.0))
        print(
            f"  {name}: return={ret:.4f}, sharpe={sharpe:.2f}, "
            f"drawdown={drawdown:.4f}"
        )
        return {"return": ret, "sharpe": sharpe, "drawdown": drawdown}

    q1 = _run_half("前半段", q1_start, q1_end)
    q2 = _run_half("后半段", q2_start, q2_end)

    q1_pass = q1["return"] > min_return_threshold
    q2_pass = q2["return"] > min_return_threshold
    passed = q1_pass and q2_pass

    print("-" * 64)
    if passed:
        print(
            f"  ✅ 双段验证通过：前半段={q1['return']:.4f}, "
            f"后半段={q2['return']:.4f} 均 > {min_return_threshold:.4f}"
        )
        reason = "两个窗口收益均达标"
    else:
        failed = []
        if not q1_pass:
            failed.append(f"前半段={q1['return']:.4f}")
        if not q2_pass:
            failed.append(f"后半段={q2['return']:.4f}")
        print(
            f"  ❌ 双季度验证未通过：{', '.join(failed)} 未达阈值 "
            f"{min_return_threshold:.4f}"
        )
        reason = f"未达标窗口: {', '.join(failed)}"
    print("=" * 64)

    return {
        "q1_return": q1["return"],
        "q1_sharpe": q1["sharpe"],
        "q1_drawdown": q1["drawdown"],
        "q2_return": q2["return"],
        "q2_sharpe": q2["sharpe"],
        "q2_drawdown": q2["drawdown"],
        "passed": passed,
        "reason": reason,
    }


def pick_idea(coverage: dict) -> str:
    """根据财务表是否非空，自动选择 idea。

    财务表为空时只能用纯量价思路；非空时可叠加基本面。
    """
    if coverage["fin_rows"] > 0:
        return (
            "研究一个结合动量、波动率与ROE的选股策略，"
            "用近20日收益率动量排序、近20日波动率过滤高波动标的、"
            "ROE 过滤盈利能力差的标的，要求最近六个月收益率最大化"
        )
    return (
        "研究一个纯量价的多因子选股策略，仅用价格和成交量数据："
        "用近20日收益率动量排序选强势股、近20日已实现波动率过滤高波动标的、"
        "近5日成交量比过滤缩量标的，等权持仓5-10只，"
        "要求最近六个月收益率最大化、最大回撤控制在15%以内"
    )


def main() -> None:  # noqa: PLR0912
    import sqlite3

    coverage = probe_data_coverage()
    print("=" * 64)
    print("数据实际覆盖（程序探测）")
    print("=" * 64)
    print(f"  行情: {coverage['price_min']} ~ {coverage['price_max']}")
    print(f"        {coverage['price_symbols']} 只, {coverage['price_rows']} 行")
    print(f"  财务: {coverage['fin_rows']} 行, {coverage['fin_symbols']} 只")

    w = derive_windows(coverage)
    idea = pick_idea(coverage)

    # 默认遵守 config 三段式数据分割规范（不覆盖 config 日期）
    # --auto-window 标志才启用动态窗口覆盖（探索性分析用）
    auto_window = "--auto-window" in sys.argv

    print()
    print("数据分割窗口")
    print("-" * 64)
    if auto_window:
        print("  [动态窗口模式 --auto-window]")
        print(f"  训练集（研发寻优）: {w['train_start']} ~ {w['train_end']}")
        print(f"  最近6个月评估窗口: {w['recent_start']} ~ {w['recent_end']}")
    else:
        print("  [config 三段式分割（遵守数据分割规范）]")
    print(f"  idea: {idea[:60]}...")
    print("=" * 64)

    # 构造 AppConfig
    from long_earn.config import AppConfig
    from long_earn.context_init import initialize_context
    from long_earn.core.storage import (
        best_strategy_path,
        checkpoint_db_path,
        strategy_results_path,
    )
    from long_earn.strategy_rd.research_service import StrategyResearchService

    config = AppConfig.from_env()

    if auto_window:
        # 仅在 --auto-window 时覆盖 config 日期（探索性分析用）
        config.train_start_date = w["train_start"]
        config.train_end_date = w["train_end"]
        config.test_start_date = w["train_start"]
        config.test_end_date = w["train_end"]
        config.validation_start_date = w["recent_start"]
        config.validation_end_date = w["recent_end"]
        config.backtest_start_date = w["train_start"]
        config.backtest_end_date = w["train_end"]

    # 始终确保 HTR dev 回测区间严格限定在训练集（铁律 #1/#2：dev/参数寻优只能用训练集，
    # 测试集仅供 _decide 节点合并门触碰，验证集仅最终评估一次）
    config.backtest_start_date = config.train_start_date
    config.backtest_end_date = config.train_end_date

    print()
    print("实际使用窗口")
    print("-" * 64)
    print(f"  训练集: {config.train_start_date} ~ {config.train_end_date}")
    print(f"  测试集: {config.test_start_date} ~ {config.test_end_date}")
    print(f"  验证集: {config.validation_start_date} ~ {config.validation_end_date}")
    print("=" * 64)

    max_rounds = 3
    # None 表示用 config.max_iterations（来自 .env 的 MAX_ITERATIONS）
    cli_max_iterations: int | None = None
    yes = False
    use_checkpoint = "--no-checkpoint" not in sys.argv
    reset_checkpoint = "--reset-checkpoint" in sys.argv

    if "--max-rounds" in sys.argv:
        idx = sys.argv.index("--max-rounds")
        max_rounds = int(sys.argv[idx + 1])
    if "--max-iterations" in sys.argv:
        idx = sys.argv.index("--max-iterations")
        cli_max_iterations = int(sys.argv[idx + 1])
    if "-y" in sys.argv or "--yes" in sys.argv:
        yes = True

    # CLI --max-iterations 同时覆盖 config.max_iterations 和 config.htr_max_cycles
    # （前置约束 #4：HTR 迭代上限必须通过 CLI --max-iterations 配置，禁止硬编码）
    if cli_max_iterations is not None:
        config.max_iterations = cli_max_iterations
        config.htr_max_cycles = cli_max_iterations
    max_iterations = config.max_iterations

    ckpt_path = checkpoint_db_path()
    if reset_checkpoint and ckpt_path.exists():
        print(f"清除旧 checkpoint: {ckpt_path}")
        ckpt_path.unlink()

    print()
    print("HTR 与 checkpoint 配置")
    print("-" * 64)
    print(f"  max_rounds       : {max_rounds}")
    print(f"  max_iterations   : {max_iterations}（子图 supervisor 迭代）")
    print(f"  htr_max_cycles   : {config.htr_max_cycles}（HTR 六步循环上限）")
    print(f"  htr_max_select   : {config.htr_max_select}（并行 fan-out）")
    print(f"  max_workers      : {config.max_workers}（并行回测 worker 数）")
    print(f"  checkpoint 启用  : {use_checkpoint}")
    print(f"  checkpoint 路径  : {ckpt_path}")
    print("=" * 64)

    if not yes and sys.stdin.isatty():
        print()
        ans = input("按 Enter 开始研发循环，输入 q 退出: ").strip().lower()
        if ans == "q":
            print("已取消。")
            return

    ctx = initialize_context(config)
    service = StrategyResearchService(ctx)

    if use_checkpoint:
        # SqliteSaver 需要长连接；用 context manager 保证结束时关闭
        from langgraph.checkpoint.sqlite import SqliteSaver

        conn = sqlite3.connect(str(ckpt_path), check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        try:
            summary = service.run_loop(
                idea=idea,
                max_rounds=max_rounds,
                max_iterations=max_iterations,
                min_improvement=0.005,
                checkpointer=checkpointer,
                thread_id_prefix=f"find-best-{config.validation_start_date}",
            )
        finally:
            conn.close()
    else:
        summary = service.run_loop(
            idea=idea,
            max_rounds=max_rounds,
            max_iterations=max_iterations,
            min_improvement=0.005,
        )

    print()
    print("=" * 64)
    print("研发循环完成")
    print("=" * 64)
    print(f"  最佳最近6个月收益率: {summary.best_recent_return:.4f}")
    print(f"  最佳轮次: 第{summary.best_round}轮")
    print(f"  最佳历史收益率: {summary.best_history_return:.4f}")
    print(f"  评估窗口: {summary.recent_eval_window}")
    print(f"  训练窗口: {summary.history_eval_window}")
    print(f"  结果文件: {strategy_results_path()}")
    print(f"  最佳策略: {best_strategy_path()}")

    if summary.best_strategy_yaml:
        best_strategy_path().write_text(
            summary.best_strategy_yaml, encoding="utf-8"
        )
        print()
        print("最佳策略 YAML（前 800 字符）:")
        print("-" * 64)
        print(summary.best_strategy_yaml[:800])
        if len(summary.best_strategy_yaml) > 800:
            print(f"... ({len(summary.best_strategy_yaml)} 字符总计)")

        # 双段前瞻验证（铁律 #3：验证集仅最终评估时触碰一次）
        # 默认不执行；仅当显式传入 --final-validation 旗标（默认 False）
        # 才运行，防止研发循环反复触碰验证集构成对验证集的调优反馈通道
        if "--final-validation" in sys.argv:
            print()
            print("!" * 64)
            print("!! 醒目警告：本次运行将消耗验证集的唯一一次触碰（铁律 #3）")
            print("!! 仅限最终评估场景执行；开发阶段严禁使用该旗标")
            print("!! 此前若已触碰过验证集，请勿再次运行本验证")
            print("!" * 64)
            validation = validate_best_strategy_dual_quarter(
                strategy_yaml=summary.best_strategy_yaml,
                ctx=ctx,
            )
            # 将验证结果追加到 strategy_research_results.json
            import json

            results_path = strategy_results_path()
            if results_path.exists():
                payload = json.loads(
                    results_path.read_text(encoding="utf-8")
                )
            else:
                payload = {}
            payload["dual_quarter_validation"] = validation
            results_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(f"  验证结果已写入: {results_path}")


if __name__ == "__main__":
    main()

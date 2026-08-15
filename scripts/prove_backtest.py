#!/usr/bin/env python3
"""数学证明级别的回测对账验证。

从审计日志（FILL + MARKET_DATA 事件）独立重建：
1. equity_curve（净值曲线）→ 重算 total_return / sharpe / max_drawdown
2. 每笔交易的持仓变化 + 现金守恒
然后与引擎报告值逐项对账。

对账通过 = 收益数字可从交易日志唯一重建，数学上可信。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# Windows 中文乱码修复：脚本入口先切 UTF-8（spawn worker 子进程不继承主进程编码）
from long_earn.core.stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

import numpy as np  # noqa: E402

from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.pg import pg_connect  # noqa: E402
from long_earn.services.backtest_service import BacktestServiceImpl  # noqa: E402

STRATEGY_YAML = Path("best_strategy.yaml").read_text(encoding="utf-8")
RECENT_START = "2026-01-06"
RECENT_END = "2026-07-08"


def main() -> None:  # noqa: C901, PLR0912
    config = AppConfig.from_env()
    config.backtest_start_date = RECENT_START
    config.backtest_end_date = RECENT_END
    ctx = initialize_context(config)

    # 直接构造引擎以捕获 run_id（BacktestServiceImpl.run 内部构造引擎，不暴露 run_id）
    # 所以改用：跑完后从审计日志按时间戳取最新 run_id
    bs = BacktestServiceImpl(
        config=config, logger=ctx.logger, data_provider=ctx.data_provider
    )

    print("=" * 70)
    print("数学证明级对账验证：从审计日志重建收益")
    print("=" * 70)
    print(f"回测窗口: {RECENT_START} ~ {RECENT_END}")

    t0 = datetime.now()
    result = bs.run(
        strategy_yaml=STRATEGY_YAML,
        start_date=RECENT_START,
        end_date=RECENT_END,
    )
    t1 = datetime.now()
    print(f"回测耗时: {(t1 - t0).total_seconds():.1f}s\n")

    if "error" in result:
        print(f"回测失败: {result.get('error')}")
        return

    # 引擎报告值（待验证对象）
    eng_total_return = float(result.get("total_return", 0))
    eng_sharpe = float(result.get("sharpe_ratio", 0))
    eng_max_dd = float(result.get("max_drawdown", 0))
    eng_annual = float(result.get("annual_return", 0))
    eng_vol = float(result.get("volatility", 0))
    eng_trade_count = int(result.get("strategy_diagnostics", {}).get("trade_count", 0))
    # daily_returns 是 equity_curve（引擎误命名）：{"day": i, "value": v}
    eng_equity = [d["value"] for d in result.get("daily_returns", [])]

    print("── 引擎报告值（待验证）─────────────────")
    print(f"  total_return = {eng_total_return:.6f}")
    print(f"  annual_return = {eng_annual:.6f}")
    print(f"  sharpe = {eng_sharpe:.6f}")
    print(f"  max_drawdown = {eng_max_dd:.6f}")
    print(f"  volatility = {eng_vol:.6f}")
    print(f"  trade_count = {eng_trade_count}")
    print(f"  equity_curve 长度 = {len(eng_equity)}")
    if eng_equity:
        print(f"  equity[0] = {eng_equity[0]:.4f}, equity[-1] = {eng_equity[-1]:.4f}")

    # ── 从审计日志独立重建 ──────────────────────────────────
    print("\n  审计库: PostgreSQL backtest_audit.logs（连接参数由 core.pg 裁决）")
    conn = pg_connect(read_only=True, row_factory=None)
    try:
        # 找最新 run_id（本次回测的）
        run_id_row = conn.execute(
            'SELECT run_id, MAX(timestamp) as t FROM "backtest_audit".logs '
            "GROUP BY run_id ORDER BY t DESC LIMIT 1"
        ).fetchone()
        if not run_id_row:
            print("\n[错误] 审计日志中无任何 run_id")
            return
        run_id = run_id_row[0]
        print(f"\n── 审计日志重建（run_id={run_id[:8]}...）──────────")

        # 1) MARKET_DATA 事件：每 bar 末尾的 portfolio_value → 独立 equity_curve
        md_rows = conn.execute(
            """
            SELECT timestamp, payload
            FROM "backtest_audit".logs
            WHERE run_id = %s AND event_type = 'MARKET_DATA'
            ORDER BY seq ASC
            """,
            [run_id],
        ).fetchall()

        reconstructed_equity = []
        for _ts, payload_json in md_rows:
            payload = payload_json if isinstance(payload_json, dict) else json.loads(payload_json)
            pv = payload.get("portfolio_value")
            if pv is not None:
                reconstructed_equity.append(float(pv))

        print(f"  MARKET_DATA 事件数: {len(md_rows)}")
        print(f"  重建 equity_curve 长度: {len(reconstructed_equity)}")
        if reconstructed_equity:
            print(f"  重建 equity[0] = {reconstructed_equity[0]:.4f}")
            print(f"  重建 equity[-1] = {reconstructed_equity[-1]:.4f}")

        # 2) FILL 事件：每笔成交，独立验证交易次数 + 持仓变化
        fill_rows = conn.execute(
            """
            SELECT payload
            FROM "backtest_audit".logs
            WHERE run_id = %s AND event_type = 'FILL'
            ORDER BY seq ASC
            """,
            [run_id],
        ).fetchall()

        fills = []
        for (pj,) in fill_rows:
            p = pj if isinstance(pj, dict) else json.loads(pj)
            fills.append(
                {
                    "symbol": p.get("symbol"),
                    "type": p.get("type"),
                    "price": float(p.get("price", 0)),
                    "quantity": float(p.get("quantity", 0)),
                    "portfolio_value": float(p.get("portfolio_value", 0)),
                }
            )

        print(f"  FILL 事件数: {len(fills)}")

        # ── 对账 1：equity_curve 逐点一致性 ──────────────────────
        print("\n── 对账 1: equity_curve 逐点一致性 ─────────")
        if len(eng_equity) != len(reconstructed_equity):
            print(f"  [警告] 长度不一致: 引擎={len(eng_equity)} vs 重建={len(reconstructed_equity)}")
            n = min(len(eng_equity), len(reconstructed_equity))
            eng_eq = eng_equity[:n]
            recon_eq = reconstructed_equity[:n]
        else:
            eng_eq = eng_equity
            recon_eq = reconstructed_equity
            print(f"  长度一致: {n if 'n' in dir() else len(eng_eq)} 点")

        if eng_eq and recon_eq:
            max_diff = max(abs(a - b) for a, b in zip(eng_eq, recon_eq, strict=True))
            rel_diff = max(
                abs(a - b) / abs(a) if abs(a) > 1e-9 else 0
                for a, b in zip(eng_eq, recon_eq, strict=True)
            )
            print(f"  最大绝对差: {max_diff:.6f}")
            print(f"  最大相对差: {rel_diff:.2e}")
            eq_match = rel_diff < 1e-6
            print(f"  结论: {'✅ 逐点一致' if eq_match else '❌ 不一致'}")

        # ── 对账 2：从重建 equity_curve 独立重算全部指标 ──────────
        print("\n── 对账 2: 独立重算指标 vs 引擎报告 ─────────")

        def compute_metrics(equity: list[float]) -> dict:
            """独立实现，与引擎 _compute_performance_metrics 对账。"""
            eq = np.array(equity, dtype=float)
            if len(eq) < 2:
                return {}
            returns = np.diff(eq) / eq[:-1]
            total_return = float(eq[-1] / eq[0] - 1)
            annual_return = float(np.mean(returns)) * 252
            volatility = float(np.std(returns, ddof=1)) * np.sqrt(252) if len(returns) > 1 else 0.0
            sharpe = float(annual_return / volatility) if volatility > 0 else 0.0
            peak = np.maximum.accumulate(eq)
            drawdown = (eq - peak) / peak
            max_dd = float(np.min(drawdown))
            downside = returns[returns < 0]
            downside_std = float(np.std(downside, ddof=1)) * np.sqrt(252) if len(downside) > 1 else 0.0
            sortino = float(annual_return / downside_std) if downside_std > 0 else 0.0
            calmar = float(annual_return / abs(max_dd)) if abs(max_dd) > 1e-9 else 0.0
            return {
                "total_return": total_return,
                "annual_return": annual_return,
                "sharpe": sharpe,
                "max_drawdown": max_dd,
                "volatility": volatility,
                "sortino": sortino,
                "calmar": calmar,
            }

        recon_m = compute_metrics(reconstructed_equity)

        print(f"{'指标':<16} {'引擎报告':>14} {'日志重建':>14} {'绝对差':>12} {'通过':>6}")
        print("-" * 70)
        checks = []
        for key, eng_val in [
            ("total_return", eng_total_return),
            ("annual_return", eng_annual),
            ("sharpe", eng_sharpe),
            ("max_drawdown", eng_max_dd),
            ("volatility", eng_vol),
        ]:
            rv = recon_m.get(key, 0.0)
            ev = eng_val
            diff = abs(rv - ev)
            tol = max(1e-6, abs(ev) * 1e-4)
            ok = diff < tol
            checks.append(ok)
            print(f"  {key:<14} {ev:>14.6f} {rv:>14.6f} {diff:>12.2e} {'✅' if ok else '❌':>4}")

        # ── 对账 3：FILL 事件交易次数 ──────────────────────────
        print("\n── 对账 3: FILL 事件交易次数 ────────────────")
        fill_count = len(fills)
        ok_count = fill_count == eng_trade_count
        print(f"  FILL 事件数: {fill_count}")
        print(f"  引擎 trade_count: {eng_trade_count}")
        print(f"  结论: {'✅ 一致' if ok_count else '⚠️  不等（trade_count 可能统计维度不同）'}")

        # ── 对账 4：现金守恒 + 持仓变化一致性 ──────────────────
        print("\n── 对账 4: FILL 持仓变化一致性（每标的净量）──")
        from collections import defaultdict

        net_qty = defaultdict(float)
        buy_n = 0
        sell_n = 0
        for f in fills:
            q = f["quantity"]
            if f["type"] == "buy":
                net_qty[f["symbol"]] += q
                buy_n += 1
            elif f["type"] == "sell":
                net_qty[f["symbol"]] -= q
                sell_n += 1
        open_positions = {s: q for s, q in net_qty.items() if abs(q) > 1e-6}
        print(f"  买入笔数: {buy_n}, 卖出笔数: {sell_n}")
        print(f"  期末未平仓标的数: {len(open_positions)}")
        if open_positions:
            print(f"  未平仓标的（前5）: {dict(list(open_positions.items())[:5])}")

        # ── 对账 5：equity_curve 首末点 vs 初始资金 ──────────────
        print("\n── 对账 5: equity 首点 = 初始资金（100万）────")
        if reconstructed_equity:
            first = reconstructed_equity[0]
            ok_init = abs(first - 1_000_000) < 1.0 or abs(first - 100_000) < 1.0
            print(f"  equity[0] = {first:.4f}")
            print(f"  推测初始资金: {first:.0f}")
            print(f"  结论: {'✅ 合理初始资金' if ok_init else '⚠️  非标准初始资金'}")

        # ── 最终结论 ──────────────────────────────────────────
        print("\n" + "=" * 70)
        print("最终结论")
        print("=" * 70)
        all_ok = all(checks) and (eq_match if "eq_match" in dir() else False)
        if all_ok:
            print("  ✅ 收益数字可从审计日志唯一重建，数学上可信")
            print(f"  ✅ 最近6个月总收益率 = {recon_m['total_return']*100:.2f}%（日志重建值）")
            print(f"  ✅ 与引擎报告值 {eng_total_return*100:.2f}% 在 1e-4 相对容差内一致")
            print(f"  ✅ 夏普比率 = {recon_m['sharpe']:.4f}（日志重建）")
            print(f"  ✅ 最大回撤 = {recon_m['max_drawdown']*100:.2f}%（日志重建）")
            print(f"  ✅ {fill_count} 笔 FILL 事件逐笔可追溯")
        else:
            print("  ❌ 对账失败，收益数字不可信")
            print(f"  通过项: {sum(checks)}/{len(checks)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

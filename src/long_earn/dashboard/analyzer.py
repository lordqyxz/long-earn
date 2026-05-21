"""回测审计分析工具

基于 DuckDB 审计日志，提供回测结果分析、风险指标计算、多策略对比等功能。
同时提供可视化所需的结构化 JSON 数据导出接口。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from long_earn.backtest.data.cache import DEFAULT_CACHE_PATH

logger = logging.getLogger(__name__)

# 风险指标计算所需的最小日收益率数据点数
_MIN_DAILY_RETURNS_FOR_RISK = 2


class BacktestAnalyzer:
    """
    回测审计分析工具

    允许 Agent 通过 SQL 查询 DuckDB 审计日志，使用 Polars 进行数据分析，
    并导出可视化所需的结构化 JSON 数据。
    """

    def __init__(self, db_path: Path = DEFAULT_CACHE_PATH) -> None:
        self.db_path = db_path

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.db_path))

    # ── 基础查询接口 ──────────────────────────────────────────────────

    def get_run_summary(self, run_id: str) -> pl.DataFrame:
        """获取特定运行 ID 的审计统计概要"""
        conn = self._get_conn()
        df = conn.execute(
            """
            SELECT event_type, status, COUNT(*) as count
            FROM audit.logs
            WHERE run_id = ?
            GROUP BY event_type, status
            """,
            [run_id],
        ).pl()
        conn.close()
        return df

    def trace_trade_lifecycle(self, trace_id: str) -> pl.DataFrame:
        """还原一个交易的完整因果链条"""
        conn = self._get_conn()
        related_ids: set[str] = {trace_id}

        current_id = trace_id
        while True:
            res = conn.execute(
                "SELECT parent_id FROM audit.logs WHERE trace_id = ? LIMIT 1",
                [current_id],
            ).fetchone()
            if not res or not res[0]:
                break
            current_id = res[0]
            related_ids.add(current_id)

        queue = list(related_ids)
        visited: set[str] = set()
        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            res = conn.execute(
                "SELECT trace_id FROM audit.logs WHERE parent_id = ?",
                [curr],
            ).fetchall()
            for row in res:
                if row[0] not in related_ids:
                    related_ids.add(row[0])
                    queue.append(row[0])

        query = (
            "SELECT * FROM audit.logs WHERE trace_id IN ("
            + ",".join(["?"] * len(related_ids))
            + ") ORDER BY timestamp ASC"
        )
        df = conn.execute(query, list(related_ids)).pl()
        conn.close()
        return df

    def analyze_rejected_events(
        self, run_id: str, event_type: str | None = None
    ) -> pl.DataFrame:
        """分析被拦截或失败的事件"""
        conn = self._get_conn()
        query = "SELECT * FROM audit.logs WHERE run_id = ? AND status != 'SUCCESS'"
        params: list[Any] = [run_id]
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        df = conn.execute(query, params).pl()
        conn.close()
        return df

    def run_custom_query(
        self, query: str, params: list[Any] | None = None
    ) -> pl.DataFrame:
        """允许 Agent 执行自定义 SQL 查询"""
        if params is None:
            params = []
        try:
            conn = self._get_conn()
            df = conn.execute(query, params).pl()
            conn.close()
            return df
        except Exception as e:
            logger.error(f"Custom audit query failed: {e}")
            return pl.DataFrame()

    # ── 增强分析接口 ──────────────────────────────────────────────────

    def get_daily_returns(self, run_id: str) -> pl.DataFrame:
        """从审计日志推导日收益率序列

        从 MARKET_DATA 事件中提取每日最后的 portfolio_value，
        计算日收益率 (r_t = (v_t - v_{t-1}) / v_{t-1})。

        Returns:
            DataFrame 包含 date (Date), portfolio_value (Float64),
            daily_return (Float64)
        """
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT timestamp, payload->>'$.portfolio_value' as value
            FROM audit.logs
            WHERE run_id = ? AND event_type = 'MARKET_DATA'
            ORDER BY timestamp ASC
            """,
            [run_id],
        ).fetchall()
        conn.close()

        if not rows:
            return pl.DataFrame(
                schema={
                    "date": pl.Date,
                    "portfolio_value": pl.Float64,
                    "daily_return": pl.Float64,
                }
            )

        daily_data: list[dict[str, Any]] = []
        for row in rows:
            ts = row[0]
            value = float(row[1]) if row[1] else 0.0
            if isinstance(ts, datetime):
                daily_data.append({"date": ts.date(), "value": value, "ts": ts})
            elif isinstance(ts, str):
                dt = datetime.fromisoformat(ts)
                daily_data.append({"date": dt.date(), "value": value, "ts": dt})
            else:
                daily_data.append({"date": None, "value": value, "ts": None})

        df_raw = pl.DataFrame(daily_data)
        if df_raw.is_empty():
            return pl.DataFrame(
                schema={
                    "date": pl.Date,
                    "portfolio_value": pl.Float64,
                    "daily_return": pl.Float64,
                }
            )

        # 按日期取最后一个快照
        df_daily = (
            df_raw.filter(pl.col("date").is_not_null())
            .sort("ts")
            .group_by("date")
            .agg(pl.col("value").last().alias("portfolio_value"))
            .sort("date")
        )

        # 计算日收益率
        df_daily = df_daily.with_columns(
            (
                (pl.col("portfolio_value") - pl.col("portfolio_value").shift(1))
                / pl.col("portfolio_value").shift(1)
            ).alias("daily_return")
        )

        return df_daily.select(["date", "portfolio_value", "daily_return"])

    def get_risk_metrics(self, run_id: str) -> dict[str, Any]:
        """计算风险指标

        基于日收益率序列计算:
        - 总收益率 (total_return)
        - 年化收益率 (annual_return, 假设 252 交易日)
        - 年化波动率 (annual_volatility)
        - 夏普比率 (sharpe_ratio, 无风险利率假设为 0.02)
        - 最大回撤 (max_drawdown)
        - 最大回撤持续天数 (max_drawdown_duration_days)
        - VaR 95% (var_95)
        - VaR 99% (var_99)
        - CVaR 95% (cvar_95, 条件 VaR / 期望损失)

        Returns:
            包含各项风险指标的字典
        """
        daily = self.get_daily_returns(run_id)
        if (
            daily.is_empty()
            or daily["daily_return"].drop_nulls().len() < _MIN_DAILY_RETURNS_FOR_RISK
        ):
            return {
                "total_return": 0.0,
                "annual_return": 0.0,
                "annual_volatility": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "max_drawdown_duration_days": 0,
                "var_95": 0.0,
                "var_99": 0.0,
                "cvar_95": 0.0,
            }

        returns_series = daily["daily_return"].drop_nulls()
        portfolio_series = daily["portfolio_value"]

        # 总收益率
        start_val = portfolio_series[0]
        end_val = portfolio_series[-1]
        total_return = (
            float((end_val - start_val) / start_val) if start_val > 0 else 0.0
        )

        # 年化收益率
        n_days = daily.height
        annual_return = (
            float((1 + total_return) ** (252 / n_days) - 1) if n_days > 0 else 0.0
        )

        # 年化波动率
        daily_vol = float(returns_series.std())
        annual_volatility = daily_vol * (252**0.5)

        # 夏普比率 (无风险利率 2%)
        risk_free_rate = 0.02
        sharpe_ratio = (
            float((annual_return - risk_free_rate) / annual_volatility)
            if annual_volatility > 0
            else 0.0
        )

        # 最大回撤 & 持续天数
        cumulative_max = portfolio_series.cum_max()
        drawdowns = (portfolio_series - cumulative_max) / cumulative_max
        max_drawdown = float(drawdowns.min())

        # 最大回撤持续天数
        in_drawdown = (drawdowns < 0).cast(pl.Int32)
        in_drawdown_list: list[int] = in_drawdown.to_list()

        max_duration = 0
        current_duration = 0
        for v in in_drawdown_list:
            if v == 1:
                current_duration += 1
                max_duration = max(max_duration, current_duration)
            else:
                current_duration = 0

        # VaR & CVaR (基于日收益率)
        returns_list = returns_series.sort().to_list()
        if returns_list:
            n = len(returns_list)
            var_95 = float(returns_list[max(int(n * 0.05), 0)])
            var_99 = float(returns_list[max(int(n * 0.01), 0)])

            # CVaR 95%: 低于 VaR 95% 的平均值
            tail_95 = [r for r in returns_list if r <= var_95]
            cvar_95 = float(sum(tail_95) / len(tail_95)) if tail_95 else var_95
        else:
            var_95 = 0.0
            var_99 = 0.0
            cvar_95 = 0.0

        return {
            "total_return": round(total_return, 6),
            "annual_return": round(annual_return, 6),
            "annual_volatility": round(annual_volatility, 6),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "max_drawdown": round(max_drawdown, 6),
            "max_drawdown_duration_days": max_duration,
            "var_95": round(var_95, 6),
            "var_99": round(var_99, 6),
            "cvar_95": round(cvar_95, 6),
        }

    def compare_runs(self, run_ids: list[str]) -> pl.DataFrame:
        """多策略对比分析

        对每个 run_id 计算关键绩效指标，返回对比表。

        Args:
            run_ids: 回测运行 ID 列表

        Returns:
            包含每列指标对比结果的 DataFrame
        """
        rows: list[dict[str, Any]] = []
        for rid in run_ids:
            metrics = self.get_risk_metrics(rid)

            # 获取交易统计
            conn = self._get_conn()
            trade_count_row = conn.execute(
                "SELECT COUNT(*) FROM audit.logs "
                "WHERE run_id = ? AND event_type = 'FILL'",
                [rid],
            ).fetchone()
            trade_count = trade_count_row[0] if trade_count_row else 0
            conn.close()

            rows.append(
                {
                    "run_id": rid,
                    "total_return": metrics["total_return"],
                    "annual_return": metrics["annual_return"],
                    "annual_volatility": metrics["annual_volatility"],
                    "sharpe_ratio": metrics["sharpe_ratio"],
                    "max_drawdown": metrics["max_drawdown"],
                    "max_drawdown_duration_days": metrics["max_drawdown_duration_days"],
                    "var_95": metrics["var_95"],
                    "var_99": metrics["var_99"],
                    "cvar_95": metrics["cvar_95"],
                    "trade_count": trade_count,
                }
            )

        return pl.DataFrame(rows)

    # ── 可视化导出接口 ──────────────────────────────────────────────

    def export_equity_curve(self, run_id: str) -> list[dict[str, Any]]:
        """导出权益曲线数据（用于折线图）"""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT timestamp, payload->>'$.portfolio_value' as value
            FROM audit.logs
            WHERE run_id = ? AND event_type = 'MARKET_DATA'
            ORDER BY timestamp ASC
            """,
            [run_id],
        ).fetchall()
        conn.close()

        return [
            {
                "time": str(row[0]) if row[0] else "",
                "value": float(row[1]) if row[1] else 0.0,
            }
            for row in rows
        ]

    def export_trade_journal(self, run_id: str) -> list[dict[str, Any]]:
        """导出完整交易日志（用于表格/交易明细）"""
        conn = self._get_conn()
        fills = conn.execute(
            """
            SELECT timestamp, trace_id, parent_id, payload
            FROM audit.logs
            WHERE run_id = ? AND event_type = 'FILL'
            ORDER BY timestamp ASC
            """,
            [run_id],
        ).fetchall()
        conn.close()

        journal: list[dict[str, Any]] = []
        for row in fills:
            payload: dict[str, Any] = (
                json.loads(row[3]) if isinstance(row[3], str) else (row[3] or {})
            )
            journal.append(
                {
                    "time": str(row[0]) if row[0] else "",
                    "trace_id": row[1],
                    "symbol": payload.get("symbol", ""),
                    "type": payload.get("type", ""),
                    "price": float(payload.get("price", 0)),
                    "quantity": float(payload.get("quantity", 0)),
                    "portfolio_value": float(payload.get("portfolio_value", 0)),
                }
            )
        return journal

    def export_signal_history(self, run_id: str) -> list[dict[str, Any]]:
        """导出信号历史（用于分析策略决策点）"""
        conn = self._get_conn()
        signals = conn.execute(
            """
            SELECT timestamp, payload
            FROM audit.logs
            WHERE run_id = ? AND event_type = 'SIGNAL'
            ORDER BY timestamp ASC
            """,
            [run_id],
        ).fetchall()
        conn.close()

        return [
            {
                "time": str(row[0]) if row[0] else "",
                "signals": (
                    json.loads(row[1]).get("signals", "")
                    if isinstance(row[1], str)
                    else ""
                ),
            }
            for row in signals
        ]

    def export_dashboard_data(self, run_id: str) -> dict[str, Any]:
        """导出仪表盘所需的完整数据集

        包含权益曲线、交易日志、信号历史、事件统计、基准指标和风险指标。
        """
        conn = self._get_conn()

        perf = conn.execute(
            """
            SELECT event_type, COUNT(*) as count
            FROM audit.logs
            WHERE run_id = ?
            GROUP BY event_type
            ORDER BY count DESC
            """,
            [run_id],
        ).fetchall()

        total_events = sum(row[1] for row in perf)
        event_breakdown: dict[str, int] = {row[0]: row[1] for row in perf}

        first_ts = conn.execute(
            "SELECT MIN(timestamp) FROM audit.logs WHERE run_id = ?",
            [run_id],
        ).fetchone()[0]
        last_ts = conn.execute(
            "SELECT MAX(timestamp) FROM audit.logs WHERE run_id = ?",
            [run_id],
        ).fetchone()[0]

        # 尝试从 MARKET_DATA 载荷中提取回测基准指标
        bm_row = conn.execute(
            """
            SELECT payload FROM audit.logs
            WHERE run_id = ? AND event_type = 'MARKET_DATA'
            ORDER BY timestamp DESC LIMIT 1
            """,
            [run_id],
        ).fetchone()
        bm: dict[str, Any] = {}
        if bm_row and isinstance(bm_row[0], str):
            try:
                pl_data = json.loads(bm_row[0])
                if "benchmark" in pl_data:
                    bm = pl_data["benchmark"]
            except json.JSONDecodeError:
                pass

        conn.close()

        # 附加风险指标
        risk = self.get_risk_metrics(run_id)

        return {
            "run_id": run_id,
            "total_events": total_events,
            "event_breakdown": event_breakdown,
            "time_range": {
                "start": str(first_ts) if first_ts else "",
                "end": str(last_ts) if last_ts else "",
            },
            "equity_curve": self.export_equity_curve(run_id),
            "trade_journal": self.export_trade_journal(run_id),
            "signal_history": self.export_signal_history(run_id),
            "benchmark": bm,
            "risk_metrics": risk,
        }

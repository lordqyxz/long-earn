import json
import logging
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from long_earn.backtest.data.cache import DEFAULT_CACHE_PATH

logger = logging.getLogger(__name__)


class BacktestAnalyzer:
    """
    回测审计分析工具

    允许 Agent 通过 SQL 查询 DuckDB 审计日志，使用 Polars 进行数据分析，
    并导出可视化所需的结构化 JSON 数据。
    """

    def __init__(self, db_path: Path = DEFAULT_CACHE_PATH):
        self.db_path = db_path

    def _get_conn(self):
        return duckdb.connect(str(self.db_path))

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
        related_ids = {trace_id}

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
        visited = set()
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
        params = [run_id]
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

        journal = []
        for row in fills:
            payload = json.loads(row[3]) if isinstance(row[3], str) else (row[3] or {})
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
                "signals": json.loads(row[1]).get("signals", "")
                if isinstance(row[1], str)
                else "",
            }
            for row in signals
        ]

    def export_dashboard_data(self, run_id: str) -> dict[str, Any]:
        """导出仪表盘所需的完整数据集"""
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
        event_breakdown = {row[0]: row[1] for row in perf}

        first_ts = conn.execute(
            "SELECT MIN(timestamp) FROM audit.logs WHERE run_id = ?", [run_id]
        ).fetchone()[0]
        last_ts = conn.execute(
            "SELECT MAX(timestamp) FROM audit.logs WHERE run_id = ?", [run_id]
        ).fetchone()[0]

        # 尝试从 MARKET_DATA 载荷中提取回测指标
        bm_row = conn.execute(
            """
            SELECT payload FROM audit.logs
            WHERE run_id = ? AND event_type = 'MARKET_DATA'
            ORDER BY timestamp DESC LIMIT 1
            """,
            [run_id],
        ).fetchone()
        bm = {}
        if bm_row and isinstance(bm_row[0], str):
            try:
                pl = json.loads(bm_row[0])
                # 引擎可以在审计中存入 Alpha/Beta 等信息
                if "benchmark" in pl:
                    bm = pl["benchmark"]
            except json.JSONDecodeError:
                pass

        conn.close()

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
        }

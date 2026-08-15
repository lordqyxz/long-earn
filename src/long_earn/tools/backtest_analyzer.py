"""Agent 工具版回测审计分析器（PostgreSQL）。

允许 Agent 通过 SQL 查询 PostgreSQL 审计日志，使用 Polars 进行数据分析，
并导出可视化所需的结构化 JSON 数据。
"""

import json
from pathlib import Path
from typing import Any

import polars as pl
from loguru import logger

from long_earn.core.pg import pg_connect

_AUDIT_TABLE = '"backtest_audit".logs'


class BacktestAnalyzer:
    """
    回测审计分析工具

    允许 Agent 通过 SQL 查询 PostgreSQL 审计日志，使用 Polars 进行数据分析，
    并导出可视化所需的结构化 JSON 数据。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        # db_path 参数保留仅为兼容旧签名（PG 时代连接参数由 core.pg 统一裁决）
        del db_path

    def _get_conn(self) -> Any:
        # 元组行：export_* 系列方法用 row[N] 下标访问，保持 DuckDB 时代
        # fetchall 返回元组的行为一致（PG jsonb 已由 psycopg 反序列化为 dict）
        return pg_connect(read_only=True, row_factory=None)

    @staticmethod
    def _rows_to_pl(conn: Any, query: str, params: list[Any]) -> pl.DataFrame:
        cur = conn.execute(query, params)
        if cur.description is None:
            return pl.DataFrame()
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
        return pl.DataFrame(rows, schema=cols, orient="row")

    def get_run_summary(self, run_id: str) -> pl.DataFrame:
        """获取特定运行 ID 的审计统计概要"""
        conn = self._get_conn()
        try:
            return self._rows_to_pl(
                conn,
                f"""
                SELECT event_type, status, COUNT(*) as count
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s
                GROUP BY event_type, status
                """,
                [run_id],
            )
        finally:
            conn.close()

    def trace_trade_lifecycle(self, trace_id: str) -> pl.DataFrame:
        """还原一个交易的完整因果链条"""
        conn = self._get_conn()
        try:
            related_ids = {trace_id}

            current_id = trace_id
            while True:
                res = conn.execute(
                    f"SELECT parent_id FROM {_AUDIT_TABLE} "
                    "WHERE trace_id = %s LIMIT 1",
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
                    f"SELECT trace_id FROM {_AUDIT_TABLE} WHERE parent_id = %s",
                    [curr],
                ).fetchall()
                for row in res:
                    if row[0] not in related_ids:
                        related_ids.add(row[0])
                        queue.append(row[0])

            query = (
                f"SELECT * FROM {_AUDIT_TABLE} WHERE trace_id IN ("
                + ",".join(["%s"] * len(related_ids))
                + ") ORDER BY timestamp ASC"
            )
            return self._rows_to_pl(conn, query, list(related_ids))
        finally:
            conn.close()

    def analyze_rejected_events(
        self, run_id: str, event_type: str | None = None
    ) -> pl.DataFrame:
        """分析被拦截或失败的事件"""
        conn = self._get_conn()
        try:
            query = (
                f"SELECT * FROM {_AUDIT_TABLE} "
                "WHERE run_id = %s AND status != 'SUCCESS'"
            )
            params = [run_id]
            if event_type:
                query += " AND event_type = %s"
                params.append(event_type)

            return self._rows_to_pl(conn, query, params)
        finally:
            conn.close()

    def run_custom_query(
        self, query: str, params: list[Any] | None = None
    ) -> pl.DataFrame:
        """允许 Agent 执行自定义 SQL 查询（PostgreSQL 方言，参数占位符 %s）"""
        if params is None:
            params = []
        try:
            conn = self._get_conn()
            try:
                return self._rows_to_pl(conn, query, params)
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Custom audit query failed: {e}")
            return pl.DataFrame()

    # ── 可视化导出接口 ──────────────────────────────────────────────

    def export_equity_curve(self, run_id: str) -> list[dict[str, Any]]:
        """导出权益曲线数据（用于折线图）"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT timestamp, payload->>'portfolio_value' as value
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'MARKET_DATA'
                ORDER BY timestamp ASC
                """,
                [run_id],
            ).fetchall()
        finally:
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
        try:
            fills = conn.execute(
                f"""
                SELECT timestamp, trace_id, parent_id, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'FILL'
                ORDER BY timestamp ASC
                """,
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        journal = []
        for row in fills:
            payload = (
                json.loads(row[3])
                if isinstance(row[3], str)
                else (row[3] or {})
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
        try:
            signals = conn.execute(
                f"""
                SELECT timestamp, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'SIGNAL'
                ORDER BY timestamp ASC
                """,
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        return [
            {
                "time": str(row[0]) if row[0] else "",
                "signals": (
                    row[1].get("signals", "")
                    if isinstance(row[1], dict)
                    else (
                        json.loads(row[1]).get("signals", "")
                        if isinstance(row[1], str)
                        else ""
                    )
                ),
            }
            for row in signals
        ]

    def export_dashboard_data(self, run_id: str) -> dict[str, Any]:
        """导出仪表盘所需的完整数据集"""
        conn = self._get_conn()
        try:
            perf = conn.execute(
                f"""
                SELECT event_type, COUNT(*) as count
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s
                GROUP BY event_type
                ORDER BY count DESC
                """,
                [run_id],
            ).fetchall()

            total_events = sum(row[1] for row in perf)
            event_breakdown = {row[0]: row[1] for row in perf}

            first_ts = conn.execute(
                f"SELECT MIN(timestamp) FROM {_AUDIT_TABLE} WHERE run_id = %s",
                [run_id],
            ).fetchone()[0]
            last_ts = conn.execute(
                f"SELECT MAX(timestamp) FROM {_AUDIT_TABLE} WHERE run_id = %s",
                [run_id],
            ).fetchone()[0]

            bm_row = conn.execute(
                f"""
                SELECT payload FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'MARKET_DATA'
                ORDER BY timestamp DESC LIMIT 1
                """,
                [run_id],
            ).fetchone()
        finally:
            conn.close()

        bm = {}
        if bm_row:
            payload = bm_row[0] if isinstance(bm_row, tuple) else bm_row.get("payload")
            if isinstance(payload, str):
                try:
                    pl_data = json.loads(payload)
                    if "benchmark" in pl_data:
                        bm = pl_data["benchmark"]
                except json.JSONDecodeError:
                    pass
            elif isinstance(payload, dict) and "benchmark" in payload:
                bm = payload["benchmark"]

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

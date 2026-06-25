"""回测可视化 API 服务

提供 RESTful API 端点，供 Web 可视化仪表盘消费审计数据。
使用标准库 http.server，无额外依赖。

可用端点：
  GET  /api/runs                    — 列出所有回测运行
  GET  /api/runs/{run_id}/summary   — 运行摘要
  GET  /api/runs/{run_id}/equity    — 权益曲线数据
  GET  /api/runs/{run_id}/trades    — 交易日志
  GET  /api/runs/{run_id}/signals   — 信号历史
  GET  /api/runs/{run_id}/dashboard — 完整仪表盘数据
  GET  /api/runs/{run_id}/risk      — 风险指标
  GET  /api/runs/{run_id}/daily_returns — 日收益率序列
  POST /api/compare                 — 多策略对比
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from long_earn.dashboard.analyzer import BacktestAnalyzer

_HERE = Path(__file__).parent
_DASHBOARD_HTML = _HERE / "templates" / "dashboard.html"


def _html_response(
    handler: BaseHTTPRequestHandler, html: str, status: int = 200
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(html.encode("utf-8"))


def _json_response(
    handler: BaseHTTPRequestHandler, data: Any, status: int = 200
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(
        json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    )


class VisualizationServer(BaseHTTPRequestHandler):
    """HTTP 请求处理器（可视化 API 服务）

    处理仪表盘页面和 REST API 请求。
    """

    analyzer: BacktestAnalyzer | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in {"/", ""}:
            self._serve_dashboard()
        elif path == "/api/runs":
            self._list_runs()
        elif path.startswith("/api/runs/") and path.endswith("/summary"):
            run_id = path.split("/")[3]
            self._run_summary(run_id)
        elif path.startswith("/api/runs/") and path.endswith("/equity"):
            run_id = path.split("/")[3]
            self._run_equity(run_id)
        elif path.startswith("/api/runs/") and path.endswith("/trades"):
            run_id = path.split("/")[3]
            self._run_trades(run_id)
        elif path.startswith("/api/runs/") and path.endswith("/signals"):
            run_id = path.split("/")[3]
            self._run_signals(run_id)
        elif path.startswith("/api/runs/") and path.endswith("/attribution"):
            run_id = path.split("/")[3]
            self._run_attribution(run_id)
        elif path.startswith("/api/runs/") and path.endswith("/dashboard"):
            run_id = path.split("/")[3]
            self._run_dashboard(run_id)
        elif path.startswith("/api/runs/") and path.endswith("/risk"):
            run_id = path.split("/")[3]
            self._run_risk(run_id)
        elif path.startswith("/api/runs/") and path.endswith("/daily_returns"):
            run_id = path.split("/")[3]
            self._run_daily_returns(run_id)
        elif path == "/api/health":
            self._health()
        else:
            _json_response(self, {"error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/compare":
            self._compare_runs()
        else:
            _json_response(self, {"error": "Not found"}, 404)

    def do_OPTIONS(self) -> None:
        """处理 CORS 预检请求"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        logger.info(f"{self.client_address[0]} - {format % args}")

    def _get_analyzer(self) -> BacktestAnalyzer:
        if VisualizationServer.analyzer is None:
            VisualizationServer.analyzer = BacktestAnalyzer()
        return VisualizationServer.analyzer

    # ── API 端点 ──────────────────────────────────────────────────────

    def _health(self) -> None:
        _json_response(self, {"status": "ok"})

    def _list_runs(self) -> None:
        """列出所有回测运行"""
        analyzer = self._get_analyzer()
        df = analyzer.run_custom_query(
            "SELECT DISTINCT run_id, MIN(timestamp) as started "
            "FROM audit.logs GROUP BY run_id ORDER BY started DESC"
        )
        if df.is_empty():
            _json_response(self, {"runs": []})
            return
        runs = [
            {"run_id": row["run_id"], "started": str(row["started"])}
            for row in df.iter_rows(named=True)
        ]
        _json_response(self, {"runs": runs})

    def _run_summary(self, run_id: str) -> None:
        analyzer = self._get_analyzer()
        summary = analyzer.get_run_summary(run_id)
        if summary.is_empty():
            _json_response(self, {"error": "Run not found"}, 404)
            return
        rows = [
            {
                "event_type": row["event_type"],
                "status": row["status"],
                "count": row["count"],
            }
            for row in summary.iter_rows(named=True)
        ]
        _json_response(self, {"run_id": run_id, "summary": rows})

    def _run_equity(self, run_id: str) -> None:
        analyzer = self._get_analyzer()
        curve = analyzer.export_equity_curve(run_id)
        _json_response(self, {"run_id": run_id, "equity_curve": curve})

    def _run_trades(self, run_id: str) -> None:
        analyzer = self._get_analyzer()
        journal = analyzer.export_trade_journal(run_id)
        _json_response(self, {"run_id": run_id, "trades": journal})

    def _run_signals(self, run_id: str) -> None:
        analyzer = self._get_analyzer()
        signals = analyzer.export_signal_history(run_id)
        _json_response(self, {"run_id": run_id, "signals": signals})

    def _run_risk(self, run_id: str) -> None:
        """风险指标端点"""
        analyzer = self._get_analyzer()
        risk = analyzer.get_risk_metrics(run_id)
        _json_response(self, {"run_id": run_id, "risk_metrics": risk})

    def _run_daily_returns(self, run_id: str) -> None:
        """日收益率序列端点"""
        analyzer = self._get_analyzer()
        daily = analyzer.get_daily_returns(run_id)
        if daily.is_empty():
            _json_response(self, {"run_id": run_id, "daily_returns": []})
            return
        returns_list = daily.select(["date", "daily_return"]).to_dicts()
        _json_response(self, {"run_id": run_id, "daily_returns": returns_list})

    def _serve_dashboard(self) -> None:
        if _DASHBOARD_HTML.exists():
            _html_response(self, _DASHBOARD_HTML.read_text(encoding="utf-8"))
        else:
            _html_response(self, "<h1>Dashboard not found</h1>", 404)

    def _run_attribution(self, run_id: str) -> None:
        analyzer = self._get_analyzer()
        data = analyzer.export_dashboard_data(run_id)
        _json_response(
            self,
            {
                "run_id": run_id,
                "equity_curve": data.get("equity_curve", []),
                "benchmark": data.get("benchmark", {}),
            },
        )

    def _run_dashboard(self, run_id: str) -> None:
        analyzer = self._get_analyzer()
        data = analyzer.export_dashboard_data(run_id)
        if not data.get("equity_curve"):
            _json_response(self, {"error": "Run not found"}, 404)
            return
        _json_response(self, data)

    def _compare_runs(self) -> None:
        """多策略对比（POST）"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            req = json.loads(body)
            run_ids: list[str] = req.get("run_ids", [])
        except json.JSONDecodeError:
            _json_response(self, {"error": "Invalid JSON"}, 400)
            return

        if not run_ids:
            _json_response(self, {"error": "run_ids is required"}, 400)
            return

        analyzer = self._get_analyzer()
        comparison = analyzer.compare_runs(run_ids)
        _json_response(
            self,
            {
                "comparison": comparison.to_dicts(),
            },
        )


def serve_visualization(
    host: str = "0.0.0.0",
    port: int = 8090,
    db_path: str | Path = "",
) -> None:
    """启动回测可视化 API 服务

    Args:
        host: 监听地址
        port: 监听端口
        db_path: DuckDB 审计数据库路径
    """
    if db_path:
        VisualizationServer.analyzer = BacktestAnalyzer(Path(db_path))

    server = HTTPServer((host, port), VisualizationServer)
    logger.info(f"回测可视化 API 服务启动: http://{host}:{port}")
    logger.info("  API 文档:")
    logger.info("    GET /api/health")
    logger.info("    GET /api/runs")
    logger.info("    GET /api/runs/{run_id}/dashboard")
    logger.info("    GET /api/runs/{run_id}/equity")
    logger.info("    GET /api/runs/{run_id}/trades")
    logger.info("    GET /api/runs/{run_id}/signals")
    logger.info("    GET /api/runs/{run_id}/risk")
    logger.info("    POST /api/compare")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("服务停止")
        server.server_close()


# 向后兼容别名：旧代码中使用 BacktestAPIHandler 的地方仍可正常工作
BacktestAPIHandler = VisualizationServer

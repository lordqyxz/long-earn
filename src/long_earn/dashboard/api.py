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
import shutil
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from long_earn.dashboard.analyzer import BacktestAnalyzer

_HERE = Path(__file__).parent
_DASHBOARD_HTML = _HERE / "templates" / "dashboard.html"

# URL 路径分段索引：/api/runs/{run_id}/symbol/{symbol}/chart
# split('/') 后为 ['', 'api', 'runs', run_id(3), 'symbol', symbol(5), 'chart']
_RUN_ID_INDEX = 3
_SYMBOL_INDEX = 5


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


def _file_response(
    handler: BaseHTTPRequestHandler, file_path: Path, content_type: str, filename: str
) -> None:
    """以文件下载方式响应（带 Content-Disposition 触发浏览器下载）"""
    data = file_path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header(
        "Content-Disposition", f'attachment; filename="{filename}"'
    )
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class VisualizationServer(BaseHTTPRequestHandler):
    """HTTP 请求处理器（可视化 API 服务）

    处理仪表盘页面和 REST API 请求。
    """

    analyzer: BacktestAnalyzer | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parsed.query

        if path in {"/", ""}:
            self._serve_dashboard()
            return
        if path == "/api/health":
            self._health()
            return
        if path == "/api/runs":
            self._list_runs()
            return

        # /api/runs/{run_id}/<suffix> 路由
        if path.startswith("/api/runs/"):
            self._route_run_endpoint(path, query)
            return

        _json_response(self, {"error": "Not found"}, 404)

    def _route_run_endpoint(self, path: str, query: str) -> None:
        """路由 /api/runs/{run_id}/<suffix> 形式的端点"""
        parts = path.split("/")
        # parts: ['', 'api', 'runs', '<run_id>', ...]
        run_id = parts[3] if len(parts) > _RUN_ID_INDEX else ""

        # 简单后缀路由表：suffix -> handler（handler 接收 run_id）
        suffix_routes: dict[str, Any] = {
            "/summary": self._run_summary,
            "/equity": self._run_equity,
            "/trades": self._run_trades,
            "/signals": self._run_signals,
            "/attribution": self._run_attribution,
            "/dashboard": self._run_dashboard,
            "/risk": self._run_risk,
            "/daily_returns": self._run_daily_returns,
            "/symbols": self._traded_symbols,
            "/symbol_charts": self._all_symbol_charts,
        }
        for suffix, handler in suffix_routes.items():
            if path.endswith(suffix):
                handler(run_id)
                return

        # 导出端点需要额外 query 参数
        if path.endswith("/export"):
            self._export_trades(run_id, query)
            return

        # 个股图表：/api/runs/{run_id}/symbol/{symbol}/chart
        if "/symbol/" in path and path.endswith("/chart"):
            symbol = parts[_SYMBOL_INDEX] if len(parts) > _SYMBOL_INDEX else ""
            self._symbol_chart(run_id, symbol)
            return

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
            "FROM backtest_audit.logs GROUP BY run_id ORDER BY started DESC"
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

    # ── 交易导出与个股图表端点 ───────────────────────────────────────

    def _traded_symbols(self, run_id: str) -> None:
        """返回本次回测中所有被交易过的标的代码列表"""
        analyzer = self._get_analyzer()
        symbols = analyzer.get_traded_symbols(run_id)
        _json_response(self, {"run_id": run_id, "symbols": symbols})

    def _export_trades(self, run_id: str, query: str) -> None:
        """导出交易日志为 CSV 或 JSON 文件下载

        查询参数 format=csv（默认）| json
        """
        params = parse_qs(query)
        fmt = (params.get("format", ["csv"])[0]).lower()
        if fmt not in {"csv", "json"}:
            _json_response(self, {"error": "format 仅支持 csv / json"}, 400)
            return

        analyzer = self._get_analyzer()
        try:
            tmp_dir = Path(tempfile.mkdtemp())
            base_name = f"trades_{run_id[:8]}"
            out_path = analyzer.export_trade_traces_to_file(
                run_id, tmp_dir / base_name, fmt=fmt
            )
            content_type = (
                "text/csv; charset=utf-8"
                if fmt == "csv"
                else "application/json; charset=utf-8"
            )
            _file_response(self, out_path, content_type, out_path.name)
        except Exception as e:
            logger.exception("导出交易日志失败")
            _json_response(self, {"error": str(e)}, 500)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _symbol_chart(self, run_id: str, symbol: str) -> None:
        """返回单只标的的价格走势 + 买卖点标注数据"""
        analyzer = self._get_analyzer()
        data = analyzer.export_symbol_chart_data(run_id, symbol)
        _json_response(self, data)

    def _all_symbol_charts(self, run_id: str) -> None:
        """返回本次回测中所有交易标的的价格走势 + 买卖点标注数据"""
        analyzer = self._get_analyzer()
        charts = analyzer.export_all_symbol_charts(run_id)
        _json_response(self, {"run_id": run_id, "symbols": len(charts), "charts": charts})


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
    logger.info("    GET /api/runs/{run_id}/symbols          交易标的列表")
    logger.info("    GET /api/runs/{run_id}/export?format=csv  导出交易日志(CSV)")
    logger.info("    GET /api/runs/{run_id}/symbol/{symbol}/chart  个股价格+买卖点")
    logger.info("    GET /api/runs/{run_id}/symbol_charts      全部标的图表数据")
    logger.info("    POST /api/compare")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("服务停止")
        server.server_close()


# 向后兼容别名：旧代码中使用 BacktestAPIHandler 的地方仍可正常工作
BacktestAPIHandler = VisualizationServer


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="回测可视化 API 服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8090, help="监听端口")
    parser.add_argument("--db", default="", help="DuckDB 审计数据库路径")
    args = parser.parse_args()

    serve_visualization(host=args.host, port=args.port, db_path=args.db)

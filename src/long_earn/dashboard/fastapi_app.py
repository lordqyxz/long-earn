"""FastAPI 可视化 API 服务

基于 FastAPI + Uvicorn 替代 stdlib http.server，新增 WebSocket 实时事件流推送
和事件推理管线触发功能。

可用端点：
  REST（向后兼容旧 API）：
    GET  /api/runs                    — 列出所有回测运行
    GET  /api/runs/{run_id}/summary   — 运行摘要
    GET  /api/runs/{run_id}/equity    — 权益曲线
    GET  /api/runs/{run_id}/trades    — 交易日志
    GET  /api/runs/{run_id}/signals   — 信号历史
    GET  /api/runs/{run_id}/dashboard — 完整仪表盘数据
    GET  /api/runs/{run_id}/risk      — 风险指标
    GET  /api/runs/{run_id}/daily_returns — 日收益率序列
    GET  /api/runs/{run_id}/symbols   — 交易标的列表
    GET  /api/runs/{run_id}/symbol_charts — 全部标的图表
    GET  /api/runs/{run_id}/symbol/{symbol}/chart — 个股图表
    GET  /api/runs/{run_id}/export?format=csv|json — 导出交易日志
    POST /api/compare                 — 多策略对比

  事件流：
    GET  /api/events                  — 事件列表
    GET  /api/events/stats            — 事件统计
    GET  /api/events/timeline         — 事件时间线
    GET  /api/events/relations        — 影响关系列表
    GET  /api/events/{sid}            — 事件详情 + 关联关系
    POST /api/events/trigger          — 触发事件推理管线

  WebSocket：
    WS   /ws/events                   — 实时事件流推送 + 管线进度

  事件流可视化：
    GET  /event-flow                  — 事件流可视化页面
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from long_earn.dashboard.analyzer import BacktestAnalyzer
from long_earn.dashboard.event_analyzer import EventAnalyzer

_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_DASHBOARD_HTML = _TEMPLATES_DIR / "dashboard.html"
_EVENT_FLOW_HTML = _TEMPLATES_DIR / "event_flow.html"

_STAGES = ["collect", "extract", "propagate", "conflict", "save"]


def _resolve_paths(
    db_path: str | Path, substances_path: str | Path
) -> tuple[Path, Path]:
    """解析数据库路径。"""
    from long_earn.config import AppConfig  # noqa: PLC0415

    cfg = AppConfig.from_env()
    resolved_db = Path(db_path) if db_path else Path(cfg.backtest_cache_path)
    resolved_substances = (
        Path(substances_path) if substances_path else Path(cfg.memory_path)
    )
    return resolved_db, resolved_substances


def _register_page_routes(app: FastAPI) -> None:
    """注册页面路由。"""

    @app.get("/", response_class=HTMLResponse)
    async def index():
        if _DASHBOARD_HTML.exists():
            return HTMLResponse(_DASHBOARD_HTML.read_text(encoding="utf-8"))
        raise HTTPException(404, "Dashboard not found")

    @app.get("/event-flow", response_class=HTMLResponse)
    async def event_flow_page():
        if _EVENT_FLOW_HTML.exists():
            return HTMLResponse(_EVENT_FLOW_HTML.read_text(encoding="utf-8"))
        raise HTTPException(404, "Event flow page not found")


def _register_run_routes(
    app: FastAPI, analyzer: BacktestAnalyzer
) -> None:
    """注册回测运行查询端点。"""

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/runs")
    async def list_runs():
        df = analyzer.run_custom_query(
            "SELECT DISTINCT run_id, MIN(timestamp) as started "
            "FROM backtest_audit.logs GROUP BY run_id ORDER BY started DESC"
        )
        if df.is_empty():
            return {"runs": []}
        runs = [
            {"run_id": row["run_id"], "started": str(row["started"])}
            for row in df.iter_rows(named=True)
        ]
        return {"runs": runs}

    @app.get("/api/runs/{run_id}/summary")
    async def run_summary(run_id: str):
        summary = analyzer.get_run_summary(run_id)
        if summary.is_empty():
            raise HTTPException(404, "Run not found")
        rows = [
            {
                "event_type": r["event_type"],
                "status": r["status"],
                "count": r["count"],
            }
            for r in summary.iter_rows(named=True)
        ]
        return {"run_id": run_id, "summary": rows}

    @app.get("/api/runs/{run_id}/equity")
    async def run_equity(run_id: str):
        curve = analyzer.export_equity_curve(run_id)
        return {"run_id": run_id, "equity_curve": curve}

    @app.get("/api/runs/{run_id}/trades")
    async def run_trades(run_id: str):
        journal = analyzer.export_trade_journal(run_id)
        return {"run_id": run_id, "trades": journal}

    @app.get("/api/runs/{run_id}/signals")
    async def run_signals(run_id: str):
        signals = analyzer.export_signal_history(run_id)
        return {"run_id": run_id, "signals": signals}

    @app.get("/api/runs/{run_id}/dashboard")
    async def run_dashboard(run_id: str):
        data = analyzer.export_dashboard_data(run_id)
        if not data.get("equity_curve"):
            raise HTTPException(404, "Run not found")
        return data

    @app.get("/api/runs/{run_id}/risk")
    async def run_risk(run_id: str):
        risk = analyzer.get_risk_metrics(run_id)
        return {"run_id": run_id, "risk_metrics": risk}

    @app.get("/api/runs/{run_id}/daily_returns")
    async def run_daily_returns(run_id: str):
        daily = analyzer.get_daily_returns(run_id)
        if daily.is_empty():
            return {"run_id": run_id, "daily_returns": []}
        returns_list = daily.select(["date", "daily_return"]).to_dicts()
        return {"run_id": run_id, "daily_returns": returns_list}

    @app.get("/api/runs/{run_id}/attribution")
    async def run_attribution(run_id: str):
        data = analyzer.export_dashboard_data(run_id)
        return {
            "run_id": run_id,
            "equity_curve": data.get("equity_curve", []),
            "benchmark": data.get("benchmark", {}),
        }


def _register_chart_export_routes(
    app: FastAPI, analyzer: BacktestAnalyzer
) -> None:
    """注册图表和导出端点。"""

    @app.get("/api/runs/{run_id}/symbols")
    async def traded_symbols(run_id: str):
        symbols = analyzer.get_traded_symbols(run_id)
        return {"run_id": run_id, "symbols": symbols}

    @app.get("/api/runs/{run_id}/symbol_charts")
    async def all_symbol_charts(run_id: str):
        charts = analyzer.export_all_symbol_charts(run_id)
        return {"run_id": run_id, "symbols": len(charts), "charts": charts}

    @app.get("/api/runs/{run_id}/symbol/{symbol}/chart")
    async def symbol_chart(run_id: str, symbol: str):
        data = analyzer.export_symbol_chart_data(run_id, symbol)
        return data

    @app.get("/api/runs/{run_id}/export")
    async def export_trades(run_id: str, format: str = Query("csv")):
        if format not in {"csv", "json"}:
            raise HTTPException(400, "format 仅支持 csv / json")
        try:
            tmp_dir = Path(tempfile.mkdtemp())
            base_name = f"trades_{run_id[:8]}"
            out_path = analyzer.export_trade_traces_to_file(
                run_id, tmp_dir / base_name, fmt=format
            )
            media_type = (
                "text/csv; charset=utf-8"
                if format == "csv"
                else "application/json; charset=utf-8"
            )
            return FileResponse(
                out_path, media_type=media_type, filename=out_path.name
            )
        except Exception as e:
            logger.exception("导出交易日志失败")
            raise HTTPException(500, str(e)) from e
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @app.post("/api/compare")
    async def compare_runs(req: dict[str, Any]):
        run_ids: list[str] = req.get("run_ids", [])
        if not run_ids:
            raise HTTPException(400, "run_ids is required")
        comparison = analyzer.compare_runs(run_ids)
        return {"comparison": comparison.to_dicts()}


def _register_event_routes(
    app: FastAPI, event_analyzer: EventAnalyzer
) -> None:
    """注册事件流 REST 端点。"""

    @app.get("/api/events")
    async def list_events(
        limit: int = Query(50),
        symbol: str | None = Query(None),
        sentiment: str | None = Query(None),
        category: str | None = Query(None),
    ):
        events = event_analyzer.list_events(
            limit=limit, symbol=symbol, sentiment=sentiment, category=category
        )
        return {"count": len(events), "events": events}

    @app.get("/api/events/stats")
    async def event_stats():
        return event_analyzer.event_stats()

    @app.get("/api/events/timeline")
    async def event_timeline(days: int = Query(30)):
        timeline = event_analyzer.event_timeline(days=days)
        return {"timeline": timeline}

    @app.get("/api/events/relations")
    async def list_relations(
        limit: int = Query(50),
        target: str | None = Query(None),
        direction: str | None = Query(None),
    ):
        relations = event_analyzer.list_relations(
            limit=limit, target=target, direction=direction
        )
        return {"count": len(relations), "relations": relations}

    @app.get("/api/events/{sid}")
    async def get_event(sid: str):
        if not sid:
            raise HTTPException(400, "sid is required")
        event = event_analyzer.get_event(sid)
        if event is None:
            raise HTTPException(404, "Event not found")
        return event

    @app.post("/api/events/trigger")
    async def trigger_event_inference(req: dict[str, Any]):
        """触发事件推理管线。"""
        query = req.get("query", "")
        if not query:
            raise HTTPException(400, "query is required")
        # 在后台任务中运行管线，通过 WebSocket 广播进度
        asyncio.create_task(  # noqa: RUF006
            _run_pipeline_and_broadcast(
                query, event_analyzer, app.state.active_ws
            )
        )
        return {"task_id": query[:20], "status": "started"}


def _register_ws_routes(
    app: FastAPI, event_analyzer: EventAnalyzer
) -> None:
    """注册 WebSocket 事件流端点。"""

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket):
        await websocket.accept()
        active_ws: set[WebSocket] = app.state.active_ws
        active_ws.add(websocket)
        logger.info(f"WebSocket 客户端已连接 (活跃: {len(active_ws)})")
        try:
            while True:
                msg = await websocket.receive_json()
                action = msg.get("action", "")

                if action == "ping":
                    await websocket.send_json({"type": "pong"})

                elif action == "trigger":
                    query = msg.get("query", "")
                    if query:
                        await websocket.send_json(
                            {"type": "pipeline_start", "query": query}
                        )
                        _ = asyncio.create_task(  # noqa: RUF006
                            _run_pipeline_and_broadcast(
                                query, event_analyzer, active_ws
                            )
                        )

                elif action == "subscribe":
                    await websocket.send_json(
                        {"type": "subscribed", "message": "已订阅事件流"}
                    )

                elif action == "reload":
                    from long_earn.config import AppConfig  # noqa: PLC0415

                    cfg = AppConfig.from_env()
                    sp = Path(cfg.memory_path)
                    if sp.exists():
                        event_analyzer.load(sp)
                        await websocket.send_json(
                            {
                                "type": "reloaded",
                                "count": event_analyzer.store.count
                                if event_analyzer.is_ready
                                else 0,
                            }
                        )

        except WebSocketDisconnect:
            logger.info("WebSocket 客户端已断开")
        except Exception:
            logger.exception("WebSocket 错误")
        finally:
            active_ws.discard(websocket)


async def _broadcast_event(
    active_ws: set[WebSocket], data: dict[str, Any]
) -> None:
    """向所有活跃 WebSocket 客户端广播事件。"""
    disconnected: set[WebSocket] = set()
    for ws in active_ws:
        try:
            await ws.send_json(data)
        except Exception:
            disconnected.add(ws)
    active_ws.difference_update(disconnected)


async def _run_pipeline_and_broadcast(
    query: str,
    ea: EventAnalyzer,
    active_ws: set[WebSocket],
) -> None:
    """后台运行事件推理管线，通过 WebSocket 广播进度。"""
    try:
        await _broadcast_event(
            active_ws,
            {
                "type": "pipeline_progress",
                "query": query,
                "stage": "collect",
                "progress": 0,
                "status": "running",
                "detail": "正在采集原始素材...",
            },
        )

        from long_earn.config import create_runtime_context  # noqa: PLC0415

        ctx = create_runtime_context()

        for i, stage in enumerate(_STAGES):
            await asyncio.sleep(0.5)
            progress = int((i + 1) / len(_STAGES) * 100)
            await _broadcast_event(
                active_ws,
                {
                    "type": "pipeline_progress",
                    "query": query,
                    "stage": stage,
                    "progress": progress,
                    "status": "running",
                    "detail": f"阶段 {stage} 完成...",
                },
            )

        ctx.prepare_context(query, force_refresh=True)

        from long_earn.config import AppConfig  # noqa: PLC0415

        cfg = AppConfig.from_env()
        sp = Path(cfg.memory_path)
        if sp.exists():
            ea.load(sp)

        stats = ea.event_stats()
        await _broadcast_event(
            active_ws,
            {
                "type": "pipeline_complete",
                "query": query,
                "progress": 100,
                "status": "completed",
                "stats": stats,
                "detail": (
                    f"事件推理完成：{stats['total_events']} 个事件，"
                    f"{stats['total_relations']} 条关系"
                ),
            },
        )

    except Exception as e:
        logger.exception("事件推理管线失败")
        await _broadcast_event(
            active_ws,
            {
                "type": "pipeline_error",
                "query": query,
                "status": "failed",
                "detail": str(e),
            },
        )


def _create_app(
    db_path: str | Path = "",
    substances_path: str | Path = "",
) -> FastAPI:
    """创建 FastAPI 应用实例。"""
    app = FastAPI(title="Long Earn 可视化仪表盘", version="2.0.0")
    app.state.active_ws = set()  # type: ignore[attr-defined]

    resolved_db, resolved_substances = _resolve_paths(db_path, substances_path)

    analyzer = BacktestAnalyzer()
    if resolved_db.exists():
        analyzer = BacktestAnalyzer(resolved_db)
        logger.info(f"审计数据库已连接: {resolved_db}")

    event_analyzer = EventAnalyzer()
    if resolved_substances.exists():
        if event_analyzer.load(resolved_substances):
            logger.info(f"事件物质已加载: {resolved_substances}")
        else:
            logger.warning(f"事件物质加载失败: {resolved_substances}")

    app.mount(
        "/static",
        StaticFiles(directory=str(_TEMPLATES_DIR)),
        name="static",
    )

    _register_page_routes(app)
    _register_run_routes(app, analyzer)
    _register_chart_export_routes(app, analyzer)
    _register_event_routes(app, event_analyzer)
    _register_ws_routes(app, event_analyzer)

    return app


def serve_visualization_fastapi(
    host: str = "0.0.0.0",
    port: int = 8090,
    db_path: str | Path = "",
    substances_path: str | Path = "",
    reload: bool = False,
) -> None:
    """启动 FastAPI 可视化服务。

    Args:
        host: 监听地址
        port: 监听端口
        db_path: DuckDB 审计数据库路径；空字符串时取 AppConfig.backtest_cache_path
        substances_path: SubstanceStore DuckDB 路径；空字符串时取 AppConfig.memory_path
        reload: 是否启用热重载（开发模式）
    """
    app = _create_app(db_path=db_path, substances_path=substances_path)

    logger.info(f"FastAPI 可视化服务启动: http://{host}:{port}")
    logger.info("  页面:")
    logger.info("    GET /                  回测可视化仪表盘")
    logger.info("    GET /event-flow         事件流可视化")
    logger.info("  WebSocket:")
    logger.info("    WS  /ws/events          实时事件流推送")
    logger.info("  API:")
    logger.info("    GET /api/health")
    logger.info("    GET /api/runs")
    logger.info("    GET /api/runs/{run_id}/dashboard")
    logger.info("    POST /api/events/trigger  触发事件推理管线")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        reload=reload,
    )

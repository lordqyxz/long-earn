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
import contextlib
import ipaddress
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger

from long_earn.app import schemas
from long_earn.app.analyzer import BacktestAnalyzer
from long_earn.app.event_analyzer import EventAnalyzer

_HERE = Path(__file__).parent
# 前端生产构建产物目录
_WEB_DIST = _HERE.parent.parent.parent / "web" / "dist"

_STAGES = ["collect", "extract", "propagate", "conflict", "save"]


def _is_loopback_host(host: str) -> bool:
    """Return whether a bind host is restricted to this machine."""
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_bind_host(host: str, allow_remote: bool) -> None:
    """Fail closed unless an externally reachable bind was explicitly allowed."""
    if not _is_loopback_host(host) and not allow_remote:
        msg = (
            f"Refusing to bind visualization server to non-loopback host {host!r}. "
            "Use allow_remote=True (CLI: --allow-remote) only behind appropriate "
            "network controls and authentication."
        )
        raise ValueError(msg)


def _origin_matches_host(origin: str, host: str) -> bool:
    """Check a browser Origin against the HTTP Host header, ignoring the port."""
    origin_hostname = urlparse(origin).hostname
    request_hostname = urlparse(f"//{host}").hostname
    if origin_hostname is None or request_hostname is None:
        return False
    return origin_hostname.lower() == request_hostname.lower()


def _websocket_origin_allowed(websocket: WebSocket) -> bool:
    """Require same-origin browser WebSocket connections in remote mode."""
    if not websocket.app.state.remote_mode:
        return True
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    return origin is not None and _origin_matches_host(origin, host)


def _resolve_paths(
    db_path: str | Path, substances_path: str | Path
) -> tuple[Path, Path]:
    """解析数据库路径。

    db_path 语义为**审计数据库**路径（BacktestAnalyzer 消费），默认取
    ``AppConfig.backtest_audit_path``（独立审计库，与价格缓存分库）。
    """
    from long_earn.config import AppConfig  # noqa: PLC0415

    cfg = AppConfig.from_env()
    resolved_db = Path(db_path) if db_path else Path(cfg.backtest_audit_path)
    resolved_substances = (
        Path(substances_path) if substances_path else Path(cfg.memory_path)
    )
    return resolved_db, resolved_substances


def _register_run_routes(  # noqa: C901
    app: FastAPI, analyzer: BacktestAnalyzer
) -> None:
    """注册回测运行查询端点。"""

    @app.get("/api/health", response_model=schemas.HealthResponse, operation_id="health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/runs", response_model=schemas.RunsResponse, operation_id="list_runs")
    async def list_runs():
        runs = analyzer.get_runs_summary()
        return {"runs": runs}

    @app.delete(
        "/api/runs/clean",
        response_model=schemas.CleanRunsResponse,
        operation_id="clean_empty_runs",
    )
    async def clean_empty_runs():
        """删除空跑或错误运行的回测数据。"""
        bad_ids = analyzer.get_empty_or_error_runs()
        deleted = 0
        for rid in bad_ids:
            deleted += analyzer.delete_run(rid)
        logger.info(f"清理完成: 删除 {len(bad_ids)} 个问题 run, {deleted} 条记录")
        return {"deleted_runs": len(bad_ids), "deleted_records": deleted}

    @app.delete(
        "/api/runs/{run_id}",
        response_model=schemas.DeleteRunResponse,
        operation_id="delete_run",
    )
    async def delete_run(run_id: str):
        """删除指定回测运行的所有审计日志。"""
        deleted = analyzer.delete_run(run_id)
        if not deleted:
            raise HTTPException(404, f"Run {run_id} not found")
        logger.info(f"删除回测运行: {run_id}, {deleted} 条记录")
        return {"deleted_run_id": run_id, "deleted_records": deleted}

    @app.get(
        "/api/runs/{run_id}/summary",
        response_model=schemas.RunSummaryResponse,
        operation_id="run_summary",
    )
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

    @app.get(
        "/api/runs/{run_id}/equity",
        response_model=schemas.EquityResponse,
        operation_id="run_equity",
    )
    async def run_equity(run_id: str):
        curve = analyzer.export_equity_curve(run_id)
        return {"run_id": run_id, "equity_curve": curve}

    @app.get(
        "/api/runs/{run_id}/trades",
        response_model=schemas.TradesResponse,
        operation_id="run_trades",
    )
    async def run_trades(run_id: str):
        journal = analyzer.export_trade_journal(run_id)
        return {"run_id": run_id, "trades": journal}

    @app.get(
        "/api/runs/{run_id}/signals",
        response_model=schemas.SignalsResponse,
        operation_id="run_signals",
    )
    async def run_signals(run_id: str):
        signals = analyzer.export_signal_history(run_id)
        return {"run_id": run_id, "signals": signals}

    @app.get(
        "/api/runs/{run_id}/dashboard",
        response_model=schemas.DashboardData,
        operation_id="run_dashboard",
    )
    async def run_dashboard(run_id: str):
        data = analyzer.export_dashboard_data(run_id)
        if not data.get("equity_curve"):
            raise HTTPException(404, "Run not found")
        return data

    @app.get(
        "/api/runs/{run_id}/risk",
        response_model=schemas.RiskResponse,
        operation_id="run_risk",
    )
    async def run_risk(run_id: str):
        risk = analyzer.get_risk_metrics(run_id)
        return {"run_id": run_id, "risk_metrics": risk}

    @app.get(
        "/api/runs/{run_id}/daily_returns",
        response_model=schemas.DailyReturnsResponse,
        operation_id="run_daily_returns",
    )
    async def run_daily_returns(run_id: str):
        daily = analyzer.get_daily_returns(run_id)
        if daily.is_empty():
            return {"run_id": run_id, "daily_returns": []}
        returns_list = daily.select(["date", "daily_return"]).to_dicts()
        return {"run_id": run_id, "daily_returns": returns_list}


def _register_chart_export_routes(
    app: FastAPI, analyzer: BacktestAnalyzer
) -> None:
    """注册图表和导出端点。"""

    @app.get(
        "/api/runs/{run_id}/symbols",
        response_model=schemas.SymbolsResponse,
        operation_id="traded_symbols",
    )
    async def traded_symbols(run_id: str):
        symbols = analyzer.get_traded_symbols(run_id)
        return {"run_id": run_id, "symbols": symbols}

    @app.get(
        "/api/runs/{run_id}/symbol_charts",
        response_model=schemas.SymbolChartsResponse,
        operation_id="all_symbol_charts",
    )
    async def all_symbol_charts(run_id: str):
        charts = analyzer.export_all_symbol_charts(run_id)
        return {"run_id": run_id, "symbols": len(charts), "charts": charts}

    @app.get(
        "/api/runs/{run_id}/symbol/{symbol}/chart",
        response_model=schemas.SymbolChartData,
        operation_id="symbol_chart",
    )
    async def symbol_chart(run_id: str, symbol: str):
        data = analyzer.export_symbol_chart_data(run_id, symbol)
        return data

    @app.get(
        "/api/runs/{run_id}/export",
        operation_id="export_trades",
    )
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

    @app.post(
        "/api/compare",
        response_model=schemas.CompareResponse,
        operation_id="compare_runs",
    )
    async def compare_runs(req: schemas.CompareRequest):
        run_ids = req.run_ids
        if not run_ids:
            raise HTTPException(400, "run_ids is required")
        comparison = analyzer.compare_runs(run_ids)
        return {"comparison": comparison.to_dicts()}


def _enrich_sectors_batch(cache: Any) -> None:
    """通过 xtquant THY1/DY1 板块批量回填 industry + region。

    遍历同花顺一级行业（THY1）和地域一级行政区（DY1）板块，
    构建 ``{symbol: 分类名}`` 映射，批量 UPDATE 到 instrument_details 表。
    仅更新空值行，不覆盖已有数据。

    xtquant 不可用时静默跳过（缓存已有数据仍可正常服务）。
    """
    try:
        from long_earn.backtest.data.miniqmt_provider import (  # noqa: PLC0415
            MiniQmtClient,
        )

        client = MiniQmtClient.get()
        if not client.is_available():
            logger.debug("xtquant 不可用，跳过板块批量回填")
            return

        # 行业
        industry_map = client.build_sector_mapping("THY1")
        if industry_map:
            cache.batch_update_instrument_sectors(industry_map, "industry")

        # 地域
        region_map = client.build_sector_mapping("DY1")
        if region_map:
            cache.batch_update_instrument_sectors(region_map, "region")

    except Exception as e:
        logger.warning(f"板块批量回填失败: {e}")


# 避免每次请求都重跑批量回填（进程内标记）
_sector_enrichment_done = False


def _fetch_symbol_detail(cache: Any, symbol: str) -> dict[str, Any] | None:
    """获取标的详情，优先缓存 → xtquant 回退 → THY1/DY1 板块批量补充行业/地区。

    Args:
        cache: DataCache 实例
        symbol: 标的代码
    Returns:
        标的详情字典或 None
    """
    global _sector_enrichment_done  # noqa: PLW0603
    detail = cache.get_instrument_detail_cached(symbol)

    # 缓存命中但 industry 为空 → 触发一次批量回填（进程内仅执行一次）
    if detail and not detail.get("industry") and not _sector_enrichment_done:
        _sector_enrichment_done = True
        _enrich_sectors_batch(cache)
        detail = cache.get_instrument_detail_cached(symbol)

    if detail is not None:
        return detail

    # 缓存未命中 → xtquant get_instrument_detail 回退
    try:
        from xtquant import xtdata  # noqa: PLC0415

        raw = xtdata.get_instrument_detail(symbol)
        if raw:
            cache.save_instrument_detail(symbol, raw)
            detail = cache.get_instrument_detail_cached(symbol)
            if detail and not detail.get("industry") and not _sector_enrichment_done:
                _sector_enrichment_done = True
                _enrich_sectors_batch(cache)
                detail = cache.get_instrument_detail_cached(symbol)
    except ImportError:
        logger.warning("xtquant 不可用，无法获取标的详情")
    except Exception as e:
        logger.warning(f"获取标的详情失败: {e}")

    return detail


def _get_sector_stats(cache: Any) -> dict[str, int]:
    """统计 instrument_details 表中行业/地区填充情况。"""
    conn = cache._get_conn()
    total = conn.execute("SELECT COUNT(*) FROM instrument_details").fetchone()[0]
    with_industry = conn.execute(
        "SELECT COUNT(*) FROM instrument_details WHERE industry != ''"
    ).fetchone()[0]
    with_region = conn.execute(
        "SELECT COUNT(*) FROM instrument_details WHERE region != ''"
    ).fetchone()[0]
    return {
        "total": total,
        "with_industry": with_industry,
        "with_region": with_region,
    }


def _register_symbol_routes(app: FastAPI) -> None:
    """注册标的详情查询端点。"""

    @app.get(
        "/api/symbols/names",
        response_model=schemas.SymbolNamesResponse,
        operation_id="symbol_names",
    )
    async def symbol_names(symbols: str = Query("")):
        """批量获取标的中文名。

        优先从 DuckDB 缓存 instrument_details 表读取；
        缓存未命中的标的回退到 xtquant get_instrument_detail 实时获取。
        """
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        if not symbol_list:
            return {"names": {}}

        # 1. 优先从缓存批量读取
        from long_earn.backtest.data.cache import DataCache  # noqa: PLC0415

        cache = DataCache()
        names = cache.get_instrument_names_batch(symbol_list)

        # 2. 缓存未命中的标的，回退到 xtquant 实时获取
        missing = [s for s in symbol_list if s not in names]
        if missing:
            try:
                from xtquant import xtdata  # noqa: PLC0415

                for sym in missing:
                    detail = xtdata.get_instrument_detail(sym)
                    if detail and detail.get("InstrumentName"):
                        names[sym] = str(detail["InstrumentName"])
                        # 顺便写入缓存，下次命中
                        cache.save_instrument_detail(sym, detail)
            except ImportError:
                logger.warning("xtquant 不可用，无法获取标的名称")
            except Exception as e:
                logger.warning(f"获取标的名称失败: {e}")

        with contextlib.suppress(Exception):
            cache.close()

        return {"names": names}

    @app.get(
        "/api/symbols/{symbol}/detail",
        response_model=schemas.SymbolDetailResponse,
        operation_id="symbol_detail",
    )
    async def symbol_detail(symbol: str):
        """获取单个标的的详情（公司信息弹窗用）。

        优先从 DuckDB 缓存读取；缓存未命中或 industry 为空时回退到
        xtquant get_instrument_detail + THY1/DY1 板块批量补充。
        """
        from long_earn.backtest.data.cache import DataCache  # noqa: PLC0415

        cache = DataCache()
        detail = _fetch_symbol_detail(cache, symbol)
        with contextlib.suppress(Exception):
            cache.close()
        if detail is None:
            raise HTTPException(404, f"标的 {symbol} 详情未找到")
        return detail

    @app.post(
        "/api/symbols/refresh-sectors",
        response_model=schemas.SectorStatsResponse,
        operation_id="refresh_sectors",
    )
    async def refresh_sectors():
        """手动触发行业+地区批量回填（通过 xtquant THY1/DY1 板块）。

        重置进程内标记，允许下一次请求重新执行批量回填。
        """
        global _sector_enrichment_done  # noqa: PLW0603
        _sector_enrichment_done = False
        from long_earn.backtest.data.cache import DataCache  # noqa: PLC0415

        cache = DataCache()
        _enrich_sectors_batch(cache)
        stats = _get_sector_stats(cache)
        with contextlib.suppress(Exception):
            cache.close()
        _sector_enrichment_done = True
        return stats

    @app.get(
        "/api/symbols/{symbol}/financials",
        response_model=schemas.FinancialsResponse,
        operation_id="symbol_financials",
    )
    async def symbol_financials(symbol: str):
        """获取标的历年财务数据（用于前端可视化）。"""
        from long_earn.backtest.data.cache import DataCache  # noqa: PLC0415

        cache = DataCache()
        financials = cache.get_financial_data(symbol, limit=20)
        with contextlib.suppress(Exception):
            cache.close()
        return {"symbol": symbol, "financials": financials}


def _register_event_routes(
    app: FastAPI, event_analyzer: EventAnalyzer
) -> None:
    """注册事件流 REST 端点。"""

    @app.get(
        "/api/events",
        response_model=schemas.EventsResponse,
        operation_id="list_events",
    )
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

    @app.get(
        "/api/events/stats",
        response_model=schemas.EventStats,
        operation_id="event_stats",
    )
    async def event_stats():
        return event_analyzer.event_stats()

    @app.get(
        "/api/events/timeline",
        response_model=schemas.TimelineResponse,
        operation_id="event_timeline",
    )
    async def event_timeline(days: int = Query(30)):
        timeline = event_analyzer.event_timeline(days=days)
        return {"timeline": timeline}

    @app.get(
        "/api/events/relations",
        response_model=schemas.RelationsResponse,
        operation_id="list_relations",
    )
    async def list_relations(
        limit: int = Query(50),
        target: str | None = Query(None),
        direction: str | None = Query(None),
    ):
        relations = event_analyzer.list_relations(
            limit=limit, target=target, direction=direction
        )
        return {"count": len(relations), "relations": relations}

    @app.get(
        "/api/events/{sid}",
        response_model=schemas.EventDetail,
        operation_id="get_event",
    )
    async def get_event(sid: str):
        if not sid:
            raise HTTPException(400, "sid is required")
        event = event_analyzer.get_event(sid)
        if event is None:
            raise HTTPException(404, "Event not found")
        return event

    @app.post(
        "/api/events/trigger",
        response_model=schemas.TriggerResponse,
        operation_id="trigger_event_inference",
    )
    async def trigger_event_inference(req: schemas.TriggerRequest):
        """触发事件推理管线。"""
        query = req.query
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
        if not _websocket_origin_allowed(websocket):
            await websocket.close(code=1008, reason="WebSocket Origin is not allowed")
            return
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

        from long_earn.context_init import create_runtime_context  # noqa: PLC0415

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


def _register_research_routes(  # noqa: C901, PLR0915
    app: FastAPI,
    _db_path: Path,
) -> None:
    """注册策略研究 WebSocket 和 REST 端点。"""

    _research_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research")

    @app.websocket("/ws/research")
    async def ws_research(websocket: WebSocket):
        if not _websocket_origin_allowed(websocket):
            await websocket.close(code=1008, reason="WebSocket Origin is not allowed")
            return
        await websocket.accept()
        active_ws: set[WebSocket] = app.state.active_ws
        active_ws.add(websocket)
        logger.info(f"研究 WebSocket 客户端已连接 (活跃: {len(active_ws)})")
        try:
            while True:
                msg = await websocket.receive_json()
                action = msg.get("action", "")

                if action == "ping":
                    await websocket.send_json({"type": "pong"})

                elif action == "start":
                    idea = msg.get("idea", "")
                    if not idea:
                        await websocket.send_json({
                            "type": "error",
                            "detail": "idea 不能为空",
                        })
                        continue

                    max_rounds = int(msg.get("max_rounds", 3))
                    max_iterations = int(msg.get("max_iterations", 2))
                    min_improvement = float(msg.get("min_improvement", 0.005))

                    await websocket.send_json({
                        "type": "research_started",
                        "idea": idea,
                        "max_rounds": max_rounds,
                        "max_iterations": max_iterations,
                    })

                    def _run_in_thread(
                            _idea: str = idea,
                            _max_rounds: int = max_rounds,
                            _max_iterations: int = max_iterations,
                            _min_improvement: float = min_improvement,
                        ) -> None:
                            from long_earn.config import AppConfig  # noqa: PLC0415
                            from long_earn.context_init import (  # noqa: PLC0415
                                initialize_context,
                            )
                            from long_earn.services.strategy_research_service import (  # noqa: PLC0415
                                StrategyResearchService,
                            )

                            config = AppConfig.from_env()
                            config.backtest_start_date = config.train_start_date
                            config.backtest_end_date = config.train_end_date
                            ctx = initialize_context(config)

                            def _send_progress(data: dict[str, Any]) -> None:
                                try:
                                    loop = asyncio.get_event_loop()
                                    asyncio.run_coroutine_threadsafe(
                                        _broadcast_event(active_ws, data), loop
                                    )
                                except Exception:
                                    pass

                            service = StrategyResearchService(ctx)
                            try:
                                service.run_loop(
                                    idea=_idea,
                                    max_rounds=_max_rounds,
                                    max_iterations=_max_iterations,
                                    min_improvement=_min_improvement,
                                    progress_callback=_send_progress,
                                )
                            except Exception as e:
                                logger.exception("策略研究失败")
                                _send_progress({
                                    "type": "research_error",
                                    "detail": str(e),
                                })

                    _research_executor.submit(_run_in_thread)

        except WebSocketDisconnect:
            logger.info("研究 WebSocket 客户端已断开")
        except Exception:
            logger.exception("研究 WebSocket 错误")
        finally:
            active_ws.discard(websocket)

    @app.post(
        "/api/research/start",
        response_model=schemas.ResearchStartResponse,
        operation_id="start_research",
    )
    async def start_research(req: schemas.ResearchStartRequest):
        """触发策略研究（REST 入口，通过 WebSocket 广播进度）。"""
        idea = req.idea
        if not idea:
            raise HTTPException(400, "idea is required")

        max_rounds = req.max_rounds
        max_iterations = req.max_iterations
        min_improvement = req.min_improvement

        active_ws: set[WebSocket] = app.state.active_ws

        def _run_in_thread(
                _idea: str = idea,
                _max_rounds: int = max_rounds,
                _max_iterations: int = max_iterations,
                _min_improvement: float = min_improvement,
            ) -> None:
                from long_earn.config import AppConfig  # noqa: PLC0415
                from long_earn.context_init import initialize_context  # noqa: PLC0415
                from long_earn.services.strategy_research_service import (  # noqa: PLC0415
                    StrategyResearchService,
                )

                config = AppConfig.from_env()
                config.backtest_start_date = config.train_start_date
                config.backtest_end_date = config.train_end_date
                ctx = initialize_context(config)

                def _send_progress(data: dict[str, Any]) -> None:
                    try:
                        loop = asyncio.get_event_loop()
                        asyncio.run_coroutine_threadsafe(
                            _broadcast_event(active_ws, data), loop
                        )
                    except Exception:
                        pass

                service = StrategyResearchService(ctx)
                try:
                    service.run_loop(
                        idea=_idea,
                        max_rounds=_max_rounds,
                        max_iterations=_max_iterations,
                        min_improvement=_min_improvement,
                        progress_callback=_send_progress,
                    )
                except Exception as e:
                    logger.exception("策略研究失败")
                    _send_progress({
                        "type": "research_error",
                        "detail": str(e),
                    })

        _research_executor.submit(_run_in_thread)

        return {
            "status": "started",
            "idea": idea,
            "max_rounds": max_rounds,
            "max_iterations": max_iterations,
        }

def _register_api_routes(
    app: FastAPI,
    analyzer: BacktestAnalyzer,
    event_analyzer: EventAnalyzer,
) -> None:
    """注册所有 API 和 WebSocket 路由（不含页面路由）。"""
    _register_run_routes(app, analyzer)
    _register_chart_export_routes(app, analyzer)
    _register_symbol_routes(app)
    _register_event_routes(app, event_analyzer)
    _register_ws_routes(app, event_analyzer)


def _create_app(
    db_path: str | Path = "",
    substances_path: str | Path = "",
    remote_mode: bool = False,
) -> FastAPI:
    """创建 FastAPI 应用实例。

    Args:
        db_path: DuckDB 审计数据库路径
        substances_path: SubstanceStore DuckDB 路径
    """
    app = FastAPI(title="Long Earn 可视化仪表盘", version="2.0.0")
    app.state.active_ws = set()  # type: ignore[attr-defined]
    app.state.remote_mode = remote_mode

    @app.middleware("http")
    async def reject_cross_origin_writes(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Prevent browser-originated API writes from arbitrary sites in remote mode."""
        if remote_mode and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if origin is not None and not _origin_matches_host(origin, host):
                return PlainTextResponse("Origin is not allowed", status_code=403)
        return await call_next(request)

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

    # 先注册 API 和 WebSocket 路由（优先级高）
    _register_api_routes(app, analyzer, event_analyzer)
    _register_research_routes(app, resolved_db)

    if _WEB_DIST.exists() and (_WEB_DIST / "index.html").exists():
        # 生产模式：React SPA
        app.mount(
            "/assets",
            StaticFiles(directory=str(_WEB_DIST / "assets")),
            name="web_assets",
        )

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str = ""):
            if full_path.startswith("api/") or full_path.startswith("ws/"):
                raise HTTPException(404)
            index_path = _WEB_DIST / "index.html"
            return HTMLResponse(index_path.read_text(encoding="utf-8"))

        logger.info("前端模式: React SPA (web/dist/)")
    else:
        logger.warning("前端构建产物不存在 (web/dist/)，请先运行 `cd web && npm run build`")

    return app


def serve_visualization_fastapi(
    host: str = "127.0.0.1",
    port: int = 8090,
    db_path: str | Path = "",
    substances_path: str | Path = "",
    reload: bool = False,
    allow_remote: bool = False,
) -> None:
    """启动 FastAPI 可视化服务。

    Args:
        host: 监听地址
        port: 监听端口
        db_path: DuckDB 审计数据库路径；空字符串时取 AppConfig.backtest_audit_path
            （独立审计库；价格行情仍从缓存库读取）
        substances_path: SubstanceStore DuckDB 路径；空字符串时取 AppConfig.memory_path
        reload: 是否启用热重载（开发模式）
        allow_remote: 显式允许绑定非 loopback 地址；远程部署仍需额外认证
    """
    _validate_bind_host(host, allow_remote)
    remote_mode = not _is_loopback_host(host)
    app = _create_app(
        db_path=db_path, substances_path=substances_path, remote_mode=remote_mode
    )

    logger.info(f"FastAPI 可视化服务启动: http://{host}:{port}")
    logger.info("  页面:")
    logger.info("    GET /                  回测可视化仪表盘")
    logger.info("    GET /event-flow         事件流可视化")
    logger.info("  WebSocket:")
    logger.info("    WS  /ws/events          实时事件流推送")
    logger.info("    WS  /ws/research        策略研究实时进度")
    logger.info("  API:")
    logger.info("    GET /api/health")
    logger.info("    GET /api/runs")
    logger.info("    GET /api/runs/{run_id}/dashboard")
    logger.info("    POST /api/events/trigger  触发事件推理管线")
    logger.info("    POST /api/research/start  触发策略研究")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        reload=reload,
    )

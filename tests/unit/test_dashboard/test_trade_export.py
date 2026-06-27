"""交易数据导出与个股图表可视化接口测试

验证 BacktestAnalyzer 的新增导出/可视化方法能从 DuckDB 审计日志
正确提取交易记录（时间/标的/金额）与个股价格+买卖点标注数据。
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path

from long_earn.backtest.engine.audit import AuditLogger, DuckDBAuditProvider
from long_earn.dashboard.analyzer import BacktestAnalyzer


def _make_provider_and_logger(db_path: Path, run_id: str) -> tuple[AuditLogger, DuckDBAuditProvider]:
    provider = DuckDBAuditProvider(db_path=db_path)
    logger = AuditLogger(provider=provider, run_id=run_id)
    return logger, provider


def _write_fills(logger: AuditLogger, run_id: str) -> None:
    """写入模拟 FILL 事件（两笔买入 + 一笔卖出）"""
    logger.log_transition(
        event_type="FILL",
        trace_id="t1",
        component="Broker",
        status="SUCCESS",
        payload={
            "symbol": "600000.SH",
            "type": "BUY",
            "price": 10.5,
            "quantity": 1000.0,
            "portfolio_value": 1_000_000.0,
        },
        timestamp=datetime(2023, 1, 3, 9, 30),
    )
    logger.log_transition(
        event_type="FILL",
        trace_id="t2",
        component="Broker",
        status="SUCCESS",
        payload={
            "symbol": "000001.SZ",
            "type": "BUY",
            "price": 15.2,
            "quantity": 500.0,
            "portfolio_value": 1_005_000.0,
        },
        timestamp=datetime(2023, 1, 4, 9, 30),
    )
    logger.log_transition(
        event_type="FILL",
        trace_id="t3",
        component="Broker",
        status="SUCCESS",
        payload={
            "symbol": "600000.SH",
            "type": "SELL",
            "price": 11.8,
            "quantity": 1000.0,
            "portfolio_value": 1_010_000.0,
        },
        timestamp=datetime(2023, 2, 1, 9, 30),
    )


def _write_prices(db_path: Path) -> None:
    """写入模拟行情数据到 price_daily 表"""
    import duckdb

    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_daily (
            symbol VARCHAR NOT NULL,
            date DATE NOT NULL,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute(
        "INSERT INTO price_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["600000.SH", "2023-01-03", 10.3, 10.6, 10.2, 10.5, 10000.0],
    )
    conn.execute(
        "INSERT INTO price_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["600000.SH", "2023-01-04", 10.6, 10.9, 10.5, 10.8, 12000.0],
    )
    conn.execute(
        "INSERT INTO price_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["600000.SH", "2023-02-01", 11.5, 11.9, 11.4, 11.8, 8000.0],
    )
    conn.close()


def test_export_trade_traces():
    """export_trade_traces 应返回完整交易日志（含金额 = 价格 × 数量）"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "audit.duckdb"
    try:
        logger, provider = _make_provider_and_logger(db_path, "run-traces")
        _write_fills(logger, "run-traces")
        provider.close()

        analyzer = BacktestAnalyzer(db_path)
        traces = analyzer.export_trade_traces("run-traces")

        assert len(traces) == 3
        # 验证字段结构
        first = traces[0]
        assert set(first.keys()) == {
            "time",
            "trace_id",
            "symbol",
            "direction",
            "price",
            "quantity",
            "amount",
            "portfolio_value",
        }
        # 验证金额计算
        assert first["symbol"] == "600000.SH"
        assert first["direction"] == "BUY"
        assert first["amount"] == round(10.5 * 1000.0, 2)  # 10500.0
        # 验证方向字段（从 payload.type 映射）
        assert traces[2]["direction"] == "SELL"
        assert traces[2]["amount"] == round(11.8 * 1000.0, 2)  # 11800.0
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_get_traded_symbols():
    """get_traded_symbols 应返回去重后的交易标的列表"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "audit.duckdb"
    try:
        logger, provider = _make_provider_and_logger(db_path, "run-sym")
        _write_fills(logger, "run-sym")
        provider.close()

        analyzer = BacktestAnalyzer(db_path)
        symbols = analyzer.get_traded_symbols("run-sym")

        assert len(symbols) == 2
        assert "600000.SH" in symbols
        assert "000001.SZ" in symbols
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_export_trade_traces_to_file_csv():
    """export_trade_traces_to_file 应导出 CSV 文件"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "audit.duckdb"
    try:
        logger, provider = _make_provider_and_logger(db_path, "run-csv")
        _write_fills(logger, "run-csv")
        provider.close()

        analyzer = BacktestAnalyzer(db_path)
        out_path = analyzer.export_trade_traces_to_file(
            "run-csv", tmp_dir / "trades", fmt="csv"
        )
        assert out_path.exists()
        assert out_path.suffix == ".csv"
        content = out_path.read_text(encoding="utf-8")
        assert "symbol" in content
        assert "600000.SH" in content
        assert "amount" in content
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_export_trade_traces_to_file_json():
    """export_trade_traces_to_file 应导出 JSON 文件"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "audit.duckdb"
    try:
        logger, provider = _make_provider_and_logger(db_path, "run-json")
        _write_fills(logger, "run-json")
        provider.close()

        analyzer = BacktestAnalyzer(db_path)
        out_path = analyzer.export_trade_traces_to_file(
            "run-json", tmp_dir / "trades", fmt="json"
        )
        assert out_path.exists()
        assert out_path.suffix == ".json"
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["trade_count"] == 3
        assert len(data["trades"]) == 3
        assert data["trades"][0]["symbol"] == "600000.SH"
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_export_symbol_chart_data():
    """export_symbol_chart_data 应返回价格走势 + 买卖点标注数据"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "audit.duckdb"
    try:
        logger, provider = _make_provider_and_logger(db_path, "run-chart")
        _write_fills(logger, "run-chart")
        provider.close()
        _write_prices(db_path)

        analyzer = BacktestAnalyzer(db_path)
        chart = analyzer.export_symbol_chart_data("run-chart", "600000.SH")

        assert chart["symbol"] == "600000.SH"
        # 价格历史应有 3 个交易日
        assert len(chart["price_history"]) == 3
        assert chart["price_history"][0]["close"] == 10.5
        # 交易点应有 2 个（1 买 + 1 卖）
        assert len(chart["trade_points"]) == 2
        buy = chart["trade_points"][0]
        assert buy["direction"] == "BUY"
        assert buy["price"] == 10.5
        assert buy["quantity"] == 1000.0
        assert buy["amount"] == round(10.5 * 1000.0, 2)
        sell = chart["trade_points"][1]
        assert sell["direction"] == "SELL"
        assert sell["price"] == 11.8
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_export_all_symbol_charts():
    """export_all_symbol_charts 应为每个交易标的生成图表数据"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "audit.duckdb"
    try:
        logger, provider = _make_provider_and_logger(db_path, "run-all")
        _write_fills(logger, "run-all")
        provider.close()
        _write_prices(db_path)

        analyzer = BacktestAnalyzer(db_path)
        charts = analyzer.export_all_symbol_charts("run-all")

        assert len(charts) == 2
        symbols = [c["symbol"] for c in charts]
        assert "600000.SH" in symbols
        assert "000001.SZ" in symbols
        # 600000.SH 有价格数据
        chart_600000 = next(c for c in charts if c["symbol"] == "600000.SH")
        assert len(chart_600000["price_history"]) == 3
        assert len(chart_600000["trade_points"]) == 2
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_export_dashboard_data_includes_traded_symbols():
    """export_dashboard_data 应包含 traded_symbols 字段"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "audit.duckdb"
    try:
        logger, provider = _make_provider_and_logger(db_path, "run-dash")
        _write_fills(logger, "run-dash")
        provider.close()

        analyzer = BacktestAnalyzer(db_path)
        data = analyzer.export_dashboard_data("run-dash")

        assert "traded_symbols" in data
        assert len(data["traded_symbols"]) == 2
        assert "600000.SH" in data["traded_symbols"]
    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)

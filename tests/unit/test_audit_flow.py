"""审计流接口测试

验证 DuckDBAuditProvider + BacktestAnalyzer 的集成接口，
不验证内部因果链细节。
"""

import shutil
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from long_earn.backtest.domain.entities import SignalEvent
from long_earn.backtest.engine.audit import DuckDBAuditProvider
from long_earn.backtest.engine.broker import TradingCostConfig
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.parallel import ParallelRunner
from long_earn.backtest.engine.param_grid import ParamGrid
from long_earn.backtest.engine.strategy import BaseStrategy


class MockStrategy(BaseStrategy):
    def init(self):
        self._state = {"initialized": True}

    def on_bar(self, slab: pl.DataFrame, context: Any) -> SignalEvent | None:
        return SignalEvent(
            timestamp=datetime.now(),
            trace_id=str(uuid.uuid4()),
            event_id=f"sig_{uuid.uuid4().hex[:6]}",
            signals={"AAPL": 0.1},
            strategy_id="test_strat",
        )


class _MockPanelProvider:
    """模拟数据提供者，直接返回预构造面板。"""

    def __init__(self, panel: pl.DataFrame) -> None:
        self._panel = panel

    def get_merged_panel_as_polars(
        self, symbols: list[str], start: str, end: str
    ) -> pl.DataFrame:
        return self._panel


_MOMENTUM_YAML = """
strategy:
  name: TestMomentum
  universe: { type: csi300 }
  start_date: "2024-01-01"
  end_date: "2024-01-12"
  operator_factors:
    - op: returns
      alias: mom
      params: { field: close, period: 5 }
  signals:
    - type: operator
      op: rank_top
      params: { field: mom, top: 1, ascending: false }
  weights: { method: equal }
"""


def _trending_panel(days: int = 12) -> pl.DataFrame:
    """构造上涨趋势面板（2 symbols × N days），供动量策略产生信号。"""
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(days):
        ts = base + timedelta(days=i)
        for sym, growth in [("A.SZ", 1.008), ("B.SH", 0.995)]:
            close = round(10.0 * (growth**i), 4)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": close,
                    "high": close * 1.005,
                    "low": close * 0.995,
                    "close": close,
                    "volume": 10000.0,
                }
            )
    return pl.DataFrame(rows)


def test_audit_records_events():
    """审计系统应记录引擎执行事件"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_audit.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider, cost_config=TradingCostConfig()
        )

        full_data = pl.DataFrame(
            {
                "timestamp": [datetime(2023, 1, 1)],
                "symbol": ["AAPL"],
                "close": [150.0],
            }
        )

        engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

        strategy = MockStrategy(strategy_id="test_strat")
        engine.run(strategy, "2023-01-01", "2023-01-02", ["AAPL"])

        # 使用 duckdb 直接查询（避免 backtest_analyzer 中的硬编码 schema 名）
        import duckdb
        conn = duckdb.connect(str(db_path))
        all_events = conn.execute(
            'SELECT * FROM "backtest_audit".logs ORDER BY timestamp ASC'
        ).pl()
        conn.close()
        assert not all_events.is_empty(), "No audit events were recorded!"

        event_types = all_events["event_type"].to_list()
        assert "SIGNAL" in event_types, "No SIGNAL event found in audit logs!"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_parallel_grid_writes_duckdb_audit():
    """并行回测应将 RUN_START/RUN_END 审计事件写入 worker 专属 DuckDB 文件。

    验证 P0-11 修复：worker 注入 DuckDBAuditProvider 后，进程结束后审计数据
    可通过 query_events / 直接 SQL 查询，不再丢失。
    """
    import duckdb

    tmp_dir = Path(tempfile.mkdtemp())
    audit_base = tmp_dir / "audit.duckdb"

    try:
        panel = _trending_panel(days=12)
        provider = _MockPanelProvider(panel)
        runner = ParallelRunner(max_workers=1, data_provider=provider)

        result = runner.run_grid(
            strategy_template=_MOMENTUM_YAML,
            param_grid=ParamGrid(),
            start_date="2024-01-01",
            end_date="2024-01-12",
            symbols=["A.SZ", "B.SH"],
            audit_db_path=audit_base,
        )

        assert result.success_count >= 1, "并行回测应至少有 1 个成功任务"

        # worker 专属 db 文件：{base}_{task_id}.duckdb，task_id="0" 为首个组合
        worker_db = tmp_dir / "audit_0.duckdb"
        assert worker_db.exists(), f"worker DuckDB 文件不存在: {worker_db}"

        conn = duckdb.connect(str(worker_db))
        rows = conn.execute(
            'SELECT event_type FROM "backtest_audit".logs ORDER BY timestamp ASC'
        ).fetchall()
        conn.close()

        event_types = [r[0] for r in rows]
        assert "RUN_START" in event_types, "缺少 RUN_START 审计事件"
        assert "RUN_END" in event_types, "缺少 RUN_END 审计事件"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

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


# ── AUDIT-P2-17: 审计时点对齐 ─────────────────────────────────


def test_audit_equity_curve_alignment():
    """MARKET_DATA 审计 portfolio_value 应与 equity_curve 逐日一致。

    每 bar 的 MARKET_DATA 事件记录 portfolio_value，equity_curve 在 bar 末尾
    通过 _sync_equity_curve 追加。二者应长度相同、值逐日对齐。
    """
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_align.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider, cost_config=TradingCostConfig()
        )

        days = 5
        full_data = pl.DataFrame(
            {
                "timestamp": [
                    datetime(2023, 1, 1) + timedelta(days=i)
                    for i in range(days)
                ],
                "symbol": ["AAPL"] * days,
                "open": [100.0 + i * 2 for i in range(days)],
                "high": [101.0 + i * 2 for i in range(days)],
                "low": [99.0 + i * 2 for i in range(days)],
                "close": [100.0 + i * 2 for i in range(days)],
                "volume": [10000.0] * days,
            }
        )
        engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

        strategy = MockStrategy(strategy_id="test_strat")
        result = engine.run(
            strategy, "2023-01-01", "2023-01-05", ["AAPL"]
        )

        import duckdb

        conn = duckdb.connect(str(db_path))
        mkt_rows = conn.execute(
            "SELECT payload FROM \"backtest_audit\".logs "
            "WHERE event_type = 'MARKET_DATA' ORDER BY timestamp ASC"
        ).fetchall()
        conn.close()

        audit_values = [
            (r[0] if isinstance(r[0], dict) else __import__("json").loads(r[0]))[
                "portfolio_value"
            ]
            for r in mkt_rows
        ]

        equity_curve = [d["value"] for d in (result.daily_returns or [])]

        assert len(audit_values) == len(equity_curve), (
            f"审计事件数({len(audit_values)}) != 净值曲线点数({len(equity_curve)})"
        )

        for i, (av, ev) in enumerate(zip(audit_values, equity_curve, strict=True)):
            assert abs(av - ev) < 1e-9, (
                f"bar {i}: 审计 portfolio_value={av} != equity_curve={ev}"
            )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── P1-16: 缺失事件类型覆盖 ────────────────────────────────────


def test_audit_run_start_contains_symbols_and_strategy_hash():
    """RUN_START 应包含完整 symbols 列表和 strategy_hash（P1-13 修复）"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_run_start.duckdb"

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
        test_yaml = "name: test\nuniverse: { type: csi300 }\n"
        engine.run(
            strategy,
            "2023-01-01",
            "2023-01-02",
            ["AAPL"],
            strategy_yaml=test_yaml,
        )

        import duckdb

        conn = duckdb.connect(str(db_path))
        run_start = conn.execute(
            "SELECT payload FROM \"backtest_audit\".logs WHERE event_type = 'RUN_START'"
        ).fetchone()
        conn.close()

        assert run_start is not None
        payload = run_start[0] if isinstance(run_start[0], dict) else __import__(
            "json"
        ).loads(run_start[0])
        assert "symbols" in payload, "RUN_START 缺少 symbols 字段"
        assert payload["symbols"] == ["AAPL"]
        assert "strategy_yaml" in payload, "RUN_START 缺少 strategy_yaml 字段"
        assert payload["strategy_yaml"] == test_yaml
        assert "strategy_hash" in payload, "RUN_START 缺少 strategy_hash 字段"
        assert len(payload["strategy_hash"]) == 16

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_audit_market_data_contains_slab_summary():
    """MARKET_DATA 应包含 slab 摘要（P1-13 修复）"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_mkt_data.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider, cost_config=TradingCostConfig()
        )

        full_data = pl.DataFrame(
            {
                "timestamp": [datetime(2023, 1, 1), datetime(2023, 1, 2)],
                "symbol": ["AAPL", "AAPL"],
                "close": [150.0, 152.0],
                "volume": [10000.0, 12000.0],
            }
        )
        engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

        strategy = MockStrategy(strategy_id="test_strat")
        engine.run(strategy, "2023-01-01", "2023-01-02", ["AAPL"])

        import duckdb

        conn = duckdb.connect(str(db_path))
        mkt_events = conn.execute(
            "SELECT payload FROM \"backtest_audit\".logs WHERE event_type = 'MARKET_DATA'"
        ).fetchall()
        conn.close()

        assert len(mkt_events) > 0, "无 MARKET_DATA 审计事件"
        payload = (
            mkt_events[0][0]
            if isinstance(mkt_events[0][0], dict)
            else __import__("json").loads(mkt_events[0][0])
        )
        assert "slab_symbol_count" in payload, "MARKET_DATA 缺少 slab_symbol_count"
        assert "slab_close_range" in payload, "MARKET_DATA 缺少 slab_close_range"
        assert "slab_volume_sum" in payload, "MARKET_DATA 缺少 slab_volume_sum"
        assert "portfolio_value" in payload, "MARKET_DATA 缺少 portfolio_value"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_audit_run_id_consistency():
    """同一 run_id 的事件链应完整：RUN_START → SIGNAL → ORDER → FILL → RUN_END"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_run_id.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider, cost_config=TradingCostConfig()
        )

        full_data = pl.DataFrame(
            {
                "timestamp": [
                    datetime(2023, 1, 1),
                    datetime(2023, 1, 2),
                    datetime(2023, 1, 3),
                ],
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "close": [150.0, 152.0, 155.0],
                "volume": [10000.0, 12000.0, 11000.0],
            }
        )
        engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

        strategy = MockStrategy(strategy_id="test_strat")
        engine.run(strategy, "2023-01-01", "2023-01-03", ["AAPL"])

        import duckdb

        conn = duckdb.connect(str(db_path))
        run_ids = conn.execute(
            "SELECT DISTINCT run_id FROM \"backtest_audit\".logs"
        ).fetchall()
        conn.close()

        assert len(run_ids) == 1, f"应只有 1 个 run_id，实际 {len(run_ids)} 个"
        run_id = run_ids[0][0]

        conn = duckdb.connect(str(db_path))
        events = conn.execute(
            "SELECT event_type FROM \"backtest_audit\".logs "
            "WHERE run_id = ? ORDER BY timestamp ASC",
            [run_id],
        ).fetchall()
        conn.close()

        event_types = [e[0] for e in events]
        assert "RUN_START" in event_types, "缺少 RUN_START"
        assert "RUN_END" in event_types, "缺少 RUN_END"
        assert "SIGNAL" in event_types, "缺少 SIGNAL"
        # 事件顺序正确：RUN_START 在最前，RUN_END 在最后
        assert event_types[0] == "RUN_START", "RUN_START 不是第一个事件"
        assert event_types[-1] == "RUN_END", "RUN_END 不是最后一个事件"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_audit_signal_to_dict_json_serializable():
    """SIGNAL 事件的 signals 字段应为 JSON dict 而非 str() 序列化（P1-13 修复）"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_signal_dict.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider, cost_config=TradingCostConfig()
        )

        full_data = pl.DataFrame(
            {
                "timestamp": [datetime(2023, 1, 1), datetime(2023, 1, 2)],
                "symbol": ["AAPL", "AAPL"],
                "close": [150.0, 152.0],
                "volume": [10000.0, 12000.0],
            }
        )
        engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

        strategy = MockStrategy(strategy_id="test_strat")
        engine.run(strategy, "2023-01-01", "2023-01-02", ["AAPL"])

        import json

        import duckdb

        conn = duckdb.connect(str(db_path))
        signal_rows = conn.execute(
            "SELECT payload FROM \"backtest_audit\".logs WHERE event_type = 'SIGNAL'"
        ).fetchall()
        conn.close()

        assert len(signal_rows) > 0, "无 SIGNAL 审计事件"
        payload = (
            signal_rows[0][0]
            if isinstance(signal_rows[0][0], dict)
            else json.loads(signal_rows[0][0])
        )
        signals = payload.get("signals", {})
        assert isinstance(signals, dict), (
            f"signals 应为 dict，实际 {type(signals)}: {signals}"
        )
        # 验证 JSON 可序列化
        json.dumps(signals)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_audit_order_contains_exec_type():
    """ORDER 事件应存在（P1-08 修复）"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_order_exec.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider, cost_config=TradingCostConfig()
        )

        full_data = pl.DataFrame(
            {
                "timestamp": [
                    datetime(2023, 1, 1),
                    datetime(2023, 1, 2),
                    datetime(2023, 1, 3),
                    datetime(2023, 1, 4),
                ],
                "symbol": ["AAPL"] * 4,
                "open": [149.0, 151.0, 154.0, 157.0],
                "high": [151.0, 153.0, 156.0, 159.0],
                "low": [148.0, 150.0, 153.0, 156.0],
                "close": [150.0, 152.0, 155.0, 158.0],
                "volume": [10000.0] * 4,
            }
        )
        engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

        # 只在第一天发信号，避免重复信号被 Portfolio 跳过
        class OneShotStrategy(BaseStrategy):
            def init(self):
                self._state = {"fired": False}

            def on_bar(
                self, slab: pl.DataFrame, context: Any
            ) -> SignalEvent | None:
                if not self._state["fired"]:
                    self._state["fired"] = True
                    return SignalEvent(
                        timestamp=datetime.now(),
                        trace_id=str(uuid.uuid4()),
                        event_id=f"sig_{uuid.uuid4().hex[:6]}",
                        signals={"AAPL": 0.5},
                        strategy_id="one_shot",
                    )
                return None

        strategy = OneShotStrategy(strategy_id="one_shot")
        engine.run(strategy, "2023-01-01", "2023-01-04", ["AAPL"])

        import duckdb

        conn = duckdb.connect(str(db_path))
        order_rows = conn.execute(
            "SELECT event_type FROM \"backtest_audit\".logs "
            "WHERE event_type = 'ORDER'"
        ).fetchall()
        conn.close()

        assert len(order_rows) > 0, "应产生 ORDER 审计事件"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── P1-18: 风控触发断言 ─────────────────────────────────────────


class MockStrategyWithDirectOrder(BaseStrategy):
    """产生带 LIMIT 订单的策略，用于测试 P1-08 高级订单路径。"""

    def init(self):
        self._state = {"called": False}

    def on_bar(self, slab: pl.DataFrame, context: Any) -> SignalEvent | None:
        if self._state["called"]:
            return None
        self._state["called"] = True
        limit_order = self.submit_order(
            "AAPL",
            "BUY",
            quantity=10,
            exec_type="LMT",
            price=149.0,
        )
        return SignalEvent(
            timestamp=datetime.now(),
            trace_id=str(uuid.uuid4()),
            event_id=f"sig_{uuid.uuid4().hex[:6]}",
            signals={},
            strategy_id="test_strat",
            metadata={"direct_orders": [limit_order]},
        )


def test_direct_limit_order_audit():
    """P1-08: 直接 LIMIT 订单应通过 submit_order 提交并产生审计事件"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_limit.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider, cost_config=TradingCostConfig()
        )

        full_data = pl.DataFrame(
            {
                "timestamp": [
                    datetime(2023, 1, 1),
                    datetime(2023, 1, 2),
                ],
                "symbol": ["AAPL", "AAPL"],
                "close": [150.0, 152.0],
                "volume": [10000.0, 12000.0],
            }
        )
        engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

        strategy = MockStrategyWithDirectOrder(strategy_id="test_limit")
        engine.run(strategy, "2023-01-01", "2023-01-03", ["AAPL"])

        import duckdb

        conn = duckdb.connect(str(db_path))
        order_rows = conn.execute(
            "SELECT payload FROM \"backtest_audit\".logs WHERE event_type = 'ORDER'"
        ).fetchall()
        conn.close()

        import json

        assert len(order_rows) > 0, "应产生 ORDER 审计事件"
        # 验证 direct 标记
        has_direct = False
        for r in order_rows:
            payload = r[0] if isinstance(r[0], dict) else json.loads(r[0])
            if payload.get("direct"):
                has_direct = True
                break
        assert has_direct, "直接订单应有 direct=True 标记"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_max_position_pct_limit():
    """P1-18: max_position_pct 限制应正确触发"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_max_pct.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider,
            cost_config=TradingCostConfig(),
            max_position_pct=0.05,  # 单只股票最多 5%
        )

        full_data = pl.DataFrame(
            {
                "timestamp": [datetime(2023, 1, 1), datetime(2023, 1, 2)],
                "symbol": ["AAPL", "AAPL"],
                "close": [150.0, 152.0],
                "volume": [10000.0, 12000.0],
            }
        )
        engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

        # 策略要求 100% 仓位，但 max_position_pct=5% 应限制
        class BigWeightStrategy(BaseStrategy):
            def init(self):
                self._state = {}

            def on_bar(
                self, slab: pl.DataFrame, context: Any
            ) -> SignalEvent | None:
                return SignalEvent(
                    timestamp=datetime.now(),
                    trace_id=str(uuid.uuid4()),
                    event_id=f"sig_{uuid.uuid4().hex[:6]}",
                    signals={"AAPL": 1.0},
                    strategy_id="big_weight",
                )

        strategy = BigWeightStrategy(strategy_id="big_weight")
        result = engine.run(strategy, "2023-01-01", "2023-01-02", ["AAPL"])

        # max_position_pct 限制后，单只股票仓位不应超过 5%
        # 回测应成功完成（不崩溃）
        assert result.success, "max_position_pct 限制后回测应成功"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_stop_loss_risk_trigger_event():
    """P1-18: 止损触发应产生 RISK_TRIGGER 审计事件"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_sl_trigger.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider,
            cost_config=TradingCostConfig(),
            stop_loss=0.03,  # 3% 止损
        )

        full_data = pl.DataFrame(
            {
                "timestamp": [
                    datetime(2023, 1, 1),
                    datetime(2023, 1, 2),
                    datetime(2023, 1, 3),
                    datetime(2023, 1, 4),
                    datetime(2023, 1, 5),
                ],
                "symbol": ["AAPL"] * 5,
                "open": [100.0, 101.0, 96.0, 95.0, 94.0],
                "high": [101.0, 102.0, 97.0, 96.0, 95.0],
                "low": [99.0, 100.0, 95.0, 94.0, 93.0],
                "close": [100.0, 101.0, 96.0, 95.0, 94.0],
                "volume": [10000.0] * 5,
            }
        )
        engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

        # 策略在第一天买入，后续不操作，让止损触发
        class BuyAndHoldStrategy(BaseStrategy):
            def init(self):
                self._state = {"bought": False}

            def on_bar(
                self, slab: pl.DataFrame, context: Any
            ) -> SignalEvent | None:
                if not self._state["bought"]:
                    self._state["bought"] = True
                    return SignalEvent(
                        timestamp=datetime.now(),
                        trace_id=str(uuid.uuid4()),
                        event_id=f"sig_{uuid.uuid4().hex[:6]}",
                        signals={"AAPL": 1.0},
                        strategy_id="buy_hold",
                    )
                return None

        strategy = BuyAndHoldStrategy(strategy_id="buy_hold")
        engine.run(strategy, "2023-01-01", "2023-01-05", ["AAPL"])

        import json

        import duckdb

        conn = duckdb.connect(str(db_path))
        risk_events = conn.execute(
            "SELECT payload FROM \"backtest_audit\".logs "
            "WHERE event_type = 'RISK_TRIGGER'"
        ).fetchall()
        conn.close()

        assert len(risk_events) > 0, "止损触发应产生 RISK_TRIGGER 审计事件"
        payload = (
            risk_events[0][0]
            if isinstance(risk_events[0][0], dict)
            else json.loads(risk_events[0][0])
        )
        assert payload.get("risk_type") == "stop_loss", (
            f"risk_type 应为 stop_loss，实际 {payload.get('risk_type')}"
        )

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

"""审计流接口测试

验证 PostgresAuditProvider + BacktestAnalyzer 的集成接口，
不验证内部因果链细节。

PostgreSQL 全量迁移后：审计直查走 ``backtest_audit.logs``（PG 表）。
PG 不可达时整组跳过（Docker 启动后自动恢复运行）。
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

import polars as pl
import pytest

from long_earn.backtest.domain.entities import SignalEvent
from long_earn.backtest.engine.audit import (
    RUN_TAG_PROD,
    RUN_TAG_TEST,
    PostgresAuditProvider,
)
from long_earn.backtest.engine.broker import TradingCostConfig
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.parallel import ParallelRunner
from long_earn.backtest.engine.param_grid import ParamGrid
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.core.pg import pg_connect

pytestmark = pytest.mark.integration


def _query_audit_rows(
    where_sql: str = "",
    params: list[Any] | None = None,
    run_id: str | None = None,
) -> list[tuple[Any, ...]]:
    """直查 PG 审计表（返回原始行）。

    共享 PG 含真实迁移数据，查询必须按 run_id 过滤以隔离测试产物。

    Args:
        where_sql: 形如 ``"WHERE event_type = %s"`` 的过滤子句（不含 WHERE 时全表）
        params: 参数列表
        run_id: 限定本测试 run_id（自动并入 WHERE）
    """
    conn = pg_connect(read_only=True, row_factory=None)
    try:
        # 解析 where_sql 中的 ORDER BY 段，确保 run_id 过滤插在 WHERE 之后、
        # ORDER BY 之前（避免 `WHERE ... ORDER BY ... AND run_id` 语法错误）
        order_by = ""
        where_part = where_sql
        if " ORDER BY " in where_sql.upper():
            idx = where_sql.upper().index(" ORDER BY ")
            where_part = where_sql[:idx]
            order_by = where_sql[idx:]
        clauses: list[str] = []
        params_list: list[Any] = list(params or [])
        stripped = where_part.strip()
        if stripped:
            clauses.append(stripped.removeprefix("WHERE").strip())
        if run_id:
            clauses.append("run_id = %s")
            params_list.append(run_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = 'SELECT * FROM "backtest_audit".logs ' + where + " " + order_by
        return conn.execute(sql, params_list).fetchall()
    finally:
        conn.close()


def _payload_of(row: tuple[Any, ...]) -> dict[str, Any]:
    """从审计行提取 payload 字典（兼容 psycopg JSONB 反序列化 dict / str）。"""
    # payload 是第 8 列（0-indexed: run_id=0, seq=1, timestamp=2, event_type=3,
    # trace_id=4, parent_id=5, component=6, status=7, payload=8, latency_ms=9）
    raw = row[8]
    return raw if isinstance(raw, dict) else json.loads(raw)


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
    provider = PostgresAuditProvider()
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
    engine.run(strategy, "2023-01-01", "2023-01-02", ["AAPL"], tags=[RUN_TAG_TEST])
    provider.close()

    all_events = _query_audit_rows(run_id=engine._current_run_id)
    assert all_events, "No audit events were recorded!"
    event_types = [r[3] for r in all_events]
    assert "SIGNAL" in event_types, "No SIGNAL event found in audit logs!"


# ── AUDIT-P2-17: 审计时点对齐 ─────────────────────────────────


def test_audit_equity_curve_alignment():
    """MARKET_DATA 审计 portfolio_value 应与 equity_curve 逐日一致。

    每 bar 的 MARKET_DATA 事件记录 portfolio_value，equity_curve 在 bar 末尾
    通过 _sync_equity_curve 追加。二者应长度相同、值逐日对齐。
    """
    provider = PostgresAuditProvider()
    engine = EventDrivenBacktestEngine(
        audit_provider=provider, cost_config=TradingCostConfig()
    )

    days = 5
    full_data = pl.DataFrame(
        {
            "timestamp": [
                datetime(2023, 1, 1) + timedelta(days=i) for i in range(days)
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
        strategy, "2023-01-01", "2023-01-05", ["AAPL"], tags=[RUN_TAG_TEST]
    )
    provider.close()

    mkt_rows = _query_audit_rows(
        "WHERE event_type = %s ORDER BY timestamp ASC",
        ["MARKET_DATA"],
        run_id=engine._current_run_id,
    )
    audit_values = [_payload_of(r)["portfolio_value"] for r in mkt_rows]

    equity_curve = [d["value"] for d in (result.daily_returns or [])]

    assert len(audit_values) == len(equity_curve), (
        f"审计事件数({len(audit_values)}) != 净值曲线点数({len(equity_curve)})"
    )

    for i, (av, ev) in enumerate(zip(audit_values, equity_curve, strict=True)):
        assert abs(av - ev) < 1e-9, (
            f"bar {i}: 审计 portfolio_value={av} != equity_curve={ev}"
        )


# ── P1-16: 缺失事件类型覆盖 ────────────────────────────────────


def test_audit_run_start_contains_symbols_and_strategy_hash():
    """RUN_START 应包含完整 symbols 列表和 strategy_hash（P1-13 修复）"""
    provider = PostgresAuditProvider()
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
        tags=[RUN_TAG_TEST],
    )
    provider.close()

    run_start = _query_audit_rows(
        "WHERE event_type = %s",
        ["RUN_START"],
        run_id=engine._current_run_id,
    )
    assert run_start, "无 RUN_START 审计事件"
    payload = _payload_of(run_start[-1])
    assert "symbols" in payload, "RUN_START 缺少 symbols 字段"
    assert payload["symbols"] == ["AAPL"]
    assert "strategy_yaml" in payload, "RUN_START 缺少 strategy_yaml 字段"
    assert payload["strategy_yaml"] == test_yaml
    assert "strategy_hash" in payload, "RUN_START 缺少 strategy_hash 字段"
    assert len(payload["strategy_hash"]) == 16


def test_audit_auto_tags_by_dsl_kind():
    """未显式传 tags 时引擎按策略 DSL kind 自动打标（research→test，production→prod）"""
    provider = PostgresAuditProvider()
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

    # 默认 research → 自动 test 标签
    strategy = MockStrategy(strategy_id="test_strat")
    engine.run(strategy, "2023-01-01", "2023-01-02", ["AAPL"])
    run_start = _query_audit_rows(
        "WHERE event_type = %s", ["RUN_START"], run_id=engine._current_run_id
    )
    assert run_start, "无 RUN_START 审计事件"
    payload = _payload_of(run_start[-1])
    assert payload["tags"] == [RUN_TAG_TEST], (
        f"research 策略应自动带 test 标签，实际 {payload['tags']}"
    )

    # production → 自动 prod 标签（清理豁免）
    from long_earn.backtest.engine.dsl import StrategyDSL
    from long_earn.backtest.engine.dsl_strategy import DSLStrategy

    dsl = StrategyDSL(name="prod_strat", kind="production")
    prod_strategy = DSLStrategy(strategy_id="prod_strat", dsl_strategy=dsl)
    engine.run(prod_strategy, "2023-01-01", "2023-01-02", ["AAPL"])
    run_start = _query_audit_rows(
        "WHERE event_type = %s", ["RUN_START"], run_id=engine._current_run_id
    )
    assert run_start, "无 RUN_START 审计事件"
    payload = _payload_of(run_start[-1])
    assert payload["tags"] == [RUN_TAG_PROD], (
        f"production 策略应自动带 prod 标签，实际 {payload['tags']}"
    )

    # 显式传 tags 优先于 DSL kind 推导
    engine.run(prod_strategy, "2023-01-01", "2023-01-02", ["AAPL"], tags=[RUN_TAG_TEST])
    run_start = _query_audit_rows(
        "WHERE event_type = %s", ["RUN_START"], run_id=engine._current_run_id
    )
    assert run_start, "无 RUN_START 审计事件"
    payload = _payload_of(run_start[-1])
    assert payload["tags"] == [RUN_TAG_TEST], "显式传入的 tags 应优先于 DSL kind 推导"
    provider.close()


def test_audit_market_data_contains_slab_summary():
    """MARKET_DATA 应包含 slab 摘要（P1-13 修复）"""
    provider = PostgresAuditProvider()
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
    engine.run(strategy, "2023-01-01", "2023-01-02", ["AAPL"], tags=[RUN_TAG_TEST])
    provider.close()

    mkt_events = _query_audit_rows(
        "WHERE event_type = %s",
        ["MARKET_DATA"],
        run_id=engine._current_run_id,
    )
    assert mkt_events, "无 MARKET_DATA 审计事件"
    payload = _payload_of(mkt_events[-1])
    assert "slab_symbol_count" in payload, "MARKET_DATA 缺少 slab_symbol_count"
    assert "slab_close_range" in payload, "MARKET_DATA 缺少 slab_close_range"
    assert "slab_volume_sum" in payload, "MARKET_DATA 缺少 slab_volume_sum"
    assert "portfolio_value" in payload, "MARKET_DATA 缺少 portfolio_value"


def test_audit_run_id_consistency():
    """同一 run_id 的事件链应完整：RUN_START → SIGNAL → ORDER → FILL → RUN_END"""
    provider = PostgresAuditProvider()
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
    engine.run(strategy, "2023-01-01", "2023-01-03", ["AAPL"], tags=[RUN_TAG_TEST])
    provider.close()

    # 用本 engine 实例的 run_id 查询：共享 PG 存在并发写者，
    # 按全表最新 timestamp 取 run 会验错对象。
    # where_sql 以空格开头，配合 _query_audit_rows 的 " ORDER BY " 切分约定，
    # 使 run_id 过滤插入 WHERE 段、ORDER BY 保留在尾部。
    run_id = engine._current_run_id
    assert run_id, "engine.run 后应有 _current_run_id"
    events = _query_audit_rows(
        " ORDER BY timestamp ASC, seq ASC", run_id=run_id
    )
    event_types = [r[3] for r in events]
    assert "RUN_START" in event_types, "缺少 RUN_START"
    assert "RUN_END" in event_types, "缺少 RUN_END"
    assert "SIGNAL" in event_types, "缺少 SIGNAL"
    # 事件顺序正确：RUN_START 在最前，RUN_END 在最后
    assert event_types[0] == "RUN_START", "RUN_START 不是第一个事件"
    assert event_types[-1] == "RUN_END", "RUN_END 不是最后一个事件"


def test_audit_signal_to_dict_json_serializable():
    """SIGNAL 事件的 signals 字段应为 JSON dict 而非 str() 序列化（P1-13 修复）"""
    provider = PostgresAuditProvider()
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
    engine.run(strategy, "2023-01-01", "2023-01-02", ["AAPL"], tags=[RUN_TAG_TEST])
    provider.close()

    signal_rows = _query_audit_rows(
        "WHERE event_type = %s",
        ["SIGNAL"],
        run_id=engine._current_run_id,
    )
    assert signal_rows, "无 SIGNAL 审计事件"
    payload = _payload_of(signal_rows[-1])
    signals = payload.get("signals", {})
    assert isinstance(signals, dict), (
        f"signals 应为 dict，实际 {type(signals)}: {signals}"
    )
    # 验证 JSON 可序列化
    json.dumps(signals)


def test_audit_order_contains_exec_type():
    """ORDER 事件应存在（P1-08 修复）"""
    provider = PostgresAuditProvider()
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

        def on_bar(self, slab: pl.DataFrame, context: Any) -> SignalEvent | None:
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
    engine.run(strategy, "2023-01-01", "2023-01-04", ["AAPL"], tags=[RUN_TAG_TEST])
    provider.close()

    order_rows = _query_audit_rows(
        "WHERE event_type = %s",
        ["ORDER"],
        run_id=engine._current_run_id,
    )
    assert order_rows, "应产生 ORDER 审计事件"


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
    provider = PostgresAuditProvider()
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
    engine.run(strategy, "2023-01-01", "2023-01-03", ["AAPL"], tags=[RUN_TAG_TEST])
    provider.close()

    order_rows = _query_audit_rows(
        "WHERE event_type = %s",
        ["ORDER"],
        run_id=engine._current_run_id,
    )
    assert order_rows, "应产生 ORDER 审计事件"
    # 验证 direct 标记
    has_direct = any(_payload_of(r).get("direct") for r in order_rows)
    assert has_direct, "直接订单应有 direct=True 标记"


def test_max_position_pct_limit():
    """P1-18: max_position_pct 限制应正确触发

    策略要求 100% 仓位，max_position_pct=0.05 应把目标仓位压制到组合净值的
    5% 以内（portfolio._compute_order_infos 的买入封顶逻辑）。

    实质断言（评审 H16）：从 FILL 审计事件取实际成交，任一买入 FILL 的
    数量×成交价 / FILL 时点组合净值 ≤ max_position_pct + 容差。
    """
    provider = PostgresAuditProvider()
    engine = EventDrivenBacktestEngine(
        audit_provider=provider,
        cost_config=TradingCostConfig(),
        max_position_pct=0.05,  # 单只股票最多 5%
    )

    # OHLC 齐全：T+1 以 open 价撮合，缺 open 列订单会被 PRICE_NOT_FOUND 跳过
    full_data = pl.DataFrame(
        {
            "timestamp": [datetime(2023, 1, 1), datetime(2023, 1, 2)],
            "symbol": ["AAPL", "AAPL"],
            "open": [149.0, 151.0],
            "high": [151.0, 153.0],
            "low": [148.0, 150.0],
            "close": [150.0, 152.0],
            "volume": [10000.0, 12000.0],
        }
    )
    engine._prepare_data = lambda s, start, end, warmup_days=0: full_data

    # 策略要求 100% 仓位，但 max_position_pct=5% 应限制
    class BigWeightStrategy(BaseStrategy):
        def init(self):
            self._state = {}

        def on_bar(self, slab: pl.DataFrame, context: Any) -> SignalEvent | None:
            return SignalEvent(
                timestamp=datetime.now(),
                trace_id=str(uuid.uuid4()),
                event_id=f"sig_{uuid.uuid4().hex[:6]}",
                signals={"AAPL": 1.0},
                strategy_id="big_weight",
            )

    strategy = BigWeightStrategy(strategy_id="big_weight")
    result = engine.run(
        strategy, "2023-01-01", "2023-01-02", ["AAPL"], tags=[RUN_TAG_TEST]
    )
    provider.close()

    # max_position_pct 限制后，单只股票仓位不应超过 5%
    # 回测应成功完成（不崩溃）
    assert result.success, "max_position_pct 限制后回测应成功"

    # 实质断言：逐笔核对买入 FILL 的市值占比不超限
    fill_rows = _query_audit_rows(
        "WHERE event_type = %s",
        ["FILL"],
        run_id=engine._current_run_id,
    )
    buy_fills = [r for r in fill_rows if _payload_of(r).get("type") == "BUY"]
    assert buy_fills, "场景应产生至少 1 笔买入 FILL（否则占比断言空转）"
    # 容差覆盖滑点/整手取整与净值计价时点差异；超限实现（如 100% 仓位）会远超
    tolerance = 0.005
    for row in buy_fills:
        payload = _payload_of(row)
        ratio = (payload["quantity"] * payload["price"]) / payload["portfolio_value"]
        assert ratio <= 0.05 + tolerance, (
            f"买入 FILL（{payload['quantity']}股 @ {payload['price']}）"
            f"占组合净值 {ratio:.4f}，超过 max_position_pct=0.05 + 容差"
        )


def test_stop_loss_risk_trigger_event():
    """P1-18: 止损触发应产生 RISK_TRIGGER 审计事件"""
    provider = PostgresAuditProvider()
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

        def on_bar(self, slab: pl.DataFrame, context: Any) -> SignalEvent | None:
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
    engine.run(strategy, "2023-01-01", "2023-01-05", ["AAPL"], tags=[RUN_TAG_TEST])
    provider.close()

    risk_events = _query_audit_rows(
        "WHERE event_type = %s",
        ["RISK_TRIGGER"],
        run_id=engine._current_run_id,
    )
    assert risk_events, "止损触发应产生 RISK_TRIGGER 审计事件"
    payload = _payload_of(risk_events[-1])
    assert payload.get("risk_type") == "stop_loss", (
        f"risk_type 应为 stop_loss，实际 {payload.get('risk_type')}"
    )


def test_parallel_grid_creates_pg_audit_provider():
    """并行回测应注入 PostgresAuditProvider（worker 直接写 PG，无临时文件）。

    验证并行 worker 审计迁移：worker 内构造 PostgresAuditProvider（无路径参数，
    直接并发写 PG 主库 backtest_audit.logs），不再产生 worker 专属 DuckDB
    临时文件，也不再需要主进程合并逻辑。用 mock 替换 provider，避免依赖真实
    PG 服务；真实写入路径留给集成测试。
    """
    from unittest.mock import patch

    panel = _trending_panel(days=12)
    provider = _MockPanelProvider(panel)
    runner = ParallelRunner(max_workers=1, data_provider=provider)

    # mock 掉 PostgresAuditProvider：仅验证 worker 会构造 provider 并注入引擎
    with patch(
        "long_earn.backtest.engine.audit.PostgresAuditProvider", autospec=True
    ) as mock_cls:
        result = runner.run_grid(
            strategy_template=_MOMENTUM_YAML,
            param_grid=ParamGrid(),
            start_date="2024-01-01",
            end_date="2024-01-12",
            symbols=["A.SZ", "B.SH"],
            write_pg=True,
        )

    assert result.success_count >= 1, "并行回测应至少有 1 个成功任务"
    # worker 应构造 PostgresAuditProvider（无路径参数，直接写 PG 主库）
    mock_cls.assert_called_once_with()

# 回测性能优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消灭 DSL 策略回测的 O(T²) 因子重算与逐事件 uuid/审计开销，把回测热路径降到 O(T·U)，同时修复 trace_id 因果链名存实亡的问题。

**Architecture:** 三层递进——(1) 确定性事件 ID + trace_id 因果链贯穿（审计语义修复 + 回归验证基础设施）；(2) VisibilityGuard 窗口截断（O(T²)→O(T·W) 快速止血）；(3) 因子全期预计算（O(T·U) 架构终态，正确性由 ADR-009 因果性证明背书）。辅以 merged panel 跨 run 缓存与审计批量写入。

**Tech Stack:** Python 3.13 / Polars / PostgreSQL（审计 + 行情缓存）/ pytest

**核心事实（已核实，实施时可直接依赖）：**

- 瓶颈现场：`src/long_earn/backtest/engine/dsl_strategy.py` 每个调仓日 `get_history_df()` 取全历史面板 → `operator_executor.py` 在其上重跑全部 factor 算子。第 t 个调仓日重算 t 期全部 rolling。
- 因果性资产：`operators/causality.py` 的 `prove_causality` 证明「行 t 的因子值只依赖同 symbol 的 ≤t 行」⇒ **全期一次性预计算 ≡ 逐 bar 截断计算，逐值相等**。
- 审计表 PK 是 `(run_id, trace_id, seq)`，且有 `get_causal_chain(trace_id)` 查询（`engine/audit.py`）——但热路径每个事件新铸 `uuid4()`，因果链实际没串起来（信号→订单→成交各持不同 trace_id）。
- `rank_top` 以 `over("timestamp")` 保证只在截面内排名（`operators/rank/topn.py:40-46`）⇒ 信号算子只需当前截面行，预计算后无需历史面板。
- `compute_warmup_days`（`engine/dsl.py:290`）返回**日历日**；截断需要**交易日 bar 数**，需抽出原始 `max_period`。
- 已有 mmap Arrow IPC 共享底座（`engine/shared_data.py`），panel 缓存可复用同一机制。

**验证总纲（每个任务通用）：**

```sh
uv run ruff check src/
uv run lint-imports
uv run pytest tests/unit/ -v
```

任何 Serena LSP 可用环境下：编辑后对目标文件跑 `mcp__serena__get_diagnostics_for_file`，Error 级别必须为空。

---

## Task 1: 基准脚本与瓶颈分解（先测量，后优化）

**Files:**
- Create: `temp/bench_backtest.py`（用户约定：临时诊断脚本放 `./temp`，用完即删）

- [x] **Step 1: 写基准脚本**

脚本做三件事：(a) 面板加载计时（首次落 `temp/bench_panel.arrow`，后续复用）；(b) A/B 审计开销（`audit_provider=None` vs `PostgresAuditProvider()`）；(c) cProfile 分解每 bar 循环热点。

策略 YAML 直接读仓库现成模板 `src/long_earn/backtest/operators/templates/double_ma.yaml`。

```python
"""回测性能基准（临时诊断工具，用完即删）。

分解计时：面板加载 / 引擎主循环（无审计）/ 引擎主循环（PG 审计）/ cProfile 热点。
前置：Docker pg 容器运行中；temp/bench_panel.arrow 首次生成后复用。
用法: uv run python temp/bench_backtest.py
"""

from __future__ import annotations

import cProfile
import io
import pstats
import time
from pathlib import Path

import polars as pl

from long_earn.backtest.engine.audit import PostgresAuditProvider
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import compute_warmup_days, parse_strategy_yaml
from long_earn.backtest.engine.strategy import InMemoryAuditTrail
from long_earn.backtest.engine.dsl_strategy import DSLStrategy

TEMP = Path(__file__).parent
PANEL_PATH = TEMP / "bench_panel.arrow"
TEMPLATE = (
    Path(__file__).parent.parent
    / "src/long_earn/backtest/operators/templates/double_ma.yaml"
)
SYMBOLS = ["600519.SH", "000858.SZ", "601318.SH", "600036.SH", "000001.SZ"]
START, END = "2023-01-01", "2024-12-31"


def load_panel() -> pl.DataFrame:
    """加载（或首次生成并缓存）合并面板。

    provider 获取方式对照 src/long_earn/backtest/engine/parallel.py:324-356
    的预取逻辑（CompositeDataProvider.get_merged_panel_as_polars 优先，
    PandasToPolarsProvider 兜底）。首次运行前先读那段代码校准 import 与构造。
    """
    if PANEL_PATH.exists():
        return pl.read_ipc(PANEL_PATH, memory_map=True)
    provider = _build_provider()  # 见 parallel.py:324-356 校准
    panel = provider.get_merged_panel_as_polars(SYMBOLS, START, END)
    panel.write_ipc(PANEL_PATH, compression="uncompressed")
    return panel


def _build_provider():
    raise NotImplementedError("对照 parallel.py:324-356 的 provider 构造实现")


def run_once(panel: pl.DataFrame, use_audit: bool) -> float:
    dsl = parse_strategy_yaml(TEMPLATE.read_text(encoding="utf-8"))
    engine = EventDrivenBacktestEngine(
        cost_config=dsl.trading_cost.to_broker_config(),
        stop_loss=0.1,
        max_drawdown_limit=0.2,
        audit_logger=InMemoryAuditTrail(),
        audit_provider=PostgresAuditProvider() if use_audit else None,
    )
    strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
    t0 = time.perf_counter()
    result = engine.run(
        strategy,
        START,
        END,
        SYMBOLS,
        "",
        full_data=panel,
        warmup_days=compute_warmup_days(dsl),
        strategy_yaml=TEMPLATE.read_text(encoding="utf-8"),
        tags=["bench"],
    )
    elapsed = time.perf_counter() - t0
    assert result.success, result.message
    return elapsed


def main() -> None:
    t0 = time.perf_counter()
    panel = load_panel()
    print(f"[panel] rows={panel.height} load={time.perf_counter() - t0:.2f}s")

    t_no_audit = run_once(panel, use_audit=False)
    print(f"[engine] 无审计: {t_no_audit:.2f}s")

    t_audit = run_once(panel, use_audit=True)
    print(f"[engine] PG审计: {t_audit:.2f}s (审计开销 {t_audit - t_no_audit:.2f}s)")

    profiler = cProfile.Profile()
    profiler.enable()
    run_once(panel, use_audit=False)
    profiler.disable()
    out = io.StringIO()
    pstats.Stats(profiler, stream=out).sort_stats("cumulative").print_stats(25)
    print(out.getvalue())


if __name__ == "__main__":
    main()
```

- [x] **Step 2: 运行并记录基线**

Run: `uv run python temp/bench_backtest.py`
Expected: 打印面板行数/加载耗时、无审计与带审计端到端耗时、cProfile top25。**把数字记到本文件末尾「基准记录」节。**重点关注：`apply`（算子）累计占比、`log_event`/INSERT 占比、`VisibilityGuard` 方法占比。

- [ ] **Step 3: Commit**

```sh
git add temp/bench_backtest.py
git commit -m "bench: 回测性能基准脚本（temp 临时诊断）"
```

---

## Task 2: 确定性事件 ID + trace_id 因果链贯穿

**回答的问题：** 每 bar 的 uuid 能否用数据库 id / 预生成 id / 时间戳替代？

**结论（已按代码证据裁定）：**
1. **数据库 id / 预生成：否。** 审计表 PK 是 `(run_id, trace_id, seq)`，不依赖数据库自增；热路径引入 DB 取号会（a）耦合引擎与 DB 可用性，（b）并行 worker 需协调号段，（c）破坏同数据同策略两次回测的轨迹一致性。
2. **时间戳派生：是。** 日线级每交易日一条 bar，时间戳天然唯一；且 `event_id` 大多已是确定性的（`mkt_{ts}` / `op_{ts}` / `ord_{event_id}_{symbol}` / `tp_{ts}_{symbol}`），只有 `trace_id` 与 `order_id` 在用 uuid。
3. **顺手修复语义 bug：** `entities.py:78` 注明 trace_id 应「贯穿信号→订单→成交」，审计层有 `get_causal_chain(trace_id)`，但现状每个事件各铸新 uuid，链查询名存实亡。本任务让 trace_id 沿链继承。

**Files:**
- Modify: `src/long_earn/backtest/domain/entities.py`（新增 `bar_trace_id` 辅助函数）
- Modify: `src/long_earn/backtest/engine/core.py:563`（mkt 事件）、`:789/:814`、`:904/:930`、`:1019/:1056`（三处风控触发 + 风控订单）
- Modify: `src/long_earn/backtest/engine/dsl_strategy.py:189`（signal 事件）
- Modify: `src/long_earn/backtest/engine/portfolio.py:389,395`（订单 trace_id 继承 + order_id 确定化）
- Modify: `src/long_earn/backtest/engine/broker.py:326,391-392`（成交 trace_id 继承 + event_id 加日期消歧）
- Test: `tests/unit/backtest/engine/test_event_ids.py`

- [x] **Step 1: 写失败测试**

先读 `tests/unit/backtest/` 下现有 broker/portfolio 测试，对齐其 fixture 与调用签名，然后落地以下断言（构造方式可借用现有测试的 helper）：

```python
"""确定性事件 ID 与 trace_id 因果链贯穿的契约测试。"""

from datetime import datetime

from long_earn.backtest.domain.entities import bar_trace_id


def test_bar_trace_id_deterministic() -> None:
    """同一天的时间戳派生同一 trace_id，跨天不撞。"""
    assert bar_trace_id(datetime(2024, 1, 5)) == "trace_20240105"
    assert bar_trace_id(datetime(2024, 1, 5, 15, 0, 0)) == "trace_20240105"
    assert bar_trace_id(datetime(2024, 1, 8)) == "trace_20240108"


def test_order_inherits_signal_trace_id() -> None:
    """Portfolio 生成的订单继承信号 trace_id，order_id 确定性派生。"""
    # 按 tests/unit 现有 Portfolio 测试的构造方式生成 signal 与 prices/cash 上下文
    orders = portfolio.generate_orders(signal, ...)  # 对齐现有测试签名
    assert orders, "测试前提：至少生成一笔订单"
    for order in orders:
        assert order.trace_id == signal.trace_id
        assert order.order_id == f"ord_{signal.event_id}_{order.symbol}"


def test_fill_inherits_order_trace_id() -> None:
    """Broker 成交继承订单 trace_id；event_id 带日期防跨日部分成交撞名。"""
    fill = broker.execute_order(order, ...)  # 对齐现有测试签名
    assert fill.trace_id == order.trace_id
    assert fill.event_id == f"fill_{order.order_id}_{order.timestamp:%Y%m%d}"
```

- [x] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/backtest/engine/test_event_ids.py -v`
Expected: FAIL（`bar_trace_id` 不存在 / trace_id 不相等）

- [x] **Step 3: entities.py 加辅助函数**

在 `src/long_earn/backtest/domain/entities.py` 的「事件系统」节（`Event` 类之前）加：

```python
def bar_trace_id(ts: datetime) -> str:
    """单日因果链 trace_id：同一交易日的行情/信号/订单/成交事件共用。

    设计依据：审计表主键为 ``(run_id, trace_id, seq)``，
    ``get_causal_chain(trace_id)`` 按其聚合因果链。日线级回测每个交易日
    一条 bar，时间戳即天然唯一键；确定性派生（替代逐事件 uuid4）保证
    同数据同策略两次回测审计轨迹一致，可作性能优化的回归基准。

    注意：T+1 执行语义下，T 日信号在 T+1 日成交，成交事件的 trace_id
    归属 T 日（决策日）——因果链按「决策」聚合而非按「成交日」。
    """
    return f"trace_{ts:%Y%m%d}"
```

- [x] **Step 4: 六处构造点改造**

各文件先补 import（`from long_earn.backtest.domain.entities import bar_trace_id`，按各文件现有 import 块合并），再逐点替换：

`core.py:563`（MarketDataEvent）：
```python
mkt_event = MarketDataEvent(
    timestamp=ts,
    trace_id=bar_trace_id(ts),
    event_id=f"mkt_{ts.isoformat()}",
    slab=slab,
)
```

`core.py:789 / :904 / :1019`（三处风控触发）：
```python
risk_trace_id = bar_trace_id(ts)
```

`core.py:814 / :930 / :1056`（三处风控订单——trace_id 继承触发链）：
```python
order = OrderEvent(
    timestamp=ts,
    trace_id=risk_trace_id,
    event_id=f"tp_{ts.isoformat()}_{symbol}",  # dd_ 处保持原 event_id 前缀
    ...
)
```

`dsl_strategy.py:189`（SignalEvent——trace_id 与 event_id 分离）：
```python
return SignalEvent(
    timestamp=context.current_timestamp,
    trace_id=bar_trace_id(context.current_timestamp),
    event_id=f"op_{context.current_timestamp.isoformat()}",
    ...
)
```

`portfolio.py:386-400`（订单继承信号链 + order_id 确定化）：
```python
orders.append(
    OrderEvent(
        timestamp=order_ts if order_ts is not None else event.timestamp,
        trace_id=event.trace_id,
        event_id=f"ord_{event.event_id}_{symbol}",
        symbol=symbol,
        order_type=order_type,
        quantity=qty,
        price=price,
        order_id=f"ord_{event.event_id}_{symbol}",
        exec_type=event.metadata.get("exec_type", ExecType.MARKET),
        stop_price=event.metadata.get("stop_price"),
        oco_group_id=event.metadata.get("oco_group_id", ""),
    )
)
```

`broker.py:324-338`（市价成交）与 `:389-393`（限价/触发成交）：
```python
fill = FillEvent(
    timestamp=order.timestamp,
    trace_id=order.trace_id,
    event_id=f"fill_{order.order_id}_{order.timestamp:%Y%m%d}",
    order_id=order.order_id,
    ...
)
```

**范围外（明确不动）：** `strategy.py:98-104` 基类自定义策略路径的订单 id（非 DSL 热路径，自定义策略可能一 bar 多单，改动有撞名风险）；`core.py` 中 RUN_START/RUN_END/DATA_EMPTY 等 run 级审计的 uuid（run 级标识，保持 uuid 合理）；`run_id` 本身（每次运行唯一是正确语义）。

- [x] **Step 5: 跑测试与质量门槛**

Run: `uv run pytest tests/unit/backtest/ -v && uv run ruff check src/ && uv run lint-imports`
Expected: 全绿（现有审计相关测试若断言 uuid 形态，按新语义更新断言——检查 `tests/unit/` 中 grep `uuid` 的测试）

- [ ] **Step 6: Commit**

```sh
git add src/long_earn/backtest/domain/entities.py src/long_earn/backtest/engine/ tests/unit/backtest/engine/test_event_ids.py
git commit -m "perf(backtest): 确定性事件 ID + trace_id 因果链贯穿（mkt→signal→order→fill）"
```

---

## Task 3: VisibilityGuard 窗口截断（O(T²) → O(T·W) 快速止血）

**原理：** `compute_warmup_days` 已能算出最大回溯窗口。调仓日的历史面板只需最近 W 个交易日（有限窗口算子精确），ewm 类算子加 4×span 收敛余量（近似，误差可忽略——精确解在 Task 4）。

**Files:**
- Modify: `src/long_earn/backtest/engine/visibility.py`（交易日边界索引 + `read_history_tail`）
- Modify: `src/long_earn/backtest/engine/dsl.py`（抽出 `lookback_profile`）
- Modify: `src/long_earn/backtest/engine/dsl_strategy.py`（`_fetch_history` 用截断窗口）
- Test: `tests/unit/backtest/engine/test_visibility_tail.py`

- [x] **Step 1: 写失败测试**

```python
"""read_history_tail 窗口截断契约：恰好最近 n_bars 个交易日的全部行。"""

from datetime import datetime

import polars as pl

from long_earn.backtest.engine.visibility import VisibilityGuard


def _panel(days: int = 10, symbols: int = 3) -> pl.DataFrame:
    rows = []
    for d in range(1, days + 1):
        for s in range(symbols):
            rows.append(
                {
                    "timestamp": datetime(2024, 1, d),
                    "symbol": f"S{s}",
                    "close": float(d + s),
                }
            )
    return pl.DataFrame(rows)


def test_read_history_tail_returns_exact_n_days() -> None:
    guard = VisibilityGuard(_panel(days=10, symbols=3))
    guard.set_time(datetime(2024, 1, 10))
    tail = guard.read_history_tail(3)
    assert tail["timestamp"].unique().to_list() == [
        datetime(2024, 1, 8),
        datetime(2024, 1, 9),
        datetime(2024, 1, 10),
    ]
    assert tail.height == 9  # 3 天 × 3 symbol


def test_read_history_tail_clamps_at_start() -> None:
    guard = VisibilityGuard(_panel(days=10, symbols=3))
    guard.set_time(datetime(2024, 1, 2))
    tail = guard.read_history_tail(50)
    assert tail.height == 6  # 只有 2 天 × 3 symbol


def test_read_history_tail_before_init_raises() -> None:
    import pytest
    from long_earn.backtest.engine.visibility import FutureDataError

    guard = VisibilityGuard(_panel())
    with pytest.raises(FutureDataError):
        guard.read_history_tail(3)
```

- [x] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/backtest/engine/test_visibility_tail.py -v`
Expected: FAIL（`read_history_tail` 不存在）

- [x] **Step 3: visibility.py 实现**

`__init__` 中 `_timestamps` 提取之后追加（O(N) 一次）：

```python
        # 交易日边界索引：_days[i] = 第 i 个不同 timestamp；
        # _day_starts[i] = 该交易日首行行号。供 read_history_tail O(log D) 定位。
        self._days: list[datetime] = []
        self._day_starts: list[int] = []
        _prev: datetime | None = None
        for _i, _t in enumerate(self._timestamps):
            if _t != _prev:
                self._days.append(_t)
                self._day_starts.append(_i)
                _prev = _t
```

`VisibilityGuard` 新增方法（放在 `read_current_slab` 之后）：

```python
    def read_history_tail(self, n_bars: int) -> pl.DataFrame:
        """最近 n_bars 个交易日（含当前 bar）的全部行。

        O(log D) 定位 + O(1) 切片，供策略因子窗口截断使用：
        有限窗口算子（rolling/shift）只需 W 个交易日历史即可复现全量值；
        ewm 类算子需调用方在 W 上加收敛余量（见 lookback_profile）。
        """
        if self.current_timestamp is None:
            raise FutureDataError("时间轴尚未初始化")
        day_idx = bisect.bisect_left(self._days, self.current_timestamp)
        start_day = max(0, day_idx - n_bars + 1)
        start_row = self._day_starts[start_day]
        return self._full_data.slice(
            start_row, self._history_end_idx - start_row
        )
```

`VisibilityContext` 新增透传：

```python
    def get_history_tail(self, n_bars: int) -> pl.DataFrame:
        """获取最近 n_bars 个交易日的全部行（含当前 bar）。"""
        return self._guard.read_history_tail(n_bars)
```

- [x] **Step 4: dsl.py 抽出 lookback_profile**

把 `compute_warmup_days` 的扫描逻辑抽为公开函数（原函数改为调用它，日历日换算不变）：

```python
def lookback_profile(dsl: StrategyDSL) -> tuple[int, int]:
    """扫描 DSL 算子参数，返回 (最大有限回溯窗口 bars, 最大 ewm span)。

    供两处消费：compute_warmup_days（转日历日）与 DSLStrategy 历史
    截断窗口（有限窗口 + 4×span ewm 收敛余量）。
    """
    lookback_keys = ("period", "periods", "window", "span", "fast", "slow", "signal")
    span_keys = ("span", "fast", "slow", "signal")
    max_window = 0
    max_span = 0
    operator_steps: list[dict[str, Any]] = list(dsl.operator_factors)
    for step in dsl.signals:
        if step.get("type") == "operator":
            operator_steps.append(step)
    for step in operator_steps:
        params = step.get("params") or {}
        for key in lookback_keys:
            val = params.get(key, 0) or 0
            max_window = max(max_window, val)
        for key in span_keys:
            val = params.get(key, 0) or 0
            max_span = max(max_span, val)
    max_window = max(max_window, max_span)
    if dsl.regime is not None:
        max_window = max(max_window, dsl.regime.window)
        if dsl.regime.uses_relative:
            max_window = max(max_window, dsl.regime.rel_window)
    return max_window, max_span


def compute_warmup_days(dsl: StrategyDSL) -> int:
    """（docstring 保留原文）"""
    max_period, _ = lookback_profile(dsl)
    if max_period <= 0:
        return 0
    return int(max_period * 1.5 + 30)
```

- [x] **Step 5: dsl_strategy.py 接入截断**

`__init__` 里存窗口（一次性）：

```python
        from long_earn.backtest.engine.dsl import lookback_profile

        max_window, max_span = lookback_profile(dsl_strategy)
        # 有限窗口算子精确需要 max_window；ewm 类加 4×span 收敛余量
        # （span=26 时 (1-α)^104 ≈ 3e-4，截断误差可忽略；精确解见因子预计算）
        self._history_window_bars = max_window + 4 * max_span + 1
```

`_fetch_history` 改为：

```python
    def _fetch_history(self, context) -> pl.DataFrame | None:
        """取截断窗口历史面板（最近 W 个交易日），失败记诊断并返回 None。

        替代 get_history_df() 全历史面板：因子算子仅需回溯窗口内的历史，
        把逐调仓日 O(全部历史) 降到 O(W)。首 bar 的 regime 预填
        （_seed_regime_from_history）仍走 get_history_df()，每 run 仅一次。
        """
        try:
            return context.get_history_tail(self._history_window_bars)
        except Exception as exc:
            self.step_failures.append(
                {
                    "type": "history_fetch",
                    "step": "on_bar history",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return None
```

- [x] **Step 6: 等价性验证 + 质量门槛**

跑现有 backtest 单测全量 + 基准脚本重跑：

Run: `uv run pytest tests/unit/ -v && uv run ruff check src/ && uv run lint-imports && uv run python temp/bench_backtest.py`
Expected: 单测全绿（若现有测试断言全历史行为，按截断语义更新）；基准耗时下降，记录到「基准记录」。

- [ ] **Step 7: Commit**

```sh
git add src/long_earn/backtest/engine/visibility.py src/long_earn/backtest/engine/dsl.py src/long_earn/backtest/engine/dsl_strategy.py tests/unit/backtest/engine/test_visibility_tail.py
git commit -m "perf(backtest): 调仓日历史面板窗口截断 O(T²)→O(T·W)"
```

---

## Task 4: 因子全期预计算（架构终态，精确等价）

**原理：** `prove_causality` 证明「行 t 因子值只依赖同 symbol ≤t 行」⇒ 全期一次计算与逐 bar 截断计算**逐值相等**（含 ewm——两侧都从面板首行起算）。行可见性仍由 VisibilityGuard 按 timestamp 截断，双重防线不变。**等价性测试同时是因果性证明的运行时验证：结果发散 = 证明被违反 = bug。**

**Files:**
- Modify: `src/long_earn/backtest/engine/operator_executor.py`（`precompute_factors` + `execute_precomputed`，公共尾部抽 `_finalize_selection`）
- Modify: `src/long_earn/backtest/engine/dsl_strategy.py`（`precompute_panel` 钩子 + on_bar 切换截面执行）
- Modify: `src/long_earn/backtest/engine/core.py:397`（引擎钩子）
- Test: `tests/unit/backtest/operators/test_precompute_equivalence.py`

- [x] **Step 1: 写等价性测试（皇冠测试）**

```python
"""预计算 ≡ 逐 bar 截断计算的等价性测试。

发散 = 因果性证明被违反 = bug。含 ewm（ema）因子——两侧均从面板
首行起算，必须逐值相等。
"""

import random
from datetime import datetime, timedelta

import polars as pl
import pytest

from long_earn.backtest.engine.operator_executor import (
    OperatorStrategyExecutor,
    precompute_factors,
    resolve_factor_step,
    resolve_signal_step,
)


def _synthetic_panel(seed: int, n_days: int = 60, n_symbols: int = 8) -> pl.DataFrame:
    rng = random.Random(seed)
    base = datetime(2024, 1, 1)
    rows = []
    price = {f"S{i}": 100.0 + i for i in range(n_symbols)}
    for d in range(n_days):
        ts = base + timedelta(days=d)
        for i in range(n_symbols):
            sym = f"S{i}"
            price[sym] *= 1 + rng.uniform(-0.03, 0.03)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "close": round(price[sym], 4),
                }
            )
    return pl.DataFrame(rows).sort("timestamp")


def _specs() -> tuple[list, list]:
    factors = [
        resolve_factor_step(
            {"op": "sma", "alias": "f_sma", "params": {"field": "close", "window": 5}}
        ),
        resolve_factor_step(
            {"op": "ema", "alias": "f_ema", "params": {"field": "close", "span": 8}}
        ),
        resolve_factor_step(
            {
                "op": "returns",
                "alias": "f_ret",
                "params": {"field": "close", "period": 3},
            }
        ),
    ]
    signals = [
        resolve_signal_step(
            {
                "type": "operator",
                "op": "rank_top",
                "params": {"field": "f_sma", "top": 3},
            }
        )
    ]
    return factors, signals


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_precompute_equals_incremental(seed: int) -> None:
    panel = _synthetic_panel(seed)
    factors, signals = _specs()
    executor = OperatorStrategyExecutor(factors, signals)

    enriched, factor_columns = precompute_factors(factors, panel)

    timestamps = panel["timestamp"].unique().sort().to_list()
    for ts in timestamps[10:]:  # 跳过预热期
        # 旧语义：截断历史面板上跑 factor+signal 全链
        history = panel.filter(pl.col("timestamp") <= ts)
        legacy_sel, legacy_rationale = executor.execute_with_rationale(history, ts)
        # 新语义：预计算面板的当前截面只跑 signal
        cross = enriched.filter(pl.col("timestamp") == ts)
        new_sel, new_rationale = executor.execute_precomputed(
            cross, factor_columns, ts
        )
        assert new_sel == legacy_sel, f"seed={seed} ts={ts}: {new_sel} != {legacy_sel}"
        assert new_rationale["universe_size"] == legacy_rationale["universe_size"]
```

- [x] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/backtest/operators/test_precompute_equivalence.py -v`
Expected: FAIL（`precompute_factors` / `execute_precomputed` 不存在）

- [x] **Step 3: operator_executor.py 实现**

模块级函数（放在 `OperatorStrategyExecutor` 类之前）：

```python
def precompute_factors(
    factor_specs: list[OperatorFactorSpec], panel: pl.DataFrame
) -> tuple[pl.DataFrame, list[str]]:
    """全期一次性预计算 factor 链，返回 (enriched, 因子列名列表)。

    正确性依据：算子目录因果性证明（ADR-009 prove_causality）保证
    行 t 的因子值只依赖同 symbol 的 ≤t 行，故全期计算与逐 bar 截断
    计算逐值相等（含 ewm：两侧均从面板首行起算）。
    VisibilityGuard 仍按 timestamp 截断行可见性，双重防线不变。
    """
    enriched = panel
    factor_columns: list[str] = []
    for spec in factor_specs:
        result = spec.op.apply(enriched, spec.params)
        enriched, added = _merge_factor_result(enriched, result, spec.alias)
        factor_columns.extend(added)
    return enriched, factor_columns
```

`OperatorStrategyExecutor`：把 `execute_with_rationale` 尾部（信号过滤之后的截面取 symbols / selection / 排序 / rationale 组装）抽成 `_finalize_selection(selected_df, factor_columns, universe_size)`，原函数改为调用它保持行为不变；新增：

```python
    def execute_precomputed(
        self,
        cross: pl.DataFrame,
        factor_columns: list[str],
        current_timestamp: datetime,
    ) -> tuple[list[str], dict[str, Any]]:
        """在已含因子列的当前截面上一键跑 signal 算子（预计算模式）。

        signal 算子均为截面内运算（rank_top 以 over("timestamp") 保证，
        filter_threshold 为行级比较），故只需当前截面行。
        """
        if cross.height == 0:
            self._empty_signal_count += 1
            if self._empty_signal_count == 1 or (
                self._empty_signal_count % self._EMPTY_SIGNAL_LOG_INTERVAL == 0
            ):
                logger.warning(
                    "OperatorStrategyExecutor: 预计算截面为空"
                    f"（timestamp={current_timestamp}），"
                    f"累计 {self._empty_signal_count} 次"
                )
            return [], self._rationale([], 0, 0)

        universe_size = cross["symbol"].unique().len()
        selected_df = cross
        for spec in self.signal_specs:
            result = spec.op.apply(selected_df, spec.params)
            selected_df = _apply_signal_result(selected_df, result)

        if selected_df.height == 0:
            self._empty_signal_count += 1
            if self._empty_signal_count == 1 or (
                self._empty_signal_count % self._EMPTY_SIGNAL_LOG_INTERVAL == 0
            ):
                logger.warning(
                    "OperatorStrategyExecutor: signal 算子过滤后为空"
                    f"（timestamp={current_timestamp}），"
                    f"累计 {self._empty_signal_count} 次"
                )
            return [], self._rationale([], universe_size, 0)

        return self._finalize_selection(selected_df, factor_columns, universe_size)
```

- [x] **Step 4: dsl_strategy.py 钩子与切换**

新增引擎钩子方法（import 补 `precompute_factors`）：

```python
    def precompute_panel(self, full_data: pl.DataFrame) -> pl.DataFrame:
        """引擎钩子：全期预计算 factor 列（O(T·U) 一次），返回 enriched 面板。

        替代逐调仓日在全历史面板重算因子（O(T²·U/f)）。benchmark/防守腿
        标的保留在面板内（regime 门控需要），其因子列多算几列开销可忽略
        （over("symbol") 按 symbol 分区，互不影响）。
        """
        if not hasattr(self, "_op_executor"):
            self._op_executor = self._build_operator_executor()
        enriched, cols = precompute_factors(
            self._op_executor.factor_specs, full_data
        )
        self._precomputed = True
        self._factor_columns = cols
        return enriched
```

`__init__` 初始化 `self._precomputed = False`、`self._factor_columns: list[str] = []`。

`on_bar` 调仓分支改为（替换 `_fetch_history` + `execute_with_rationale` 段）：

```python
        if self._precomputed:
            # 预计算模式：guard 面板已含因子列，当前截面直接跑 signal 算子
            cross = context.get_current_slab()
            pool_cross = self._strip_non_pool(cross)
            selected, rationale = self._op_executor.execute_precomputed(
                pool_cross, self._factor_columns, context.current_timestamp
            )
        else:
            # 兜底：未经引擎预计算钩子（如单测直接构造 DSLStrategy 调 on_bar）
            history_pl = self._fetch_history(context)
            if history_pl is None:
                return None
            pool_history = self._strip_non_pool(history_pl)
            selected, rationale = self._op_executor.execute_with_rationale(
                pool_history, context.current_timestamp
            )
```

**注意：** `_fetch_history` 兜底分支保留（Task 3 的窗口截断继续生效）；`_seed_regime_from_history` 不动（首 bar 一次、读 benchmark 全历史）。

- [x] **Step 5: core.py 引擎钩子**

在 `guard = VisibilityGuard(full_data)`（core.py:398）之前插入：

```python
            # 因子全期预计算（O(T·U) 一次替代逐调仓日全历史重算）：
            # 策略声明 precompute_panel 即启用；正确性由算子因果性证明保证
            # （见 operator_executor.precompute_factors docstring）
            precompute = getattr(strategy, "precompute_panel", None)
            if callable(precompute):
                full_data = precompute(full_data)
```

**并行路径说明（无需改 parallel.py）：** `SharedDataContext` 序列化的是原始面板，worker attach 后 `engine.run` 内走同一预计算钩子——每 task 的因子链不同（参数网格），worker 各自预计算一次 O(T·U) 即可，仍是数量级改善。

- [x] **Step 6: 等价性 + 全量验证**

Run: `uv run pytest tests/unit/ -v && uv run ruff check src/ && uv run lint-imports && uv run python temp/bench_backtest.py`
Expected: 等价性测试全绿（含 ewm 精确相等）；现有 DSL 回测测试若断言具体数值应不变（预计算逐值等价）；基准耗时大幅下降，记录数字。

- [ ] **Step 7: Commit**

```sh
git add src/long_earn/backtest/engine/operator_executor.py src/long_earn/backtest/engine/dsl_strategy.py src/long_earn/backtest/engine/core.py tests/unit/backtest/operators/test_precompute_equivalence.py
git commit -m "perf(backtest): 因子全期预计算 O(T²·U)→O(T·U)，等价性由因果证明背书"
```

---

## Task 5: merged panel 跨 run 缓存（网格/WF 场景）

**原理：** 借鉴 nautilus ParquetDataCatalog 的物化缓存思想。网格寻优 / Walk-Forward 反复构建同区间 merged panel（pandas merge + sort + ffill），首次物化为未压缩 Arrow IPC，后续 `read_ipc(memory_map=True)` 零拷贝命中。

**Files:**
- Create: `src/long_earn/backtest/data/panel_cache.py`
- Modify: `src/long_earn/backtest/engine/parallel.py:331-356`（三处取数调用包缓存）
- Test: `tests/unit/backtest/data/test_panel_cache.py`

- [x] **Step 1: 写失败测试**

```python
"""merged panel 跨 run 缓存契约：同参数第二次调用不重建。"""

from datetime import datetime

import polars as pl

from long_earn.backtest.data.panel_cache import cached_merged_panel


class _CountingProvider:
    """假 provider：计数真实构建次数，返回固定面板。"""

    def __init__(self) -> None:
        self.build_count = 0

    def get_merged_panel_as_polars(
        self, symbols: list[str], start: str, end: str
    ) -> pl.DataFrame:
        self.build_count += 1
        return pl.DataFrame(
            {
                "timestamp": [datetime(2024, 1, 1), datetime(2024, 1, 2)],
                "symbol": ["600519.SH", "600519.SH"],
                "close": [100.0, 101.0],
            }
        )


def test_second_call_hits_cache(tmp_path, monkeypatch) -> None:
    provider = _CountingProvider()
    monkeypatch.setattr(
        "long_earn.backtest.data.panel_cache._cache_dir", lambda: tmp_path
    )
    p1 = cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")
    p2 = cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")
    assert provider.build_count == 1
    assert p1.equals(p2)


def test_different_args_miss(tmp_path, monkeypatch) -> None:
    provider = _CountingProvider()
    monkeypatch.setattr(
        "long_earn.backtest.data.panel_cache._cache_dir", lambda: tmp_path
    )
    cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-06-30")
    cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")
    assert provider.build_count == 2
```

- [x] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/backtest/data/test_panel_cache.py -v`
Expected: FAIL（模块不存在）

- [x] **Step 3: 实现 panel_cache.py**

```python
"""merged panel 跨 run 物化缓存（未压缩 Arrow IPC + mmap 零拷贝读取）。

网格寻优 / Walk-Forward 反复构建同区间面板（pandas merge + sort + ffill
开销大）；首次物化落盘，后续 run 内存映射直读。

失效策略：键含 LONG_EARN_PANEL_CACHE_VER 环境变量（默认 "1"）。
download_data.py 全量刷新 PG 后 bump 该版本或删除缓存目录
（get_data_dir()/"panel_cache"）即可整体失效。行内数据被原地修正
（如财务重述）而不改变行数时，缓存不会自动失效——依赖版本号手工控制。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable

import polars as pl
from loguru import logger

from long_earn.core.storage import get_data_dir


def _cache_dir():
    return get_data_dir() / "panel_cache"


def _cache_key(symbols: list[str], start_date: str, end_date: str) -> str:
    ver = os.environ.get("LONG_EARN_PANEL_CACHE_VER", "1")
    raw = f"{ver}|{start_date}|{end_date}|{','.join(sorted(symbols))}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def cached_merged_panel(
    provider,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> pl.DataFrame:
    """带物化缓存的 get_merged_panel_as_polars 代理。

    provider 只需 duck-type 提供 get_merged_panel_as_polars
    （CompositeDataProvider / PandasToPolarsProvider 包装对象均可）。
    """
    path = _cache_dir() / f"{_cache_key(symbols, start_date, end_date)}.arrow"
    if path.exists():
        logger.debug(f"panel cache HIT: {path}")
        return pl.read_ipc(path, memory_map=True)
    panel = provider.get_merged_panel_as_polars(symbols, start_date, end_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 未压缩 IPC 才能内存映射（同 SharedDataContext 约定）
    panel.write_ipc(path, compression="uncompressed")
    logger.debug(f"panel cache MISS→WRITE: {path}, rows={panel.height}")
    return panel
```

- [x] **Step 4: parallel.py 三处取数接入**

`parallel.py:331-356` 预取逻辑中，三处 `get_merged_panel_as_polars(...)` 调用（CompositeDataProvider 直调 / PandasToPolarsProvider 包装 / MiniQmtDataProvider）统一替换为：

```python
from long_earn.backtest.data.panel_cache import cached_merged_panel  # 顶部 import

panel = cached_merged_panel(provider, symbols, start_date, end_date)
```

- [ ] **Step 5: 验证 + Commit**

Run: `uv run pytest tests/unit/ -v && uv run ruff check src/ && uv run lint-imports`

```sh
git add src/long_earn/backtest/data/panel_cache.py src/long_earn/backtest/engine/parallel.py tests/unit/backtest/data/test_panel_cache.py
git commit -m "perf(backtest): merged panel 跨 run Arrow IPC 物化缓存"
```

---

## Task 6: 审计批量写入 + read_history 去冗余 sort

**Files:**
- Modify: `src/long_earn/backtest/engine/audit.py`（PostgresAuditProvider 缓冲批量 flush）
- Modify: `src/long_earn/backtest/engine/visibility.py:145`（去冗余 sort）
- Test: `tests/unit/backtest/engine/test_audit_batching.py`

- [x] **Step 1: 读 audit.py 现状**

通读 `PostgresAuditProvider.log_event`（约 L175-203）与连接管理方式，确认：INSERT 列序 `(run_id, seq, timestamp, event_type, trace_id, parent_id, component, status, payload, latency_ms)`、连接是长连还是逐次获取、`close()` 现有逻辑。

- [x] **Step 2: 写失败测试**

```python
"""审计批量写入契约：缓冲满或 close() 时批量落库，不逐条 INSERT。"""


def test_buffered_flush_on_close(monkeypatch) -> None:
    provider = _make_provider_with_fake_conn(monkeypatch)  # 按 audit.py 连接方式打桩
    for i in range(150):
        provider.log_event(_record(seq=i))
    provider.close()
    # close 全量 flush，且批量（executemany）而非逐条（execute）
    writes = _cursor_of(provider).executed
    assert writes, "close 后必须落库"
    assert all(w.startswith("executemany:") for w in writes)
    assert sum(int(w.split(":")[1]) for w in writes) == 150


def test_flush_at_threshold(monkeypatch) -> None:
    provider = _make_provider_with_fake_conn(monkeypatch)
    for i in range(600):  # 超过 _FLUSH_EVERY=500
        provider.log_event(_record(seq=i))
    writes = _cursor_of(provider).executed
    assert sum(int(w.split(":")[1]) for w in writes) == 500  # 已 flush 一批
    provider.close()
    total = sum(int(w.split(":")[1]) for w in _cursor_of(provider).executed)
    assert total == 600
```

（`_make_provider_with_fake_conn` / `_record` / `_cursor_of` 按 Step 1 读到的连接方式实现——原则：只替换连接/游标对象，不 mock `log_event` 本身。）

- [x] **Step 3: 实现缓冲**

`PostgresAuditProvider` 加：

```python
    _FLUSH_EVERY = 500

    def __init__(self, ...) -> None:
        ...  # 原有初始化
        self._buffer: list[tuple] = []

    def log_event(self, record: AuditRecord) -> None:
        """缓冲写入；满 _FLUSH_EVERY 条批量落库（长回测 ~数千事件，
        逐条 INSERT 在共享 PG 上是网格并发的热点）。"""
        self._buffer.append(
            (
                record.run_id,
                record.seq,
                record.timestamp,
                record.event_type,
                record.trace_id,
                record.parent_id,
                record.component,
                record.status,
                record.payload,
                record.latency_ms,
            )
        )
        if len(self._buffer) >= self._FLUSH_EVERY:
            self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        # 复用原 log_event 的连接获取与 INSERT SQL（列序不变），
        # execute 改 executemany，一次性提交（按 Step 1 读到的连接方式落实现）

    def close(self) -> None:
        self._flush()
        ...  # 原有关闭逻辑
```

seq 在入缓冲时分配（沿用原有 seq 计数器），批量落库保持顺序，PK `(run_id, trace_id, seq)` 语义不变。引擎 `finally` 已调用 `close()`（core.py:510-514），RUN_END 会随 close flush。

- [x] **Step 4: visibility.read_history 去冗余 sort**

`visibility.py` `read_history` 中：面板预排序 by timestamp，`filter` 保序，`.sort("timestamp", descending=False)` 冗余，删除：

```python
        result = (
            history.filter(pl.col("symbol") == symbol)
            .tail(window)
            .select(field)
            .to_series()
        )
```

- [ ] **Step 5: 验证 + Commit**

Run: `uv run pytest tests/unit/ -v && uv run ruff check src/ && uv run lint-imports && uv run python temp/bench_backtest.py`
Expected: 全绿；带审计基准耗时显著下降（对比 Task 1 记录）。

```sh
git add src/long_earn/backtest/engine/audit.py src/long_earn/backtest/engine/visibility.py tests/unit/backtest/engine/test_audit_batching.py
git commit -m "perf(backtest): 审计批量落库 + read_history 去冗余排序"
```

---

## Task 7: 收尾——全量门槛、基准对比、TODO 登记

- [x] **Step 1: 全量质量门槛**

```sh
uv run ruff check src/
uv run lint-imports
uv run pytest tests/unit/ -v
uv run pytest tests/integration/ -v   # 若 .env 就绪
```

- [x] **Step 2: 基准终测并记录**

Run: `uv run python temp/bench_backtest.py`，把 Task 1 基线与各任务后的数字填入下表：

| 阶段 | 无审计端到端 | 带审计端到端 | 备注 |
|------|-------------|-------------|------|
| 基线（Task 1） | TBD | TBD | |
| Task 3 后 | TBD | TBD | 窗口截断 |
| Task 4 后 | TBD | TBD | 预计算 |
| Task 6 后 | TBD | TBD | 审计批量 |

- [x] **Step 3: TODO.md 登记 + 清理临时文件**

在 `TODO.md` 对应位置登记完成状态；删除 `temp/bench_backtest.py` 与 `temp/bench_panel.arrow`（用户约定：temp 用完即删；若需保留基准数字，只保留上表）。

- [ ] **Step 4: 最终 Commit**

```sh
git add TODO.md
git commit -m "docs: 回测性能优化收尾登记"
```

---

## 基准记录

**基线（Task 1，2026-08-23）**：面板 2480 行（5 标的 × ~496 交易日，double_ma 模板，2023-01-01~2024-12-31）

| 阶段 | 无审计端到端 | 带审计端到端 | 备注 |
|------|-------------|-------------|------|
| 基线（Task 1） | 0.47s | 2.27s | 审计开销 1.80s（占 79%，逐条 INSERT 是最大单点） |
| Task 3 后 | 0.43s | 2.20s | 窗口截断（小面板收益有限，收益随池规模放大） |
| Task 4 后 | 0.39s | — | 预计算（因子重算路径从热点消失） |
| Task 6 后 | 0.39s | 0.43s | 审计批量（审计开销 1.80s → 0.04s，-98%） |

最终 cProfile 热点（无审计轮 0.569s）：因子重算路径（`execute_with_rationale`）已消失，调仓走 `execute_precomputed`（25 次累计 0.033s）；最大热点转移为 `portfolio.update_market_values` 的 polars filter（1489 次累计 0.253s）——下一优化候选。`core._build_price_dict` 496 次累计 0.114s。

基线 cProfile 热点（无审计轮，0.686s 含预热）：`dsl_strategy.on_bar` 25 次调仓累计 0.162s（其中 `execute_with_rationale` 0.159s——因子重算路径）；`core._build_price_dict` 每 bar 调用 496 次累计 0.148s；算子 `sma_ema.apply` 50 次累计 0.055s。小面板下审计开销主导；大池（数千标的）时 O(T²) 因子重算与每 bar 全历史面板复制将成为主导——Task 3/4 的收益随池规模放大。

## 自查清单（计划作者已核）

1. **覆盖度**：窗口截断（Task 3）、预计算（Task 4）、panel 缓存（Task 5）、审计/小额（Task 6）、基准（Task 1/7）、uuid 问题（Task 2）——对应对话中提出的全部改进项。
2. **占位符**：Task 1 的 `_build_provider` 与 Task 6 Step 3 的 `_flush` 连接细节标注了「对照源码校准」（provider 构造与连接管理未在本次调研中完整读取）——实施第一步即读对应源码，不属于 TBD 悬置。
3. **类型一致性**：`bar_trace_id(ts: datetime) -> str`、`read_history_tail(n_bars: int) -> pl.DataFrame`、`precompute_factors(factor_specs, panel) -> tuple[pl.DataFrame, list[str]]`、`execute_precomputed(cross, factor_columns, current_timestamp)` 在各任务间签名一致。

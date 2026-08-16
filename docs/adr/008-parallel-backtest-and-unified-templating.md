# ADR-008: 并行回测编排 + 参数网格

日期: 2026-06
状态: Partially Superseded（A 部分模板渲染层已被 [ADR-011](011-unified-mustache-prompt-templating.md) 废弃；B 部分并行回测编排继续有效，含 2026-08 增补 B5/B6，Implemented）

## 背景

回测引擎（ADR-005）为事件驱动单进程串行循环，多核无法利用；walk_forward 各折与参数网格寻优天然可并行。约束：xtquant C++ 端不可重入（worker 内必须禁用下载）；引擎内部可变状态（audit trail / VisibilityGuard 缓存 / strategy / broker）每个并行回测必须独立实例。

## 决策

### A. 统一模板渲染层（已废弃）

A 部分（`${var}` 语法 + 自研 `core/render.py` 纯函数渲染器）已被 **ADR-011 废弃**：统一改用 LangChain `PromptTemplate(template_format='jinja2')`（`{{ var }}`），自研渲染层删除，CI grep 卡口（`scripts/check_deprecated_syntax.py`）防回退。

### B. 并行回测编排层

- **B1 引擎微改**：`run(full_data=...)` 支持注入预取面板（跳过取数，含防御性日期过滤）；`__init__` 支持注入独立 audit 实例；`walk_forward_run` 各折复用同一份面板。
- **B2 共享数据底座**：`SharedDataContext` 用 `multiprocessing.shared_memory` + Arrow IPC 零拷贝分发 DataFrame；主进程持句柄，全部 worker 完成后 `close()+unlink()`（try/finally + atexit 兜底）；SharedMemory 不可用时 pickle fallback。内存占用 = 1 份数据。
- **B3 并行编排**（`engine/parallel.py`）：`ParallelRunner` + `BacktestTask`/`BacktestOutcome`（可 pickle）；worker 入口强制 `LONG_EARN_DISABLE_XTQUANT=1`、独立构造引擎/策略/broker；`max_workers=1` 退化为顺序（CI）；默认上限 256 组合（超出需 `allow_large_grid`）；单 task 异常转 `success=False` 不拖垮整批。
- **B4 服务薄封装**：`BacktestService.run_grid` / `run_walk_forward_parallel` / `run_candidates`（均接受 `tags` 透传到审计 RUN_START，测试/冒烟 run 必须带 `test` 标签，见 AGENTS.md）。
- **参数网格**（`engine/param_grid.py`）：`ParamGrid`（笛卡尔积或显式组合）+ `render_template`（标量插值，渲染引擎已随 ADR-011 切 jinja2）+ `apply_struct_params`（解析后 DSL 对象上做字段变换）。**标量文本插值与对象层变换职责分离**，避免把列表/嵌套对象塞进文本插值的转义地狱。

### B5. warmup 注入契约（2026-08 增补）

并行路径曾不传 `warmup_days`，时序因子前若干 bar 全 NaN（ADR-013 T6）。契约：

1. `BacktestTask.warmup_days` 字段，`_run_one_backtest` 透传 `engine.run(..., warmup_days=...)`。
2. **每个 task 独立算 warmup**（`compute_warmup_days(dsl)`）：grid 对每个 combo 算（struct_params 可改回溯窗口）；walk_forward 同策略算一次；candidates 各候选独立算。
3. 主进程预取 `[start - max_warmup, end]`，worker 内按 `[start - warmup_days, end]` 过滤面板（filter 非再取数），交易循环严格限 `[start, end]`——warmup 段只进 VisibilityGuard history 不产生交易，PIT 不变。

### B6. diagnostics 保真约束（2026-08 增补）

`BacktestOutcome` 完整保留 `degenerate` / `step_failures` / `factor_failures`——`AcceptanceGate` 依赖 `degenerate` 做退化判定，降级会让退化策略（trade_count=0）靠 sharpe 数值混入 OOS 门浪费 held-out 测试集。**等价性硬约束**：同一策略 YAML，串行 `run` 与批量 `run_candidates` 的核心指标（sharpe/return/drawdown）与 diagnostics 必须一致（浮点容差内），由等价性测试覆盖。

## 后果

- Windows spawn 下 SharedMemory 需兜底 unlink 防泄漏；worker 严禁 import `backtest.data`（数据只在主进程预取）。
- HTR/ResearchAgent 候选批量回测（ADR-010 / ADR-018）复用 `run_candidates`，受 B5/B6 约束。
- ADR-011 后 `render_independent` 合约失效删除。

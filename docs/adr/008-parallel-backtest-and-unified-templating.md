---
id: 8
title: 并行回测编排与参数网格
status: Accepted
date: 2026-06
summary: ProcessPool 与共享面板并行编排；B5/B6 为硬性约束。模板渲染层已由 ADR-011 取代。
related: ["ADR-011"]
---

# ADR-008: 并行回测编排与参数网格


## 背景

回测引擎（ADR-005）采用事件驱动单进程串行循环，多核资源无法利用；Walk-Forward 各折与参数网格寻优天然可并行。约束条件：xtquant C++ 端不可重入（worker 内必须禁用下载）；引擎内部可变状态（audit trail、VisibilityGuard 缓存、strategy、broker）在每个并行回测中须为独立实例。

## 决策

### A. 统一模板渲染层（已废弃）

A 部分（`${var}` 语法与自研 `core/render.py` 纯函数渲染器）已被 ADR-011 取代：统一改用 LangChain `PromptTemplate(template_format='jinja2')`（`{{ var }}`），自研渲染层删除；CI 静态检查（`scripts/check_deprecated_syntax.py`）防止回退。

### B. 并行回测编排层

- **B1 引擎微改**：`run(full_data=...)` 支持注入预取面板（跳过取数，含防御性日期过滤）；`__init__` 支持注入独立 audit 实例；`walk_forward_run` 各折复用同一份面板。
- **B2 共享数据基础层**：`SharedDataContext` 使用 `multiprocessing.shared_memory` 与 Arrow IPC 零拷贝分发 DataFrame；主进程持句柄，全部 worker 完成后 `close()` + `unlink()`（try/finally 与 atexit 最终清理）；SharedMemory 不可用时采用 pickle 回退。内存占用等于 1 份数据。
- **B3 并行编排**（`engine/parallel.py`）：`ParallelRunner` 与 `BacktestTask`/`BacktestOutcome`（可 pickle）；worker 入口强制 `LONG_EARN_DISABLE_XTQUANT=1`，独立构造引擎/策略/broker；`max_workers=1` 退化为顺序执行（CI）；默认上限 256 组合（超出须 `allow_large_grid`）；单 task 异常转为 `success=False`，不拖垮整批。
- **B4 服务薄封装**：`BacktestService.run_grid` / `run_walk_forward_parallel` / `run_candidates`（均接受 `tags` 透传至审计 RUN_START；测试/冒烟 run 必须携带 `test` 标签，见 AGENTS.md）。
- **参数网格**（`engine/param_grid.py`）：`ParamGrid`（笛卡尔积或显式组合）+ `render_template`（标量插值，渲染引擎已随 ADR-011 切换至 jinja2）+ `apply_struct_params`（在解析后 DSL 对象上执行字段变换）。标量文本插值与对象层变换职责分离，避免将列表/嵌套对象塞入文本插值引发转义问题。

### B5. warmup 注入契约

并行路径曾不传 `warmup_days`，导致时序因子前若干 bar 全为 NaN（ADR-013 T6）。契约如下：

1. `BacktestTask.warmup_days` 字段，`_run_one_backtest` 透传 `engine.run(..., warmup_days=...)`。
2. 每个 task 独立计算 warmup（`compute_warmup_days(dsl)`）：grid 对每个 combo 计算（struct_params 可改回溯窗口）；walk_forward 同策略计算一次；candidates 各候选独立计算。
3. 主进程预取 `[start - max_warmup, end]`，worker 内按 `[start - warmup_days, end]` 过滤面板（filter 非再取数），交易循环严格限于 `[start, end]`——warmup 段仅进入 VisibilityGuard history，不产生交易，PIT 不变。

### B6. diagnostics 保真约束

`BacktestOutcome` 完整保留 `degenerate` / `step_failures` / `factor_failures`——`AcceptanceGate` 依赖 `degenerate` 执行退化判定；若 diagnostics 字段被裁减，退化策略（trade_count=0）可能凭 sharpe 数值混入 OOS 门，浪费 held-out 测试集。**等价性硬性约束**：同一策略 YAML，串行 `run` 与批量 `run_candidates` 的核心指标（sharpe/return/drawdown）与 diagnostics 须一致（浮点容差内），由等价性测试覆盖。

## 后果

**正面**

- 多核 CPU 资源可用于 Walk-Forward 与参数网格寻优，显著提升批量回测吞吐。
- 共享数据基础层避免 worker 重复取数，内存占用等于 1 份面板。
- B5/B6 契约保证 warmup 正确性与 diagnostics 保真，防止退化策略误判。

**负面**

- Windows spawn 模式下 SharedMemory 须以 unlink 最终清理，否则可能泄漏。
- worker 严禁 import `backtest.data`（数据仅在主进程预取），架构约束增加。
- ADR-011 之后 `render_independent` 合约失效并删除。

**中性**

- ResearchAgent 候选批量回测（ADR-018）复用 `run_candidates`，受 B5/B6 约束。
- A 部分由 ADR-011 Superseded；B 部分继续有效。
- 具体实现细节以源码为准。

## 关联

- Supersedes（A 部分）: ADR-011
- 依赖: ADR-005（事件驱动回测）、ADR-013（T6 warmup 陷阱）

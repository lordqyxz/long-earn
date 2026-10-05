---
id: 5
title: 事件驱动回测框架
status: Superseded
superseded_by: "long-earn-engine ADR-001（docs/adr/001-event-sourcing-and-component-contracts.md）"
date: 2024-05
summary: 以事件驱动替代向量化回测；可信性优先于表达力与性能。
---

# ADR-005: 事件驱动回测框架

> **处置（M5 划线，2026-10-06）：Superseded —— 实现语言变更（Python → Rust），事件溯源为本 ADR 的结构性强化。**
> **为什么**：M3.5 已取消「旧项目消费 Rust 引擎」的双引擎并行路径，引擎成为新项目专属确定性引擎，仅由新仓经 CLI 子进程调用；本仓库归档冻结，回测结果的唯一产出路径移出本仓。
> **新实现在哪**：引擎仓 `d:/dev/long-earn-engine`——内核 `src/engine/kernel.rs`、DSL 编译 `src/dsl/compile.rs`、四组件 `src/components/`（`Strategy` / `FillModel` / `RiskManager` / `Portfolio`）、面板加载 `src/panel/loader.rs`（Arrow IPC）、指标 `src/metrics.rs`。调用形态：`long-earn-engine.exe run --strategy <YAML> --panel <Arrow IPC> --out <DIR>`（退出码 0 成功 / 1 内部错误 / 2 契约失败 / 3 数据失败，见 `src/main.rs`）；TS 侧调用桥 `packages/server/src/engine/{cli,outputs,bridge}.ts`。
> **延续了什么**：事件驱动优于向量化的价值序（可信性 > 表达力 > 性能）、「禁止策略在时刻 T 访问 T 及之后的数据」、状态化策略（`on_bar` / `on_event`）与撮合经纪（滑点、佣金、涨跌停）语义全部延续；组件划分亦一一对应旧实现。
> **结构性强化**：审计链由**旁路观测面**升格为**引擎输出本体**——`events.jsonl` 即全部输出，持仓 / 现金 / 风控状态均由事件序列重放可得，引擎不得持有任何「不进事件流」的可变状态（引擎 ADR-001 §决策 1）。旧实现的「审计写入失败即丢事件、缓冲区截断」缺口不复存在，「每一步状态可追溯、可重放」由旁路保证升为结构保证。
> **变了什么**：实现语言与进程边界（进程内 Python → 独立 Rust CLI 子进程）；本仓 `src/long_earn/backtest/engine/` 保留为**黄金语料基准**（L1 事件流 / L2 不变量 / L3 分层 checkpoint 比对），不再是结果产出路径。

## 背景

向量化回测（Pandas MultiIndex + 表达式求值）在简单因子策略上吞吐高，但存在三类局限：

1. **可信性**：复杂窗口与自定义逻辑易引入前视偏差（look-ahead bias）；
2. **表达力**：难以实现依赖路径的状态机（条件序列、动态止损/止盈）；
3. **执行真实性**：以权重变化近似成本，难以模拟订单生命周期与部分成交。

## 决策

我们将回测架构转向 **事件驱动（Event-Driven）**，优先级为：

1. **可信性**：禁止策略在时刻 \(T\) 访问 \(T\) 及之后的数据；以事件流推进时间线；
2. **表达力**：允许策略持有内部状态，支持事件触发逻辑与订单生命周期控制；
3. **性能**：次要目标；接受逐步迭代开销，必要时在单时间步内做局部向量化。

主要组件：事件循环、数据喂入（可见性受控）、状态化策略（`on_bar` / `on_event`）、组合与资金、撮合经纪（滑点与佣金）。

实现细节以 `src/long_earn/backtest/engine/` 与 [architecture.md](../architecture.md) 为准。

## 后果

- **正面**：金融可信性成为架构级约束；复杂风控与状态逻辑可表达。
- **负面**：相对纯向量化吞吐下降；YAML DSL 与策略接口须支持状态化语义。
- **中性**：须以事件序列回归测试守护收益与成交语义；后续并行编排见 ADR-008，算子路径见 ADR-009。

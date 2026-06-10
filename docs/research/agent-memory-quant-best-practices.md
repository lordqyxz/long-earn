# 量化交易 + LLM Agent 系统最佳实践调研 (2024-2025)

日期: 2026-05-12

## 1. LLM Agent 记忆架构

占主导地位的是 **分级记忆** (Letta/MemGPT, 2023年提出，2024年稳定)：

| 层级 | 生命周期 | 用途 |
|---|---|---|
| Working | 会话级 | 当前推理上下文(等同于对话历史) |
| Core | 持久 | 已学习的事实、策略规则、用户偏好 |
| Archival | 卸载 | 历史经验、旧回测运行、过往错误 |

Agent 自主编辑这些层级：将相关 Archival 记录提升到 Core，清理过时事实，在上下文相关时将 Core 记忆注入 Working 上下文。

**关键洞察**：不是原始向量搜索，而是增加 **反思步骤 (reflection step)**，Agent 显式决定记住什么以及放在哪个层级。

参考：
- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)
- [Letta: Memory Management for LLM Agents](https://www.letta.com/)

## 2. 量化交易领域服务结构

生产系统一致遵循分层流水线，每层是独立的服务接口：

```
DataProvider → FactorEngine → SignalGenerator → RiskManager → PortfolioOptimizer → Execution
```

关键架构洞察：

- **FactorEngine** 和 **SignalGenerator** 是不同的关注点。Factor 计算原始值(如 `rank(roe)`)，Signal 组合它们(加权和、ML模型)。解耦允许因子跨策略复用。
- **RiskManager** 管控每个订单。它不是事后检查，而是 Signal 和 Execution 之间的必经关卡。模式是 `Signal → RiskManager.check() → Order`。
- **回测引擎** 必须与实盘共享完全相同的接口 — 仅替换 `PaperBrokerAdapter` 为 `LiveBrokerAdapter`。当前 `backtest/engine/core.py` 已经遵循此原则。
- **CQRS-lite**：策略产生信号(命令/领域事件)，分析查询组合状态(查询)。分离读写路径。

参考实现：
- [QuantTradingOS/qtos-core](https://github.com/QuantTradingOS/qtos-core)
- [QuantFlow: Event-Driven Framework](https://dev.to/iwtxokhtd83/building-quantflow)
- [CanonicalFlow/core](https://github.com/CanonicalFlow/core)
- [Alpha Factory](https://github.com/ywuwuwu/Alpha-Factory)

## 3. 现代 Agent 设计模式

LLM Agent 的标准演进路径：

1. **ReAct** (Thought-Action-Observation 循环) — 任何 tool-using agent 的基线
2. **Reflection** (Generate-Critique-Revise) — ROI 最高的升级；2-3 轮是最佳点
3. **Plan-and-Execute** — 适合 10+ 步骤任务，ReAct 会迷失目标
4. **Multi-Agent Orchestration** — 并行专家 + 协调者

当前 `strategy_rd` 子图已经实现了大部分：Reflection(代码修复循环)、Plan-and-Execute(research-plan-develop-backtest)、Multi-Agent(researcher + developer + supervisor)。

**差距**：需要在每个回测循环后增加结构化记忆反思 — Agent 显式反思"我学到了什么？这应该成为持久规则还是一次性观察？"

参考：
- [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366)
- [Agentic Design Patterns](https://self.md/concepts/agentic-design-patterns/)
- [Alpha Agent Pool — FinAgent Orchestration](https://finagent-orchestration.readthedocs.io/)

## 4. Python 金融系统 Clean Architecture

三个参考实现 (QuantTradingOS, QuantFlow, CanonicalFlow) 一致采纳：

- **Hexagonal/Ports-and-Adapters**: Core domain (strategy, portfolio, risk) 定义接口；infrastructure (brokers, data feeds, caches) 实现它们。领域代码零 infrastructure 导入。
- **Event-Driven Core**: 行情数据作为事件到达，策略发出信号，订单是领域事件。确定性事件排序实现可重现回测。
- **Domain Layer Purity**: Strategy 是纯函数 `(Bar, Portfolio) → Signal`，无副作用。
- **DuckDB/SQLite 作为本地优先缓存** (已实现)，配合云对象存储做归档。

## 5. 本项目适配建议 (Top-5)

1. **3-tier 记忆系统替代 Qdrant**: Working/Core/Archival 分级
2. **拆分 BacktestService 为独立服务**: FactorService, SignalService, RiskService
3. **增加 ReflectNode**: 每个 strategy_rd 循环后整合学习
4. **强制领域层零 infrastructure 导入**: `backtest/domain/` 不依赖 `data/` 或 `engine/`
5. **定义 BrokerAdapter Protocol**: 为未来实盘兼容做准备

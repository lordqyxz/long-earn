# ADR-009: 算子目录 + 算子研发子图（替代自由表达式 DSL）

日期: 2026-06
状态: Accepted, Implemented

## 背景

YAML DSL 曾通过 `SafeExpressionEvaluator`（ADR-003，287 行手写 AST 解释器）执行自由表达式字符串，暴露五个结构性痛点：evaluator 执行与字段校验双重事实源需手工同步；自研 mini-language 维护成本高于收益；refine 循环的存在本身说明表达式 DSL 对 LLM 不友好；每步可能静默退化导致诊断逻辑膨胀；DSL 声明式与 Python 命令式策略双套割裂。

## 决策

采用**类型化算子目录 + 算子研发子图**，全部策略计算走「算子名 + 参数」。

### A. 算子目录（`backtest/operators/`）

- DSL 由自由表达式改为算子引用（`op` + `alias` + `params`），参数由算子 `params_cls`（Pydantic）校验——未知 op / 参数错误在**解析期**抛错，错误根本进不到回测，refine 循环基本退役。
- `Operator` 基类契约：`name`/`category`/`inputs`/`params_cls` + `apply(panel, params)`；`@operator` 装饰器注册 `OPERATOR_REGISTRY`；加载时 `validate_contract` 校验。
- 约定目录自动扫描（`_loader.py`）：**必须 dotted 路径 import**（否则算子类双重身份，isinstance/Pydantic 校验失效）；算子名全局唯一，冲突启动即抛错（静默覆盖最危险）。
- **因果性证明器**（`causality.py`）：`prove_causality` 基于因果性的操作定义做「未来扰动不变性」数值验证——扰动 `timestamp > T` 的数据，断言 `<= T` 输出逐元素不变。数学性质而非经验拟合，作为算子上线硬约束。

### B. 策略执行路径

`OperatorStrategyExecutor` 在 VisibilityGuard 保证的历史面板上依次跑 factor 算子（结果列并回面板）-> signal 算子（filter/rank 行选择）-> 取当前时刻截面选股。旧表达式路径已删除（2026-07）：DSL 解析期强制拒绝 `factors` 字段与 `filter`/`rank`/`expression` 旧信号类型；`ml_strategy.py`/`strategy_templates.py` 双套体系一并清理，内置策略改算子 DSL YAML 模板（`operators/templates/`）。

### C. 算子研发子图（operator_dev）

异步闭环：pick_task（backlog 按 priority 取）-> spec_review（去重/合理性/补参数 schema/校验 reference_strategy）-> implement（LLM 产算子源码）-> test_validate（**AST 允许列表审计**：只许 polars/numpy/math/long_earn.backtest.*；契约校验；**prove_causality**）-> 失败 refine（≤3 轮）-> 预算用尽 mark_blocked；通过则 register（写 `.py` 到代码库 + 内存热注册）。

关键性质：策略研发**永不阻塞**等算子开发（缺口写 backlog 就继续）；LLM 代码执行风险收敛在这一个可审查、可关停的子图内，策略研发主链路**零 LLM 代码执行**；注册后的算子与人工写的无区别（同过 CI/因果证明）；策略研发 reflection 后的 `gap_detector` 产 OperatorSpec 写 backlog，形成「假设驱动算子研发」闭环。

### D. 策略优化模块（strategy_optimization）

`StrategyOptimizer` 协议（LLM 委托 research_agent / Fake 测试实现）+ `AcceptanceGate`（验收主判据 **sharpe 严格提升**——风险调整后收益而非裸收益，防「高收益高波动」劣化误判）+ `OptimizationPipeline`（optimize -> backtest -> accept + lineage 谱系）。

## 后果

- 单一事实源：下线算子 = 删文件，无第二个清单同步。三层无未来函数保证：每算子因果证明 + 算子 DSL 执行路径未来扰动证明（真实引擎 e2e）+ 解析期白名单。
- ADR-003 Superseded（evaluator 已删）；import-linter 新增 `operators_independent` 合约。

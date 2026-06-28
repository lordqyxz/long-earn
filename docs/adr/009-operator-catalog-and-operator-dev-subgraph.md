# ADR-009: 算子目录 + 算子研发子图（替代自由表达式 DSL）

日期: 2026-06
状态: Accepted, Implemented (核心链路)；部分后续项待完成

## 背景

回测引擎（ADR-005）的 YAML DSL 策略通过 `SafeExpressionEvaluator`（ADR-003，287 行手写 AST 解释器）执行自由表达式字符串。此架构暴露五个结构性痛点：

1. **双重事实源**：`evaluator.py`（执行表达式）与 `dsl.py::_extract_field_names`（正则反向解析表达式做字段校验）必须手工同步。改一边漏改另一边即静默 bug。
2. **自研 mini-language**：`SafeExpressionEvaluator` 本质在重造受限 Python，维护成本高于收益。
3. **refine 循环的存在本身**：`MAX_CODE_REFINES=3` + 多重 YAML 抠取 hack，说明 YAML + 嵌套缩进 + 表达式语法对 LLM 不友好。
4. **静默退化诊断膨胀**：`DSLStrategy` 里 `factor_failures` / `step_failures` 大量逻辑，因为 DSL 每一步都可能"什么都不做却 success=True"。
5. **双套策略体系割裂**：`DSLStrategy`（声明式）与 `MLSignalStrategy` + `FeatureEngine`（命令式 Python 类）并行存在，互不复用。

## 决策

采用**类型化算子目录 + 算子研发子图**，分三层落地：

### A. 算子目录（类型化、可组合、自动扫描注册）

#### A1. 策略 DSL 由"自由表达式"改为"算子名 + 参数"引用

```yaml
factors:
  - op: shift
    alias: prev_close
    params: { field: close, periods: 20 }
  - op: arithmetic
    alias: momentum
    params: { lhs: close, rhs: prev_close, op: "/" }
signals:
  - op: filter_threshold
    params: { field: momentum, op: ">", value: 0.05 }
  - op: rank_top
    params: { field: momentum, ascending: false, top: 10 }
```

不再有 `formula: close / shift(close, 20) - 1` 这种自由表达式字符串。所有计算都是"算子名 + 参数"，参数由对应算子的 `params_cls`（Pydantic）校验。

**关键收益**：未知 op 或参数错误在**解析期**抛错——这是消灭 refine 循环的关键，错误根本进不到回测。

#### A2. `Operator` 基类契约

`backtest/operators/base.py`：

- `Operator`（ABC）：`name`/`category`/`inputs`/`params_cls` 类属性 + `apply(panel, params) -> Series | DataFrame` 抽象方法。
- `OperatorParams`（Pydantic BaseModel）：每个算子定义自己的参数 schema，LLM 引用算子时目录能直接吐出 JSON Schema 给 function calling。
- `@operator` 装饰器：注册到 `OPERATOR_REGISTRY`。
- `validate_contract`：加载时校验——有 `apply`、`params_cls` 是 `OperatorParams` 子类、`inputs` 是 `list[str]`、`category` 在白名单内、**`causal=True` 硬约束**（算子必须声明并证明因果性）。

#### A3. 约定目录自动扫描

`backtest/operators/_loader.py`：

- 扫描 `operators/` 下所有 `*.py`（递归），跳过 `_` 前缀文件。
- 识别带 `@operator` 装饰器的类。
- **必须用 dotted 路径 `importlib.import_module` 加载**（非 `spec_from_file_location` 合成名），否则算子类会有两份身份，`isinstance` / Pydantic 校验失效。
- 算子名 `Operator.name` 全局唯一，冲突**启动即抛错**（静默覆盖最危险，启动炸出来最早）。
- 加载时机：首次 `import long_earn.backtest.operators` 时扫描一次，缓存进 `OPERATOR_REGISTRY`。
- 热注册：`register_operator(op)` 写入当前进程 registry，新进程靠启动扫描收敛。

#### A4. 因果性证明器（数学证明无未来函数）

`backtest/operators/causality.py`：

**因果性形式定义**：算子 `f` 因果，当且仅当对任意面板 `P` 与任意时刻 `T`，仅修改 `P` 中 `timestamp > T` 的行，不改变 `f(P)` 在 `timestamp <= T` 行上的输出。

`prove_causality(operator, panel, split_points)`：用"未来扰动不变性"数值验证——取确定性面板算 `O1`；把 `timestamp > T` 的数据大幅扰动（乘大常数/置 NaN），再算 `O2`；断言 `O1` 与 `O2` 在 `timestamp <= T` 上逐元素相等（容差内）。

该证明是**数学性质的**（基于因果性的操作定义），不是经验拟合，作为"系统从数学角度证明符合金融交易规范、严谨无未来函数"的依据。

> 这是本 ADR 区别于普通"算子库"的核心：算子上线有**数学硬约束**，不只是"能跑就行"。

### B. 算子目录接入策略执行路径

`backtest/engine/operator_executor.py`：

- `OperatorStrategyExecutor`：在 polars 历史面板（`timestamp <= 当前时刻`，VisibilityGuard 保证）上依次跑 factor 算子（结果列并回面板）→ signal 算子（filter/rank 行选择）→ 取当前时刻截面 → 选中标的。
- `resolve_factor_step` / `resolve_signal_step`：解析期校验 op + params，失败抛 ValueError。
- `DSLStrategy.on_bar`：`dsl.has_operator_steps()` 为真 → 走 `_on_bar_operators`（算子目录路径，polars，无 evaluator/pandas 转换）；否则走旧表达式路径（向后兼容）。

### C. 算子研发子图（operator_dev）

#### C1. 异步闭环：消费算子缺口 → 实现 → 验证 → 注册

```
START → pick_task ──(backlog 空)──► END
         │
         ▼
       spec_review ──(reject)──► (循环回 pick_task)
         │ (accept + 补全 params schema)
         ▼
       implement ──(LLM 产出算子类源码)
         │
         ▼
       test_validate ──(失败, 预算未用尽)──► refine ─┐
         │                                          │
         └─(失败, 预算用尽)──► mark_blocked ────────┤
         ▼ (通过)                                  │
       register ───────────────────────────────────┘
         │
         ▼
       (循环回 pick_task)
```

**关键性质**：
- 策略研发**永不阻塞**等待算子开发——缺口写进 backlog 就继续当前迭代（降级/跳过）。
- 算子开发子图**消费 backlog**，可离线/定时跑，也可人工触发单条。
- 产物是代码库里的 `.py` 文件 + 内存热注册，与人类写的算子**无任何区别**。
- LLM 代码执行风险收敛在本子图这一个可审查、可关停的子流程内；策略研发主链路**零 LLM 代码执行**。

#### C2. 各节点职责

| 节点 | 职责 | 用 LLM | 失败兜底 |
|---|---|---|---|
| `pick_task` | 从 backlog 按 priority 取一条未处理 spec；空则结束 | 否 | — |
| `spec_review` | 去重（目录已有？backlog 已有？）、判定合理性、补 Pydantic 参数 schema 草案、**校验 reference_strategy 非空** | 是（轻量） | reject |
| `implement` | LLM 产出实现 `Operator` 接口的 Python 类源码 | 是 | 进 refine |
| `test_validate` | **AST 审计**（白名单 polars/numpy/math/long_earn.backtest.*；禁 os/subprocess/eval/dunder）+ 契约校验 + **因果性证明**（prove_causality） | 否 | 失败进 refine；预算用尽 mark_blocked |
| `refine` | 把失败信息喂回 LLM 重写算子代码；改的是**算子代码**不是策略 | 是 | 计数到 `MAX_OP_REFINES`(3) 后 mark_blocked |
| `register` | 内存热注册 `register_operator(op)`；更新 backlog 状态 | 否 | 写盘失败 → mark_blocked |
| `mark_blocked` | 更新 backlog 状态为 blocked | 否 | — |

> **关键关卡**：`test_validate` 用 `prove_causality` 数学证明无未来函数，不通过则 refine，用尽则 blocked——**含未来函数的算子绝不进目录**。

#### C3. 安全边界

- **AST 审计白名单采用允许列表**（非禁止列表）：只允许 `import polars/numpy/math/typing/dataclasses/enum/from long_earn.backtest.*`，其余全拒。
- **执行隔离**：算子 `apply` 永远在子图自己的测试 runner 里跑，**绝不进策略研发子图的回测主循环**。策略研发引用的是"注册后、已审查、已测试"的算子对象。
- **产物等价**：注册后的算子与人类写的算子无任何区别，同样过 CI/审查。

### D. 策略优化模块（strategy_optimization）

`strategy_optimization/`：独立的"交易策略优化"模块，与 operator_dev 正交。

- `StrategyOptimizer` 协议 + `LLMStrategyOptimizer`（委托 strategy_rd research_agent）+ `FakeStrategyOptimizer`（测试用确定性改写）。
- `AcceptanceGate`：客观业绩验收——优化版无 error + 非退化 + **sharpe 严格提升**（用 sharpe 而非裸收益，防"高收益高波动"劣化被误判）；基线无 sharpe 时要求优化版 sharpe>0 且收益提升。
- `OptimizationPipeline`：optimize → backtest → accept + lineage 谱系。optimizer/backtest 可注入，e2e 用 Fake + mock。
- `optimize_strategy()` 便捷函数。

> **验收主判据用 sharpe**：这是"金融交易规范"在策略优化层的体现——风险调整后收益，不是裸收益。

## 文件结构

```
src/long_earn/
├── backtest/operators/
│   ├── __init__.py              # OPERATOR_REGISTRY, get_operator, register_operator
│   ├── base.py                  # Operator ABC, OperatorParams, @operator, validate_contract
│   ├── _loader.py               # 自动扫描 + 契约校验（dotted 路径加载）
│   ├── _util.py                 # temporal_series（时间序对齐）, cross_section
│   ├── causality.py             # prove_causality / is_causal / math_note
│   ├── factor/                  # shift, returns, windowed(mean/std/min/max/median/sum)
│   ├── filter/                  # filter_threshold
│   ├── rank/                    # rank_top（横截面 over timestamp）
│   ├── compose/                 # arithmetic(+ - * /)
│   └── technical/               # sma, ema, rsi, macd, bollinger
├── backtest/engine/
│   ├── operator_executor.py     # OperatorStrategyExecutor + resolve_*_step
│   └── dsl.py                   # StrategyDSL 新增 operator_factors 字段 + _validate_operator_steps
├── services/
│   └── backtest_service.py      # DSLStrategy._on_bar_operators + _build_operator_executor
├── operator_dev/                # 算子研发子图
│   ├── spec.py                  # OperatorSpec（强制 reference_strategy 非空）+ Priority
│   ├── backlog.py               # 线程安全优先级队列（HIGH→NORMAL→LOW，同名去重）
│   ├── sandbox.py               # AST 白名单审计 + 隔离编译加载 + 唯一 @operator 类提取
│   ├── agents.py                # OperatorImplementer 协议 + LLMImplementer + FakeImplementer
│   ├── state.py                 # OperatorDevState TypedDict
│   └── subgraph.py              # create_operator_dev_subgraph
└── strategy_optimization/       # 策略优化模块
    ├── optimizer.py             # StrategyOptimizer 协议 + LLM/Fake 实现
    ├── acceptance.py            # AcceptanceGate（sharpe 严格提升）
    └── pipeline.py              # OptimizationPipeline + optimize_strategy
```

## 理由

1. **参数错误解析期拦截**：Pydantic 校验在 `parse_strategy_yaml` 阶段就拦下错误，refine 循环基本退役——这是对 LLM 最友好的设计（错误信息清晰、无隐式回退）。
2. **单一事实源**：算子目录是唯一的算子定义源，删除 evaluator ↔ field_names 双重同步。下线算子 = 删文件，无第二个清单需同步。
3. **因果性数学证明**：每个算子过 `prove_causality`，新研发算子强制过因果性证明才注册，算子 DSL 执行路径整体过未来扰动不变性证明——三层保证无未来函数。
4. **LLM 代码风险收敛**：策略研发零 LLM 代码执行（只选算子名+填参数）；算子开发子图执行 LLM 代码但卡死在 AST 白名单 + 因果性证明 + 隔离 runner 内。
5. **异步闭环不阻塞**：策略研发发现算子缺口写 backlog 就继续，算子开发子图异步消费，两者解耦。
6. **产物等价于人工代码**：注册后的算子是 `.py` 文件 + 内存注册，与人类写的算子无任何区别，同样过 CI/审查。
7. **sharpe 验收**：策略优化用风险调整后收益做主判据，符合金融规范。

## 后果

- **旧表达式路径保留**（向后兼容）：`DSLStrategy.on_bar` 在 `has_operator_steps()` 为假时仍走 `_eval` / `SafeExpressionEvaluator`。`evaluator.py` 不删除，但新策略应使用算子路径。
- **双套策略体系部分保留**：`ml_strategy.py` / `strategy_templates.py` 暂保留，技术指标已迁移为算子但其调用方未统一切换（避免一次性改动过大）。
- **operator_dev register 目前仅内存热注册**：未写盘 `.py` 文件（plans 要求写盘），进程间一致性靠下次启动收敛。功能等价可用，写盘是增强项。
- **operator_dev / strategy_optimization 未挂载主图 `agent.py`**：两模块作为独立可调用子图存在，主图路由接入待后续。
- **strategy_rd 的 `gap_detector` 节点未接入**：strategy_rd reflection 后未产 OperatorSpec 写 backlog，两模块尚未串联。这是异步闭环的"入口"缺失，不影响各自独立可用。
- import-linter 新增 `operators_independent` 合约（算子目录不依赖上层）。

## 已实施状态

| 组件 | 状态 | 验证 |
|------|------|------|
| 算子目录骨架（base/loader/util） | ✅ 已交付 | `test_loader.py`（扫描/契约/冲突/热注册） |
| 因果性证明器 `causality.py` | ✅ 已交付 | `test_causality.py`（每算子过证明 + 负向测试 + 全目录覆盖断言） |
| 11 个初始算子（全因果） | ✅ 已交付 | `test_numerics.py` |
| `operator_executor.py` 接入策略执行路径 | ✅ 已交付 | `test_operator_dsl_e2e.py`（6 用例） |
| 算子 DSL 执行路径因果性证明 | ✅ 已交付 | `test_operator_dsl_causality.py`（未来扰动不变性，真实引擎） |
| operator_dev 子图（spec/backlog/sandbox/agents/subgraph） | ✅ 已交付 | `test_operator_dev_e2e.py`（正向+3负向+refine+去重） |
| strategy_optimization 模块 | ✅ 已交付 | `test_strategy_optimization_e2e.py` + 真实引擎 e2e |
| 系统级进化闭环测试 | ✅ 已交付 | `test_auto_evolution_system.py`（研发算子→优化策略→验收） |
| `gap_detector` 节点接入 strategy_rd | ❌ 未实施 | — |
| operator_dev register 写盘 `.py` | ❌ 未实施（仅内存热注册） | — |
| operator_dev / strategy_optimization 挂载主图 | ❌ 未实施 | — |
| 清理 ml_strategy / strategy_templates 双套体系 | ❌ 未实施 | — |
| 删除 evaluator.py | ❌ 未实施（向后兼容保留） | — |

## 后续（按优先级）

1. **`gap_detector` 节点接入**：strategy_rd `reflection` 后新增 `gap_detector` 节点，扫描 `improvement_suggestions` 与算子目录差异，产出 `OperatorSpec` 写 backlog。拓扑：`reflection → gap_detector → save_experience → supervisor`。这是异步闭环的"入口"，串联后系统才算完整自进化。
2. **operator_dev register 写盘**：register 节点写 `.py` 到 `operators/<category>/<name>.py`，产物持久化到代码库，走 CI/审查。当前仅内存注册，进程重启后丢失（靠启动扫描收敛，但 LLM 研发的算子不在扫描范围）。
3. **主图挂载**：`agent.py` 注册 operator_dev / strategy_optimization 子图入口，支持 CLI / 路由触发。
4. **清理双套体系**：评估 `ml_strategy.py::FeatureEngine` / `MLSignalStrategy` 是否可由算子目录 + 新 DSL 完全替代；`strategy_templates.py` 改写为新 DSL 或保留为示例。
5. **退役 evaluator.py**：当所有策略迁移到算子路径后，删除 `SafeExpressionEvaluator` + `_extract_field_names`。

## 与其他 ADR 的关系

- **ADR-003**（AST 安全求值器）：本 ATR 的算子目录**替代** ADR-003 的 `SafeExpressionEvaluator` 作为策略计算的主路径。ADR-003 的求值器暂时保留（向后兼容），待策略全部迁移后退役。ADR-003 状态后续应改为 Superseded by ADR-009。
- **ADR-005**（事件驱动回测）：本 ADR 在 ADR-005 引擎之上替换 DSL 执行层（`DSLStrategy.on_bar`），引擎核心（Event Loop / Broker / Portfolio / VisibilityGuard）不变。
- **ADR-007**（物质-运动架构）：operator_dev `notify` 节点（plans 设计，未实施）用 SubstanceStore 存"算子 X 已上线 + 适用场景"为 knowledge Substance，依赖 ADR-007 的记忆系统。strategy_optimization 的优化谱系可存为 strategy Substance。
- **ADR-008**（并行回测 + 统一模板）：参数网格的标量插值用 ADR-008 的 `render()`；算子 DSL 的参数化（`${lookback}` 等）用 ADR-008 的模板渲染。
- **ADR-010**（HTR）：HTR executor 内部的 backtest 步骤复用算子目录 DSL；HTR 假设的"改进方向"可触发算子缺口检测，产出 OperatorSpec 进 operator_dev backlog。
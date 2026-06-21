# 新回测引擎：算子目录 + 算子开发子图

## 背景与目标

当前回测引擎通过自定义 YAML DSL 描述策略，链路为：

```
LLM 生成 YAML DSL (strategy_develop_prompt.md)
  → parse_strategy_yaml → StrategyDSL (dsl.py, Pydantic)
  → DSLStrategy.on_bar (backtest_service.py)
  → SafeExpressionEvaluator (evaluator.py, 手写 AST 解释器)
  → EventDrivenBacktestEngine
```

### 核心维护痛点

1. **双重事实源**：`evaluator.py`（执行表达式）与 `dsl.py::_extract_field_names`（正则反向解析表达式做字段校验）必须手工同步。`dsl.py` 注释自述"必须与 `_KEYWORDS_AND_FUNCTIONS` 集合保持同步"，改一边漏改另一边即静默 bug。
2. **自研 mini-language**：`SafeExpressionEvaluator` 是 287 行手写 AST 解释器，本质上在重造受限 Python。
3. **refine 循环的存在本身**：`MAX_CODE_REFINES=3` + `_extract_yaml_from_response` 多重 hack（从 JSON 抠 / 从 ```yaml 抠 / 清理 trailing JSON），说明 YAML + 嵌套缩进 + 表达式语法对 LLM 不友好。
4. **静默退化诊断膨胀**：`DSLStrategy` 里 `factor_failures` / `step_failures` 大量逻辑，因为 DSL 每一步都可能"什么都不做却 success=True"。
5. **双套策略体系割裂**：`DSLStrategy`（声明式）与 `MLSignalStrategy` + `strategy_templates.py` + `FeatureEngine`（命令式 Python 类）并行存在，互不复用。

### 目标

采用 **方案 C（类型化算子目录）+ 自定义算子**：

- 策略 DSL 由"自由表达式字符串"改为"**有限、类型化、可组合的算子目录**"引用
- 算子目录通过**约定目录自动扫描**注册（`operators/<category>/*.py` + `@operator` 装饰）
- 提供**算子开发子图**作为异步闭环，处理算子目录缺口
- 策略研发主链路**零 LLM 代码执行**；LLM 代码执行风险收敛在算子开发子图这一个可审查、可关停的子流程内

---

## 架构总览

### 两个正交的子系统（异步闭环）

```
┌─ strategy_rd 子图 ────────────────────────────────┐
│  ... → reflection ──► gap_detector ──► backlog    │
│        (improvement_    (产出              (JSON)  │
│         suggestions)   OperatorSpec)               │
└────────────────────────────────────────────────────┘
                                  │ 异步，不阻塞
                                  ▼
┌─ operator_dev 子图（本设计）────────────────────────────┐
│ START → pick_task → spec_review → implement → test │
│   ↑                        │           │           │
│   │                        ▼           ▼           │
│   │                   (reject)     refine ◄──┐     │
│   │                        │        │      │     │
│   │                        ▼        └──────┘     │
│   │                   (blocked)                  │
│   │                        │                     │
│   │                        ▼                     │
│   └──────────────── validate(强制回测对比)         │
│                            │                      │
│                            ▼                      │
│                 register(写.py 到约定目录 + 内存热注册)│
│                            │                      │
│                            ▼                      │
│                 notify(memory + 回写策略研发)        │
│                            │                      │
│                            ▼                      │
│           END → 回 pick_task 消费下一条             │
└────────────────────────────────────────────────────┘
```

**关键性质**：
- 策略研发**永不阻塞**等待算子开发——缺口写进 backlog 就继续当前迭代（降级/跳过）
- 算子开发子图**消费 backlog**，可离线/定时跑，也可人工触发单条
- 产物是代码库里的 `.py` 文件，走审查/CI，与人类写的算子**无任何区别**

### 核心设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 算子引用方式 | LLM 从目录里**选名字 + 填参数**，不写任何表达式 | 参数错误被 Pydantic 在解析期拦下，refine 循环基本退役 |
| 自定义算子扩展时机 | **开发期注册**（offline），非运行期生成 | LLM 代码风险收敛在算子开发子图；策略研发零 LLM 代码执行 |
| 注册机制 | **约定目录自动扫描** + `@operator` 装饰 | 单一事实源；register 节点只写 `.py`；下线=删文件；无第二个清单需同步 |
| 算子名唯一性 | `Operator.name` 全局唯一，冲突启动即抛错 | 静默覆盖最危险，启动炸出来最早 |
| 缺口产出 | **独立 gap_detector 节点**（嵌在 strategy_rd 子图 reflection 之后） | 不污染 reflection；扫描 improvement_suggestions 与目录差异 |
| 回测验证 | **强制回测对比**；spec_review 拒绝缺 `reference_strategy` 的 spec | validate 永远有策略可跑，自洽不依赖运行时兜底 |
| 热注册 | register 节点写盘后**内存热注册**（`register_operator(op)`） | 开发完当轮可用；进程间一致性靠下次启动收敛 |

---

## 算子规约契约

### `Operator` 基类（所有算子必须满足）

位置：`src/long_earn/backtest/operators/base.py`

```python
from abc import ABC, abstractmethod
from typing import ClassVar
import polars as pl
from pydantic import BaseModel


class OperatorParams(BaseModel):
    """算子参数基类（每个算子定义自己的子类）"""
    pass


class Operator(ABC):
    """算子基类。

    每个算子是一个实现本接口的类，用 @operator 装饰后放入
    operators/<category>/ 目录即被自动扫描注册。

    设计要点：
    - params 用 Pydantic：LLM 在策略 DSL 里引用算子时，目录能直接吐出
      JSON Schema 给 function calling。填错参数在解析期被拦下，根本进不到回测。
    - apply 输入输出均为 polars：与引擎主循环一致，避免 pandas↔polars 胶水。
    """

    name: ClassVar[str]          # 全局唯一，与 spec.name 一致
    category: ClassVar[str]      # factor | filter | rank | compose | technical
    inputs: ClassVar[list[str]]  # 依赖的输入列名，如 ["close"]
    params_cls: ClassVar[type[OperatorParams]]  # 参数 schema

    @abstractmethod
    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series | pl.DataFrame:
        """执行算子。

        Args:
            panel: 面板数据，MultiIndex 语义 (timestamp, symbol) 或按算子需要
            params: 已校验的参数对象

        Returns:
            factor 类: 返回 Series（新增一列）
            filter 类: 返回布尔 Series（行选择掩码）
            rank 类: 返回排序后的 symbol 列表或带 rank 的 DataFrame
        """
        ...
```

### 算子目录结构（约定）

```
src/long_earn/backtest/operators/
├── __init__.py              # 暴露 OPERATOR_REGISTRY, get_operator, register_operator
├── base.py                  # Operator 基类、@operator 装饰器
├── _loader.py               # 自动扫描器 + 契约校验（首次 import 跑一次）
├── factor/
│   ├── __init__.py
│   ├── windowed.py          # WindowedFactor: window + agg=mean|std|min|max|median
│   ├── returns.py           # Returns: period + shift
│   └── shift.py             # Shift: field + periods
├── filter/
│   └── threshold.py         # FilterThreshold: field + op + value
├── rank/
│   └── topn.py              # RankTop: field + top + ascending
├── compose/
│   └── arithmetic.py        # Combine: lhs + rhs + op=+|-|*|/
└── technical/
    ├── rsi.py               # 从 ml_strategy.compute_rsi 迁移
    ├── macd.py              # 从 ml_strategy.compute_macd 迁移
    ├── bollinger.py         # 从 ml_strategy.compute_bollinger_bands 迁移
    └── ...
```

### 自动扫描规则（必须定死）

| 规则 | 约定 |
|---|---|
| 扫描范围 | `operators/` 下所有 `*.py`（递归），跳过 `_` 前缀文件（`_loader.py` 等） |
| 识别标记 | 模块内**带 `@operator` 装饰器**的类 |
| 算子名来源 | `Operator.name` 类属性 |
| 冲突处理 | 两个算子 `name` 撞了 → 加载器**启动即抛错** |
| 加载时机 | 首次 `import long_earn.backtest.operators` 时扫描一次，缓存进 `OPERATOR_REGISTRY: dict[str, Operator]` |
| 契约校验 | 加载时校验：有 `apply`、`params_cls` 是 `OperatorParams` 子类、`inputs` 是 `list[str]`、`category` 在白名单内 |
| 热注册 | `register_operator(op)` 写入当前进程 `OPERATOR_REGISTRY`，供同进程后续使用；新进程靠启动扫描自然生效 |

---

## 算子开发子图（operator_dev）

### 输入：`OperatorSpec`

来源：
- **自动**：strategy_rd 子图 `gap_detector` 节点产出
- **人工**：CLI / dashboard 投递

```python
@dataclass
class OperatorSpec:
    name: str                      # 目标算子名，如 "ema_slope"
    intent: str                    # 一句话意图：做什么、解决什么缺口
    input_fields: list[str]        # 依赖的输入列，如 ["close"]
    category: str                  # factor | filter | rank | compose | technical
    expected_output: str           # 语义说明：每行 float / bool / 横截面排名
    reference_strategy: str        # 触发它的策略 DSL（强制非空，validate 用）
    motivation: str                # 为什么现有目录满足不了
    priority: str = "normal"       # high | normal | low
```

**spec_review 节点强制要求 `reference_strategy` 非空**：gap_detector 产出时必须带上触发它的策略 YAML；人工投递必须附带最小验证策略。validate 节点因此永远有策略可跑。

### 子图节点与拓扑

```
START
  │
  ▼
 pick_task ──(backlog 空)──► END
  │
  ▼
 spec_review ──(reject: 重复/无意义/缺reference)──► mark_resolved → END
  │ (accept + 补全 params schema 草案)
  ▼
 implement ──(LLM 产出算子类源码)
  │
  ▼
 test ──┬─(测试失败, 预算未用尽)─► refine ─┐
        │                                │
        └─(测试失败, 预算用尽)────► mark_blocked → END
  │
  ▼ (通过)
 validate ──(AST 审计/契约/强制回测对比)──┐
  │  (劣化或契约不符)                    │
  │  └────────────────────────────► refine ─┘
  ▼ (通过)
 register (写 .py 到 operators/<category>/ + 内存热注册)
  │
  ▼
 notify (memory 记录 + 回写策略研发: 缺口已补)
  │
  ▼
 END → 循环回 pick_task
```

### 各节点职责

| 节点 | 职责 | 用 LLM | 失败兜底 |
|---|---|---|---|
| `pick_task` | 从 backlog 按 priority 取一条未处理 spec；空则结束 | 否 | — |
| `spec_review` | 去重（目录已有？backlog 已有？）、判定合理性、让 LLM 补 Pydantic 参数 schema 草案（字段名/类型/默认值/约束）、**校验 reference_strategy 非空** | 是（轻量） | reject → mark_resolved |
| `implement` | LLM 产出实现 `Operator` 接口的 Python 类源码（单文件字符串） | 是 | 进 refine |
| `test` | LLM 生成 + 执行单测：边界（NaN/空/单行）、数值正确性、契约（输入输出类型/shape）。执行用 pytest subprocess | 测试用例用 LLM，执行用 pytest | 失败进 refine；预算用尽 mark_blocked |
| `validate` | **静态**：AST 审计（import 白名单 polars/numpy/long_earn.backtest.*；禁 os/subprocess/socket/open/eval/exec/dunder）+ 契约。**强制回测对比**：用 spec.reference_strategy 跑"有/无新算子"对比，劣化则报错 | 否 | 契约不符/回测劣化 → refine（带对比报告） |
| `refine` | 把失败信息（测试输出 / 审计错误 / 回测对比）喂回 LLM 重写算子代码；**改的是算子代码**，不是策略 | 是 | 计数到 `MAX_OP_REFINES`(默认 3) 后 mark_blocked |
| `register` | 写算子到 `src/long_earn/backtest/operators/<category>/<name>.py`，调用 `register_operator(op)` 内存热注册；产出测试文件 | 否 | 写盘失败 → mark_blocked |
| `notify` | memory 存"算子 X 已上线 + 适用场景"；若 spec 来自某策略研发会话，回写可被该会话下一轮检索 | 否 | 静默警告 |
| `mark_resolved` / `mark_blocked` | 更新 backlog 状态 | 否 | — |

### 安全边界（守门）

算子开发子图**会执行 LLM 生成的 Python**（test / validate），但卡死范围：

- **AST 审计白名单**：只允许 `import polars`、`import numpy`、`from long_earn.backtest.* import`；禁 `os/subprocess/socket/open/eval/exec/__import__/dunder`
- **执行隔离**：算子 `apply` 永远在子图自己的测试 runner 里跑，**绝不进策略研发子图的回测主循环**。策略研发引用的是"注册后、已审查、已测试"的算子对象
- **产物等价**：注册后的算子与人类写的算子**无任何区别**，同样过 CI/审查

---

## strategy_rd 子图改动（gap_detector 接入）

### 新增 `gap_detector` 节点

位置：接在 `reflection` 节点之后、`save_experience` 之前（或并入 reflection 后处理）。

```python
# strategy_rd/subgraph.py
def _gap_detector_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """扫描 improvement_suggestions 与算子目录的差异，产出 OperatorSpec 写 backlog。

    不阻塞当前策略研发迭代：产出即返回，strategy_rd 继续 supervisor 判定。
    """
    suggestions = state.get("improvement_suggestions", []) or []
    strategy_yaml = state.get("strategy_yaml", "") or state.get("optimized_strategy_yaml", "")

    specs = research_agent.detect_operator_gaps(suggestions, strategy_yaml)
    # 写入 backlog（JSON 文件 / DuckDB 表）
    for spec in specs:
        write_to_backlog(spec)

    return {"operator_requests": specs}  # 仅记录，不影响主流程
```

### 拓扑改动

```python
# 原: reflection → save_experience → supervisor
# 改: reflection → gap_detector → save_experience → supervisor
workflow.add_edge("reflection", "gap_detector")
workflow.add_edge("gap_detector", "save_experience")
```

### `StrategyResearchAgent.detect_operator_gaps`

新增方法，单次轻量 LLM 调用规整 improvement_suggestions 中的"缺 X 能力"为 OperatorSpec 列表。规整失败的（LLM 吐不出合法 spec）直接丢弃，不进 backlog——宁可漏掉低质量需求，不堆垃圾。

---

## 策略 DSL 重构（声明式引用算子）

### 新 DSL 形态（YAML，但引用算子名 + 参数）

```yaml
strategy:
  name: ProfitGrowthStrategy
  description: 净利润同比增长率选股
  universe:
    type: csi300
    rebalance_freq: 20D
  start_date: 2020-01-01
  end_date: 2023-12-31
  factors:
    - op: shift              # 引用算子目录里的 "shift"
      alias: prev_close
      params: { field: close, periods: 20 }
    - op: arithmetic         # 组合算子
      alias: momentum
      params:
        lhs: close
        rhs: prev_close
        op: "/"
  signals:
    - op: filter_threshold   # filter 算子
      params: { field: momentum, op: ">", value: 0.05 }
    - op: rank_top           # rank 算子
      params: { field: momentum, ascending: false, top: 10 }
  weights:
    method: equal
```

**对比旧 DSL**：不再有 `formula: close / shift(close, 20) - 1` 这种自由表达式字符串。所有计算都是"算子名 + 参数"，参数由对应算子的 `params_cls`（Pydantic）校验。

### 解析与执行

- `parse_strategy_yaml`：解析 YAML，对每个 `op` 从 `OPERATOR_REGISTRY` 查找，用其 `params_cls` 校验 params。**未知 op 或参数错误在解析期抛错**——这是消灭 refine 循环的关键。
- `DSLStrategy.on_bar`：按顺序实例化算子、调用 `apply(panel, params)`。删除 `_eval` / `SafeExpressionEvaluator` 依赖。

---

## 实施阶段

### 阶段 0：算子目录基础设施（不破坏现状）

**目标**：建好算子目录 + 扫描器，但旧 DSL 链路完全不动，可并行验证。

新增文件：
- `src/long_earn/backtest/operators/__init__.py` — 暴露 `OPERATOR_REGISTRY`、`get_operator(name)`、`register_operator(op)`
- `src/long_earn/backtest/operators/base.py` — `Operator`、`OperatorParams`、`@operator` 装饰器
- `src/long_earn/backtest/operators/_loader.py` — 自动扫描 + 契约校验
- `src/long_earn/backtest/operators/factor/{__init__.py, windowed.py, returns.py, shift.py}`
- `src/long_earn/backtest/operators/filter/{__init__.py, threshold.py}`
- `src/long_earn/backtest/operators/rank/{__init__.py, topn.py}`
- `src/long_earn/backtest/operators/compose/{__init__.py, arithmetic.py}`

初始算子（覆盖当前 evaluator 能力）：
- `factor/shift.py` — 对应旧 `shift(field, n)`
- `factor/windowed.py` — 对应 `rolling_mean/std/min/max`
- `factor/returns.py` — 对应 `close / shift(close, n) - 1`
- `filter/threshold.py` — 对应 `field > value` 等
- `rank/topn.py` — 对应旧 rank step
- `compose/arithmetic.py` — 对应 `+ - * /`

测试：
- `tests/unit/test_backtest/test_operators/test_loader.py` — 扫描、冲突检测、契约校验
- `tests/unit/test_backtest/test_operators/test_*.py` — 每个初始算子的数值正确性

**验收**：`OPERATOR_REGISTRY` 能列出所有初始算子；重名算子启动抛错；契约不符加载被拒。

### 阶段 1：技术指标算子迁移

**目标**：把 `ml_strategy.py` 的技术指标迁移成算子，消除双套体系。

新增文件：
- `src/long_earn/backtest/operators/technical/{rsi.py, macd.py, bollinger.py, atr.py, kdj.py, cci.py, williams_r.py, obv.py}`

迁移映射：
- `compute_rsi` → `operators/technical/rsi.py` 的 `RSI` 算子
- `compute_macd` → `MACD` 算子
- `compute_bollinger_bands` → `BollingerBands` 算子
- 其余类同

**注意**：`ml_strategy.py` 的 `FeatureEngine` 与 `MLSignalStrategy` 暂时保留，仅做算子迁移不改其调用，避免一次性改动过大。阶段 3 再统一。

**验收**：技术指标算子单测通过；`ml_strategy.compute_*` 可改为薄包装调用算子（或保留原实现，二选一，保证数值一致）。

### 阶段 2：新 DSL 解析 + DSLStrategy 改造

**目标**：策略 DSL 切换到"算子引用"形态。

修改文件：
- `src/long_earn/backtest/engine/dsl.py` — `StrategyDSL` 模型调整：`factors` / `signals` 改为算子引用结构（list of `{op, alias?, params}`）；`parse_strategy_yaml` 增加算子查找与参数校验；删除 `_extract_field_names`（字段校验改由算子的 `inputs` + params 声明驱动）
- `src/long_earn/backtest/services/backtest_service.py` — `DSLStrategy.on_bar` 改为按算子顺序调用 `apply`；删除 `_eval` / `SafeExpressionEvaluator` 依赖；保留 `factor_failures` / `step_failures` 诊断（改为算子级失败记录）
- `src/long_earn/strategy_rd/agents/strategy_develop_prompt.md` — 更新为新 DSL 语法示例

**删除**：`src/long_earn/backtest/engine/evaluator.py`（287 行手写 AST 解释器）

**验收**：旧版示例策略（利润增长/动量/低估值）在新 DSL 下回测结果与旧版数值一致（容差内）；填错算子名/参数在解析期报错而非回测期。

### 阶段 3：算子开发子图

**目标**：实现异步闭环，处理算子目录缺口。

新增文件：
- `src/long_earn/operator_dev/__init__.py`
- `src/long_earn/operator_dev/state.py` — `OperatorDevState` TypedDict
- `src/long_earn/operator_dev/backlog.py` — `OperatorSpec`、backlog 读写（JSON / DuckDB）
- `src/long_earn/operator_dev/subgraph.py` — `create_operator_dev_subgraph`（pick_task/spec_review/implement/test/validate/refine/register/notify）
- `src/long_earn/operator_dev/agents/`
  - `spec_review_agent.py` + `spec_review_prompt.md`
  - `implement_agent.py` + `implement_prompt.md`
  - `refine_agent.py` + `refine_prompt.md`
- `src/long_earn/operator_dev/sandbox.py` — AST 审计 + 受限执行（pytest subprocess）

修改文件：
- `src/long_earn/strategy_rd/agents/strategy_research_agent.py` — 新增 `detect_operator_gaps` 方法
- `src/long_earn/strategy_rd/subgraph.py` — 加 `gap_detector` 节点 + 拓扑边
- `src/long_earn/strategy_rd/agents/strategy_research_prompt.py` — gap 检测 prompt
- `src/long_earn/agent.py` — 注册 `operator_dev` 子图入口

测试：
- `tests/unit/test_operator_dev/test_subgraph.py` — 各节点单测
- `tests/unit/test_operator_dev/test_sandbox.py` — AST 审计白名单/黑名单
- `tests/integration/test_operator_dev_e2e.py` — 端到端：投递 spec → 注册成功

**验收**：投递一条 spec，子图能完成 implement → test → validate（回测对比）→ register 全流程；危险 import 被 AST 审计拦下；测试失败的算子进 refine 循环。

### 阶段 4：清理与统一

**目标**：消除双套策略体系残留。

- 评估 `ml_strategy.py::FeatureEngine` / `MLSignalStrategy` 是否可由算子目录 + 新 DSL 完全替代
- `strategy_templates.py`（DoubleMA/RSIMeanReversion/MACDHistogram）改写为新 DSL 或保留为算子组合示例
- 删除 `dsl.py::_extract_field_names` 相关死代码
- 更新 `backtest/__init__.py` 导出
- 文档：`docs/` 下新增算子开发指南（人类加算子姿势）+ 算子目录清单生成脚本

**验收**：全套测试通过；`grep -r "SafeExpressionEvaluator\|_extract_field_names"` 无残留业务引用。

---

## 风险与对策

| 风险 | 对策 |
|---|---|
| 算子目录初始覆盖不足，LLM 表达不出某些策略 | 阶段 0/1 把 evaluator + ml_strategy 的全部能力迁移为算子，保证起点等价 |
| 强制回测对比导致算子上线慢 | spec_review 控制 priority；high 优先；validate 回测用短周期/小股票池快速验证 |
| 自动扫描的加载顺序/循环导入 | `_loader.py` 用 `importlib` 显式按文件路径加载，避免 `__init__.py` 链式导入；扫描顺序固定（按字母序）保证可复现 |
| 内存热注册的进程间不一致 | 仅当前进程生效，下次启动靠扫描收敛；文档注明"跨进程需重启或显式 register_operator" |
| LLM 生成的算子绕过 AST 审计 | 白名单采用**允许列表**而非禁止列表（只允许 polars/numpy/long_earn.backtest.*），其余全拒；CI 再跑一次静态审计 |
| gap_detector 产出垃圾 spec 污染 backlog | 规整失败直接丢弃；spec_review 二次去重与合理性判定；backlog 可人工清理 |

---

## 迁移后的复杂度对比

| 维度 | 现状 | 迁移后 |
|---|---|---|
| 手写 AST 解释器 | `evaluator.py` 287 行 | **删除** |
| 表达式反向解析校验 | `_extract_field_names` 正则 + 手工同步 | **删除**（改由算子 `inputs` + params 声明驱动） |
| 双重事实源 | evaluator ↔ field_names | **消除**（单一算子目录） |
| refine 循环 | YAML + 表达式出错反复修 | **基本退役**（参数错误解析期拦下） |
| 算子扩展 | 改 evaluator + field_names + prompt | 写一个 `.py` 文件 |
| 双套策略体系 | DSLStrategy ↔ MLSignalStrategy | **统一**到算子目录 |
| LLM 代码执行风险 | 无（DSL） | 收敛在算子开发子图（可审查、可关停） |

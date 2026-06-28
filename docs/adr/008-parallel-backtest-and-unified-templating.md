# ADR-008: 并行回测 + 统一模板渲染（`${var}`）

日期: 2026-06
状态: Accepted, Implemented

## 背景

回测引擎（ADR-005）已落地为事件驱动单进程串行循环。在以下两个维度出现基础设施缺口：

### 1. 性能维度

- 本机 32 逻辑核心，但回测主循环（`core.py` T-Loop）纯串行，每 bar 做 polars filter + 策略 `on_bar` + broker 撮合。
- `walk_forward_run` n_splits×2 次 `run()` 依次跑，互不依赖，是最容易并行、收益最直接的部分。
- 参数网格寻优（CLAUDE.md TODO 3「自动化参数寻优」）天然需要并行回测 N 组合。
- 数据层有共享单例：`MiniQmtClient` 单例、DuckDB 单连接、xtquant C++ 端不可重入。**不能**在并行 worker 里再触发 xtquant 下载。
- 引擎内部可变状态：`audit_logger.trail`、`VisibilityGuard._cached_history`、`strategy._state`、`broker`——每个并行回测必须各持一份独立实例。

### 2. 模板渲染维度

项目有两套变量语法并存，带来维护负担与跨语言迁移阻碍：

- `MarkdownPromptTemplate`（~10 个 `.md` prompt）用 `{{var}}`，内部转成 LangChain 的 `{var}` 再渲染。
- `agent.py` / `extract_prompt.py` / `strategy_optimize_prompt` 直接用原生 LangChain `PromptTemplate` 的 `{var}`。
- `prompt_loader.py` 有 ~80 行脆弱的代码块/内联代码 `{}` 转义逻辑，靠正则识别 ``` 和 `` ` ``——这正是 `{}` 语义带来的根本痛点，跨语言迁移时要逐语言重写。
- DSL YAML 当前零模板机制，参数寻优无法用"模板 + 网格"描述。

## 决策

两个正交但同期交付的基础设施改进：

### A. 统一模板渲染层：`${var}` + 纯函数渲染 + 解耦 LangChain

#### A1. 占位符语法统一为 `${var}`

**选择 `${var}`**（POSIX/bash/JS 模板字面量同款），替代并存的两套旧语法（`{var}` / `{{var}}`）。

**理由**：
- 跨语言最优：Go `os.Expand`、JS 模板字面量反引号、Rust `envsubst` 同形。
- 冲突最少：`${var}` 与 `{}`（JSON/代码块）、`` ` ``（内联代码）、```（代码围栏）均无冲突，**删除** 80 行转义逻辑。
- 认知最一致：一个项目内只有一种变量语法。

**转义**：`$$` → 字面 `$`（跨语言一致）。

#### A2. 纯函数渲染器解耦 LangChain

新增 `src/long_earn/core/render.py`（~36 行）：

- `render(template: str, variables: dict[str, Any]) -> str`：基于 `string.Template.safe_substitute`，零第三方依赖。
- `extract_variables(template: str) -> list[str]`：正则 `r"\$\{(\w+)\}"`，供 `MarkdownPromptTemplate` 自动提取变量。
- 缺失变量原样保留（`safe_substitute` 语义）。

`MarkdownPromptTemplate` 不再继承 `langchain_core.prompts.PromptTemplate`，变为持有 `(template_str, metadata)` 的轻量类；`.format(**vars)` 内部调用 `render()`。**API 不变**，调用方零改动。

#### A3. DSL 参数网格模板

新增 `src/long_earn/backtest/engine/param_grid.py`：

- `render_template(yaml_template: str, scalar_params: dict) -> str`：调 `render()`，仅做标量 `${var}` 插值。
- `apply_struct_params(dsl: StrategyDSL, struct_params: dict) -> StrategyDSL`：在解析后的 DSL 对象上做字段深拷贝+赋值（如 `top`、`universe.type`、`weights.signal_field` 等）。
- `ParamGrid`：接受 `dict[str, list]`（笛卡尔积）或 `list[dict]`（显式组合），展开为 `list[dict]`。
- **标量/结构化参数分区**：`ParamGrid(scalars={...}, structs={...})`，渲染时分两步走——标量先渲染 YAML 文本，结构化参数在解析后的 DSL 对象上变换。

> **语义单一原则**：标量插值（字符串替换）与对象层变换（字段赋值）职责分离，避免把列表/嵌套对象塞进 `${var}` 文本插值导致 JSON 转义地狱。

### B. 并行回测编排层

#### B1. 引擎微改（向后兼容）

`core.py` 两处可选参数，不改变现有调用方语义：

- `run()` 新增 `full_data: pl.DataFrame | None = None`：传入则跳过 `_prepare_data()`，直接用此份面板（含防御性日期过滤）。不传则原逻辑不变。
- `__init__` 新增 `audit_logger: InMemoryAuditTrail | None = None`：允许并行 worker 注入独立 audit 实例。不传则维持默认。
- `walk_forward_run()` 内部复用 `_prepare_data` 给每个 fold 传入同一份 `full_data`，避免每 fold 重复取数（顺带单进程性能优化）。

#### B2. 共享数据底座（零拷贝）

新增 `src/long_earn/backtest/engine/shared_data.py`：

- `SharedDataContext`：`multiprocessing.shared_memory.SharedMemory` + Arrow IPC 零拷贝分发 `pl.DataFrame`。
- 主进程：`pl.DataFrame.write_ipc(buf)` → 写入 SharedMemory，记下 `name/size/schema_hash`。
- worker：`SharedMemory.attach(name)` → `pl.read_ipc(buf)` 重建 DataFrame（polars 底层直接映射 Arrow buffer，零额外拷贝）。
- 生命周期：主进程持句柄，全部 worker 完成后 `close()+unlink()`；`try/finally` + `atexit` 注册兜底 unlink。
- pickle fallback：SharedMemory 不可用时退化为 pickle 传递，两条路径共存。
- 内存占用 = 1 份数据，无论多少 worker。

#### B3. 并行编排层

新增 `src/long_earn/backtest/engine/parallel.py`：

- `BacktestTask` / `BacktestOutcome`（`@dataclass(slots=True)`，可 pickle）。
- `_run_one_backtest(task)`：worker 入口。
  - **入口处强制 `os.environ["LONG_EARN_DISABLE_XTQUANT"]="1"`**——确保即便误引入也不触发 C++ abort。
  - 独立构造 `EventDrivenBacktestEngine`（注入 audit）+ `DSLStrategy`（每次 `init()`）+ 独立 broker。
  - `engine.data_provider=None`（不取数），调 `run(full_data=...)`。
- `ParallelRunner`：
  - `run_grid(strategy_template, param_grid, ...) -> GridResult`
  - `run_walk_forward(strategy_yaml, ..., n_splits) -> dict`
  - `max_workers` 默认 `os.cpu_count()`；`max_workers=1` 退化为顺序（CI/测试）。
  - 默认上限 256 组合，超出需显式 `allow_large_grid=True`。
- 错误隔离：单个 task 抛异常 → 转为 `BacktestOutcome(success=False, error=..., error_category=...)`，不拖垮整批（对齐 `_backtest_node` 的 `non_refine_categories` 分类语义）。

#### B4. BacktestService 薄封装

`services/backtest_service.py` 新增：

- `run_grid(strategy_template, param_grid, start_date="", end_date="") -> dict`
- `run_walk_forward_parallel(strategy_yaml, start_date="", end_date="", n_splits=3) -> dict`

委托 `ParallelRunner`，logger 打印进度。

## 文件结构

```
src/long_earn/
├── core/
│   ├── render.py                    # 新增：纯函数渲染器（${var}，~36 行）
│   └── prompt_loader.py             # 重写：解耦 LangChain，删除转义逻辑
├── backtest/engine/
│   ├── param_grid.py                # 新增：参数网格 + 标量插值 + 对象层变换
│   ├── shared_data.py               # 新增：SharedMemory + Arrow IPC 共享数据底座
│   ├── parallel.py                  # 新增：进程级并行编排层
│   └── core.py                      # 微改：run() 支持 full_data，__init__ 支持 audit_logger
└── services/
    └── backtest_service.py          # 新增方法：run_grid / run_walk_forward_parallel

# prompt 批量迁移：{var}/{{var}} → ${var}
├── stock_analysis/agents/*.md       # 5 个
├── strategy_rd/agents/*.md          # 5 个
├── agent.py                         # 原生 PromptTemplate → render()
├── stock_analysis/agents/extract_prompt.py
└── strategy_rd/agents/strategy_research_prompt.py

# 测试
tests/unit/test_backtest/
├── test_render.py                   # ${var} 基础插值/缺失保留/$$转义/extract_variables
├── test_param_grid.py               # 笛卡尔展开/render_template/apply_struct_params
└── test_parallel.py                 # SharedData/ParallelRunner（max_workers=1）/full_data 注入
```

## 理由

1. **`${var}` 消灭转义地狱**：`{}` 在 JSON/代码块里有语义负担，`{{}}` 是手工逃逸。`${var}` 与这些都不冲突，删除 80 行脆弱正则。
2. **解耦 LangChain 渲染**：`MarkdownPromptTemplate` 不再继承 `PromptTemplate`，渲染层零第三方依赖，跨语言可移植。
3. **进程级并行**：Python GIL 限制线程并行，回测是 CPU 密集，必须进程级。`ProcessPoolExecutor` + SharedMemory 零拷贝是 Windows spawn 下的最优解。
4. **共享数据底座**：32 worker 各自取数 = 32× 内存 + 32× xtquant 风险。共享一份面板 + worker 禁用 xtquant，内存 = 1 份，无 C++ 重入风险。
5. **标量/结构化参数分区**：参数网格覆盖"风控参数 + DSL 任意字段插值"全部场景，但标量用文本插值、结构化用对象变换，职责单一。
6. **向后兼容**：引擎微改不破坏现有调用方，`max_workers=1` 退化为顺序保证 CI 可测。

## 后果

- `MarkdownPromptTemplate` 不再继承 LangChain，但保留 `input_variables` 同名属性兼容 `__repr__`。
- 所有 `.md` prompt 与内联模板统一为 `${var}`，旧 `{var}`/`{{var}}` 被批量替换。
- `render.py` 零外部依赖，import-linter 新增 `render_independent` 合约。
- `backtest.engine.parallel` 在 worker 侧严禁 import `backtest.data`（编排层只在主进程预取时依赖 data）。
- Windows spawn 下 SharedMemory handle 需 `try/finally` + `atexit` 兜底 unlink，防泄漏。

## 已实施状态

| 组件 | 状态 | 验证 |
|------|------|------|
| `core/render.py` 纯函数渲染器 | ✅ 已交付 | `test_render.py` |
| `prompt_loader.py` 解耦重写 | ✅ 已交付 | 现有 prompt 加载测试不破 |
| prompt `.md` 批量 `${var}` 迁移 | ✅ 已交付 | — |
| `engine/param_grid.py` 参数网格 | ✅ 已交付 | `test_param_grid.py` |
| `engine/shared_data.py` 共享数据底座 | ✅ 已交付 | `test_parallel.py::TestSharedData` |
| `engine/parallel.py` 并行编排层 | ✅ 已交付 | `test_parallel.py::TestParallelRunner` |
| `engine/core.py` 微改（full_data/audit_logger） | ✅ 已交付 | `test_engine.py` 无需改动 |
| `BacktestService.run_grid` / `run_walk_forward_parallel` | ✅ 已交付 | 服务层测试 |

## 后续（不在本 ADR 范围）

- **strategy_rd subgraph 接入 grid 自动寻优节点**（CLAUDE.md TODO 3「自动化参数寻优」）：基础设施已交付（`parallel.py` + `param_grid.py`），subgraph 接入留待 ADR-010 HTR executor 内部或独立优化节点。
- **T-Loop 内部向量化/线程化**：独立优化项，本 ADR 不动。

## 与其他 ADR 的关系

- **ADR-002**（partial 节点注入）：并行编排层的节点注入沿用 partial 模式。
- **ADR-005**（事件驱动回测）：本 ADR 在 ADR-005 引擎之上叠加并行编排，不改引擎核心语义（`run()` 微改向后兼容）。
- **ADR-009**（算子目录）：参数网格的标量插值用本 ADR 的 `render()`；DSL 模板渲染是算子目录 DSL 的参数化入口。
- **ADR-010**（HTR）：HTR executor 内部的 backtest 步骤可复用并行编排做参数寻优；HTR held-out 验证门可用 `run_walk_forward_parallel`。
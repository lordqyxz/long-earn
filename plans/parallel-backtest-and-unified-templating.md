# 并行回测 + 统一模板渲染执行计划

> 目标：充分利用本机 32 逻辑核心提高回测速率；设计参数网格并行回测工作流；同时把项目变量引入语法统一为跨语言可移植的 `${var}`，并从 LangChain 解耦渲染器。

## 背景与现状

- 本机 32 逻辑核心，Python 3.11 严格版本（生产 `requires-python="==3.11.*"`，uv 管理环境；系统默认 Python 为 3.14 但项目跑在 3.11）。
- Windows 平台，`multiprocessing` 默认 `spawn`：worker 为干净进程，但不能 pickle lambda/局部闭包。
- 现有 T-Loop 纯串行 Python（`backtest/engine/core.py:120`），每个 bar 做 polars filter + 策略 `on_bar` + broker 撮合。
- `walk_forward_run`（`core.py:664`）n_splits×2 次 `run()` 依次跑，互不依赖，是最容易并行、收益最直接的部分。
- 数据层有共享单例：`MiniQmtClient` 单例、DuckDB 单连接、xtquant C++ 端不可重入。**不能**在并行 worker 里再触发 xtquant 下载。
- 引擎内部可变状态：`audit_logger.trail`、`VisibilityGuard._cached_history`、`strategy._state`、`broker`——每个并行回测必须各持一份独立实例。
- 现有两套变量语法并存：
  - `MarkdownPromptTemplate`（~10 个 `.md` prompt）用 `{{var}}`，内部转成 LangChain 的 `{var}` 再渲染。
  - `agent.py` / `extract_prompt.py` / `strategy_optimize_prompt` 直接用原生 LangChain `PromptTemplate` 的 `{var}`。
- `prompt_loader.py:262-322` 有 80 行脆弱的代码块/内联代码 `{}` 转义逻辑，靠正则识别 ``` 和 `` ` ``——这正是 `{}` 语义带来的根本痛点，跨语言迁移时要逐语言重写。
- Jinja2 在依赖树里但是 torch 的传递依赖，项目从未直接 import。
- DSL YAML 当前零模板机制：LLM 直接生成完整 YAML。

## 已确认决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 占位符语法 | `${var}` | POSIX/bash/JS 模板字面量同款，跨语言最优、冲突最少、认知最一致 |
| 渲染器归属 | 解耦 LangChain，纯函数渲染 | 跨语言可移植，删除 80 行转义逻辑 |
| 结构化参数 | 标量插值 + 对象层变换 | 语义单一、跨语言易移植 |
| 并行粒度 | 同时交付 `run_param_grid` + `run_walk_forward_parallel` | 参数网格服务自动寻优 TODO；Walk-Forward 改造面小 |
| 数据分发 | 共享内存底层，上层引用指针各自处理 | 零拷贝共享，内存占用 = 1 份 |
| 引擎改动 | 允许小改：`run()` 支持注入 `full_data` | 向后兼容，编排层更简洁 |
| 参数网格维度 | 风控参数 + DSL 任意字段插值 | 覆盖自动寻优全部场景 |
| 本轮交付范围 | 阶段 1–5 + 阶段 7–8，subgraph 接入留下一轮 | 避免一次性改动过大 |

## 执行阶段

### 阶段 A — 模板渲染层统一（`${var}` + 纯函数渲染 + 解耦 LangChain）

#### A1. 新增 `src/long_earn/core/render.py`（~20 行纯函数渲染器）

- `render(template: str, variables: dict[str, Any]) -> str`
- 规则：
  - `${name}` → `str(variables[name])`
  - 缺失原样保留（`safe_substitute` 语义）
  - `$$` → 字面 `$`（跨语言一致转义）
- 基于 `string.Template.safe_substitute`，零第三方依赖
- 附 `extract_variables(template: str) -> list[str]`：正则 `r"\$\{(\w+)\}"`，供 `MarkdownPromptTemplate` 自动提取
- 跨语言映射：
  - Go: `os.Expand(s, lookup)` / `strings.NewReplacer`
  - JS: 模板字面量反引号同形 `${var}`
  - Rust: `envsubst` crate / `str::replace`

#### A2. 重写 `src/long_earn/core/prompt_loader.py`

- `MarkdownPromptTemplate` 不再继承 `langchain_core.prompts.PromptTemplate`
- 变为持有 `(template_str, metadata)` 的轻量类；`.format(**vars)` 内部调用 `render()`
- **删除 `_convert_variables` / `_convert_inline_line` / `_extract_variables`（~80 行转义逻辑）**——`${var}` 与 `{}`/`` ` ``/``` 无冲突，转义逻辑不需要
- API 不变：`MarkdownPromptTemplate("foo.md", caller_file=__file__).format(query=q)` 调用方零改动
- 自动提取变量改用 `render.extract_variables`
- 保留 `input_variables` 同名属性（从 `extract_variables` 预计算），兼容现有 `__repr__`

#### A3. prompt `.md` 文件批量替换 `{var}` / `{{var}}` → `${var}`

涉及文件（已 grep 确认）：
- `stock_analysis/agents/`:
  - `buffett_prompt.md`
  - `charles_munger_prompt.md`
  - `fiske_prompt.md`
  - `petter_prompt.md`
  - `extract_prompt.md`
- `strategy_rd/agents/`:
  - `strategy_develop_prompt.md`
  - `strategy_develop_refine_prompt.md`
  - `strategy_research_prompt.md`
  - `strategy_rd_supervisor_prompt.md`
  - `strategy_rd_supervisor_continue_prompt.md`（同时把 `{{var}}` 双花括号也统一）

#### A4. `agent.py`、`extract_prompt.py`、`strategy_research_prompt.py` 中的原生 `PromptTemplate` 迁移

- 这三处内联 Python 字符串模板改用 `render()` 纯函数
- 模板字符串内 `{var}` → `${var}`
- `{{ }}` JSON 转义 → 直接 `{ }`（`${var}` 下 JSON 大括号无需转义）
- 删除 `from langchain_core.prompts import PromptTemplate` 导入
- 涉及位置：
  - `agent.py:58-73`（routing_prompt）
  - `agent.py:194-209`（summarize_prompt）
  - `stock_analysis/agents/extract_prompt.py:12-21`
  - `strategy_rd/agents/strategy_research_prompt.py:41-93`（strategy_optimize_prompt）

### 阶段 B — DSL 参数网格模板（标量插值 + 对象层变换）

#### B1. 新增 `src/long_earn/backtest/engine/param_grid.py`

- `render_template(yaml_template: str, scalar_params: dict[str, Any]) -> str`：调 `render()`，仅做标量 `${var}` 插值
- `apply_struct_params(dsl: StrategyDSL, struct_params: dict[str, Any]) -> StrategyDSL`：在解析后的 DSL 对象上做字段深拷贝+赋值（如 `top`、`universe.type`、`weights.signal_field`、`operator_factors` 列表项等）
- `ParamGrid`：接受 `dict[str, list]`（笛卡尔积）或 `list[dict]`（显式组合），展开为 `list[dict]`
- 标量/结构化参数分区：`ParamGrid(scalars={...}, structs={...})`，渲染时分两步走

#### B2. DSL YAML 模板示例

```yaml
name: ${strategy_name}
universe:
  type: ${universe_type}
factors:
  momentum: "close / shift(close, ${lookback}) - 1"
signals:
  - type: filter
    condition: "momentum > ${threshold}"
  - type: rank
    by: momentum
    top: 10
risk_control:
  stop_loss: ${stop_loss}
```

### 阶段 C — 引擎微改（向后兼容）

#### C1. `src/long_earn/backtest/engine/core.py`

- `run()` 新增可选参数 `full_data: pl.DataFrame | None = None`：
  - 若传入则跳过 `_prepare_data()`，直接用此份面板（含防御性日期过滤，保持现有行为）
  - 不传则保持原逻辑不变
- `__init__` 新增可选参数 `audit_logger: InMemoryAuditTrail | None = None`：
  - 允许并行 worker 注入独立 audit 实例；不传则维持现有默认（`self.audit_logger = InMemoryAuditTrail()`）
- `walk_forward_run()` 内部改用 `_prepare_data` 复用 + 给每个 fold 的 `run()` 传入同一份 `full_data`，避免每 fold 重复取数（顺带的单进程性能优化）

> 这两条改动都不改变现有调用方的语义，单测无需改动。

### 阶段 D — 共享数据底座

#### D1. 新增 `src/long_earn/backtest/engine/shared_data.py`

- `SharedDataContext`：`multiprocessing.shared_memory.SharedMemory` + Arrow IPC 零拷贝分发 `pl.DataFrame`
- 主进程：`pl.DataFrame.write_ipc(buf)` → 写入 SharedMemory，记下 `name/size/schema_hash`
- worker：`SharedMemory.attach(name)` → `pl.read_ipc(buf)` 重建 DataFrame（polars 底层直接映射 Arrow buffer，零额外拷贝）
- 生命周期：主进程持句柄，全部 worker 完成后 `close()+unlink()`
- 提供 `SharedDataContext` 上下文管理器：`with SharedDataContext(df) as ctx: ... ctx.token`
- `try/finally` + `atexit` 注册兜底 unlink；worker 端只 `attach` 不 `close`（close 由主进程统一管）
- pickle fallback 路径：当 SharedMemory 不可用时（极少数受限环境），退化为 pickle 传递。代码里两条路径共存，按可用性探测

### 阶段 E — 并行编排层

#### E1. 新增 `src/long_earn/backtest/engine/parallel.py`

- `BacktestTask` / `BacktestOutcome`（`@dataclass(slots=True)`，可 pickle）
- 顶层 `_run_one_backtest(task)`：worker 入口
  - **入口处强制 `os.environ["LONG_EARN_DISABLE_XTQUANT"]="1"`**，确保即便误引入也不触发 C++ abort
  - 独立构造 `EventDrivenBacktestEngine`（注入 audit）+ `DSLStrategy`（每次 `init()`）+ 独立 broker
  - `engine.data_provider=None`（不取数），调 `run(full_data=...)`
- `ParallelRunner`：
  - `run_grid(strategy_template, param_grid, start_date, end_date, symbols, universe_type, benchmark_symbol) -> GridResult`
  - `run_walk_forward(strategy_yaml, start_date, end_date, symbols, n_splits, benchmark_symbol) -> dict`
  - `max_workers` 默认 `os.cpu_count()`；`max_workers=1` 退化为顺序（CI/测试）
  - 默认上限 256 组合，超出需显式 `allow_large_grid=True`；并行度受 `max_workers` 自然节流
- 内部：主进程预取 `full_data` → 建 `SharedDataContext` → 构造 N 个 `BacktestTask` → `ProcessPoolExecutor.map(_run_one_backtest, tasks)` → 汇总
- 错误隔离：单个 task 抛异常 → 捕获后转为 `BacktestOutcome(success=False, error=..., error_category=...)`，不拖垮整批（对齐现有 `_backtest_node` 的 `non_refine_categories` 分类语义）

### 阶段 F — BacktestService 薄封装

#### F1. `src/long_earn/services/backtest_service.py` 新增

- `run_grid(strategy_template, param_grid, start_date="", end_date="") -> dict`
- `run_walk_forward_parallel(strategy_yaml, start_date="", end_date="", n_splits=3) -> dict`
- 内部委托 `ParallelRunner`
- logger 打印进度（`f"[grid] {done}/{total} 完成, 最优 sharpe={best}"`）

### 阶段 G — 测试

#### G1. `tests/unit/test_backtest/test_render.py`（新）

- `${var}` 基础插值、缺失原样保留、`$$` 转义、`extract_variables` 正确性

#### G2. `tests/unit/test_backtest/test_param_grid.py`（新）

- 笛卡尔展开数量
- `render_template` 标量插值
- `apply_struct_params` 对象层变换
- 代码块大括号不受影响

#### G3. `tests/unit/test_backtest/test_parallel.py`（新）

- `TestSharedData`：写入 → 多进程读取一致；上下文退出后 SharedMemory unlink
- `TestParallelRunner`（`max_workers=1` 单进程同步模式，避免 CI 开销）：
  - grid 2×2 → 4 结果按 sharpe 排序
  - walk_forward 并行 vs 串行数值一致
  - 单 task 异常不拖垮整批，error_category 透传
- `TestEngineFullDataInjection`：`run(full_data=...)` 跳过取数、防御性日期过滤仍生效

#### G4. 现有 `tests/unit/test_backtest/test_engine.py` 无需改动（向后兼容）

### 阶段 H — 质量门槛

按 CLAUDE.md 顺序：

1. 每个新建/修改文件 `mcp__serena__get_diagnostics_for_file` Error=0
2. `uv run ruff check src/` 全绿（McCabe ≤15、未用参数）
3. `uv run lint-imports`：
   - `backtest.engine` 不反向依赖 `backtest.data`（编排层只在主进程预取时依赖 data，worker 侧 import 严禁）
   - `core/render.py` 零外部依赖
4. `uv run pytest tests/unit/ -v` 全绿

## 风险与对策

| 风险 | 对策 |
|---|---|
| Windows spawn 下 SharedMemory handle 泄漏 | `SharedDataContext` 用 `try/finally` + `atexit` 注册兜底 unlink；worker 端只 `attach` 不 `close`（close 由主进程统一管） |
| polars Arrow IPC 与 SharedMemory 跨进程 schema 不匹配 | 预取后立即 round-trip 校验；不通过则降级 pickle 路径 |
| 32 worker 同时拉起 xtquant | worker 入口显式 `os.environ["LONG_EARN_DISABLE_XTQUANT"]="1"` 确保即便误引入也不触发 C++ abort |
| Grid 笛卡尔爆炸（如 5 维 × 5 值 = 3125 次） | `ParallelRunner` 默认上限 256 组合，超出要求显式 `allow_large_grid=True`；并行度受 `max_workers` 自然节流 |
| 单进程 fallback（CI 无法起进程池） | `executor=None` 或 `max_workers=1` 时退化为顺序 map，保证逻辑可测 |
| `${var}` 在含 `$` 的金融表达式里误伤 | DSL 因子表达式极少出现 `$`；渲染前可加 lint 校验"模板内 `$` 必须成对 `{`" |
| `MarkdownPromptTemplate` 解耦后 `input_variables` 属性被外部读取 | 保留同名属性（从 `extract_variables` 预计算），兼容现有 `__repr__` |

## 不在本轮范围

- `strategy_rd` subgraph 接入 grid 自动寻优节点（CLAUDE.md TODO 3）——留下一轮，避免改动过大
- T-Loop 内部向量化/线程化——独立优化项，本轮不动

## 新增/修改文件清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `src/long_earn/core/render.py` | 新增 | 纯函数渲染器（`${var}`，~20 行） |
| `src/long_earn/core/prompt_loader.py` | 重写 | 解耦 LangChain，删除转义逻辑 |
| `src/long_earn/backtest/engine/param_grid.py` | 新增 | 参数网格 + 标量插值 + 对象层变换 |
| `src/long_earn/backtest/engine/shared_data.py` | 新增 | SharedMemory + Arrow IPC 共享数据底座 |
| `src/long_earn/backtest/engine/parallel.py` | 新增 | 进程级并行编排层 |
| `src/long_earn/backtest/engine/core.py` | 微改 | `run()` 支持注入 `full_data`，`__init__` 支持注入 `audit_logger` |
| `src/long_earn/services/backtest_service.py` | 新增方法 | `run_grid` / `run_walk_forward_parallel` |
| `src/long_earn/agent.py` | 改 | 原生 `PromptTemplate` → `render()`，`{var}` → `${var}` |
| `src/long_earn/stock_analysis/agents/extract_prompt.py` | 改 | 原生 `PromptTemplate` → `render()` |
| `src/long_earn/strategy_rd/agents/strategy_research_prompt.py` | 改 | 原生 `PromptTemplate` → `render()` |
| `src/long_earn/stock_analysis/agents/*.md` (5 个) | 改 | `{var}` → `${var}` |
| `src/long_earn/strategy_rd/agents/*.md` (5 个) | 改 | `{var}` / `{{var}}` → `${var}` |
| `tests/unit/test_backtest/test_render.py` | 新增 | 渲染器测试 |
| `tests/unit/test_backtest/test_param_grid.py` | 新增 | 参数网格测试 |
| `tests/unit/test_backtest/test_parallel.py` | 新增 | 并行编排测试 |
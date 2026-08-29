# 代码评审规则归结（code-review 规则产物）

> **用途**：本文件是代码评审规则的**归结产物**——由评审 subagent 在评审时依据
> `AGENTS.md` + `docs/adr/` 动态归结，稳定后的结论写回本文件沉淀复用
> （LLM 归结一次 → 确定性脚手架复用，与 ToG `record_path_outcome` 写回同构）。
>
> **维护约定**：评审 subagent 发现本文件未覆盖的规则时，先在评审意见中给出，
> 规则稳定后写回对应路径小节；与 AGENTS.md / ADR 冲突时以上游为准并回改本文件。

## 通用（所有路径）

- 架构整洁是第一原则，优先于向后兼容与修改范围；不得引入反向依赖、跨上下文耦合或双轨实现（AGENTS.md §2.1）
- 所有函数/参数必须有类型注解；内部结构用 `@dataclass`，外部动态数据用 Pydantic；`Any` 仅限第三方边界并注释原因
- 日志只用 loguru（禁止 `import logging`）；Python 3.13 严格版本
- 新增环境变量必须同步更新 `config.py::AppConfig` docstring
- Prompt 一律 `.md` + jinja2 `{{ var }}`（`${var}` 是 CI 卡口违例，ADR-011）

## src/long_earn/backtest/**

- 绝对杜绝未来函数：任何数据读取/因子计算/撮合逻辑改动，必须确认 PIT 对齐与窗口截断（ADR-005 / ADR-013）；因子全期预计算须有算子因果性证明背书
- 引擎仅支持多头；`weights` 仅 `equal`；DSL 强制拒绝旧式 `factors`/`filter`/`rank`/`expression`
- 回测记录标签铁律：测试/冒烟回测写共享 PG 必须带 `tags=["test"]`，生产回测不传 tags
- PG 缓存表（`price_daily`/财务/`panel_daily`）是权威数据源：不得手动 DELETE/DROP 或覆盖；增量维护走 `download_data.py` / 水位机制

## src/long_earn/strategy_rd/**

- 三段式数据分割铁律：训练集自由用；测试集仅在合并门触碰；验证集研发全程禁用（AGENTS.md §五）
- 合并决策必须过 held-out OOS + ADR-015 统计门；写回 success 须过证据/指标可信/DSR 门（`_validate_success_writeback`）
- HTR 遗留线（`htr_subgraph.py` + `agents/`）冻结：不新增调用方、不加功能，待清退专项（TODO）

## src/long_earn/services/** 与 src/long_earn/tools/**

- 依赖方向 `tools` → `services` → `domain` 单向收敛；services 不得依赖 tools（import-linter 0 broken）
- 新服务定义为 `Protocol`（`services/__init__.py`），实现放 `*_service.py`
- **不得内嵌 LLM 调用**（ADR-021）：脚手架层只产确定性、类型化结构化中间态；LLM 推理只允许在 agent 节点层（卡口：`scripts/check_llm_call_sites.py`）
- `RuntimeContext` 下游只接受已构造完毕的服务实例；`Service | None` 仅限容器初始化中间态

## src/long_earn/event_inference/** 与 operator_dev/**

- `prove_causality` / `prove_registration_causality` 是上线硬约束，不可跳过、不可降级（ADR-009）
- 未通过因果性证明的算子不得写盘注册进目录

## src/long_earn/stock_analysis/**

- 标的解析顺序：状态已有 code → 正则 → 名称字典 → LLM 兜底（ADR-021）；不得把 LLM 兜底改回首选项
- 数据节点保持纯确定性取数，重试走既有 `_retry_with_exponential_backoff`

## agent 层（master_agent / research_agent / 各子图 agents）

- LLM 节点产出之后的数据整理/分组/校验，优先做纯确定性后处理（参考 `event_inference/subgraph.py` 冲突分组）
- LangGraph 节点只返回需更新的 key；prompt `.md` 与 agent `.py` 同目录
- LLM 循环控制流决策（continue/merge/stop 类）原则上规则化；确需 LLM 时必须有非 JSON 安全降级

## tests/unit/**

- 只测接口层与系统关键环节（AGENTS.md §4.3）；不写数据类构造、显而易见错误路径、mock 链端到端
- 涉及三段式分割/统计门/写回门的测试断言变更，须在提交说明中说明为什么不破坏铁律

## scripts/ 与 CI

- 卡口脚本（`check_deprecated_syntax.py` / `check_llm_call_sites.py`）退出码契约：0 通过 / 1 违例；白名单扩容必须注明架构理由
- 脚本入口 `logger.remove()` + `logger.add(sys.stderr, ...)`，格式对齐 `LoggerServiceImpl`

---
id: 12
title: 大师智能节点可复用技能包
status: Accepted
date: 2026-07
summary: MasterPersona 协议与注册表；四 mode 可扩展，统一大师视角调用契约。
---

# ADR-012: 大师智能节点可复用技能包


## 背景

巴菲特、芒格、费雪、林奇原为 `stock_analysis` 内 4 个硬编码节点：仅服务单一消费方（strategy_rd 无法调用大师视角做策略审视/生成）；新增大师须修改子图拓扑、创建 agent、创建 prompt；prompt 资产散落，复用须手工拷贝，存在漂移风险；无统一调用契约（分析股票输入 stock_data，审视策略输入 strategy + backtest_result）。

## 决策

我们将大师升级为 **MasterPersona 智能节点**：统一 Protocol 与注册表，任何消费方按 name 注入调用。

### 核心抽象（`src/long_earn/skills/personas/`）

- **`MasterPersona` Protocol**：`name` / `display_name` / `perspective` + `analyze(context: PersonaContext) -> PersonaResult`。
- **`PersonaContext`**（Pydantic）：`mode`（四 mode：`stock_analysis` / `strategy_review` / `strategy_generate` / `result_synthesis`）+ `target` + 可选 `backtest_result` / `event_context`。
- **`PersonaResult`**：`verdict` / `rationale` / `weaknesses` / `suggestions` / `confidence` / `raw_analysis`（可审计原文）。
- **`PersonaRegistry`**：`@PersonaRegistry.register` 装饰器注册，`create_all(llm)` 创建全部实例；`__init__.py` import 触发自动发现。
- **`BasePersona`** 基类：`__init__` / `_parse_result` 等公用方法；大师可只实现部分 mode，未实现抛 `NotImplementedError`，消费方自动跳过（渐进扩展）。
- prompt 集中于 `skills/personas/prompts/<name>/<mode>.md`（frontmatter `messages` 结构，jinja2 `{{ var }}`），few-shot 迁入大师类内按 mode 区分。

### 新增大师三步（无需修改任何消费方代码）

1. `skills/personas/<name>.py`：继承 `BasePersona` + `@PersonaRegistry.register`，声明 `name`（须与 prompts 目录名一致）/ `display_name` / `perspective` / `supported_modes`，按 mode 实现 `_do_analyze`；
2. `skills/personas/prompts/<name>/` 下按 mode 建 `.md`；
3. `__init__.py` 加一行 import 触发注册。

内置：buffett / charles_munger / fiske / petter + 扩展示例 livermore。

### 失败回退策略

大师调用失败（LLM 异常 / 超时 / NotImplementedError）：`stock_analysis` 模式该大师结果置空、其余正常汇总；`strategy_generate` / `strategy_review` 模式跳过该视角，不影响主流程；`result_synthesis` 回退为无大师视角汇总。try/except 实现，不中断主流程。

## 后果

**正面**

- 多消费方统一复用（股票分析五视角 / 策略生成 hint / 策略反思 review / 结果综合）。
- prompt 集中管理；新增大师零拓扑改动。
- 统一调用契约（`PersonaContext` / `PersonaResult`），消费方与大师解耦。

**负面**

- 大师仍为单次 LLM 调用（有意为之，mini agent 升级另行立项）。
- 多大师 × 多 mode 的 prompt 文件须逐个维护。
- Registry 类变量为全局状态，测试间须 fixture 重置。

**中性**

- 节点注入复用 ADR-002 partial 模式；prompt 消费 ADR-011 `MarkdownChatPromptTemplate`。
- 具体实现细节以源码为准。

## 关联

- 依赖: ADR-002（partial 节点注入）、ADR-011（MarkdownChatPromptTemplate）

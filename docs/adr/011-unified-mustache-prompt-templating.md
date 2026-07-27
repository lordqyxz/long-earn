# ADR-011: 统一使用 LangChain jinja2 + ChatPromptTemplate 处理提示词

日期: 2026-07
状态: Accepted

## 背景

ADR-008 A 部分曾决策「`${var}` + 纯函数渲染 + **解耦 LangChain**」——新建 `core/render.py`（基于 `string.Template`）+ 重写 `core/prompt_loader.py`，理由是「跨语言可移植」与「删除 80 行转义逻辑」。

落地一年后，该方向暴露出四项结构性成本：

1. **维护一套与生态脱节的自研渲染层**：`core/render.py` + `MarkdownPromptTemplate` 是项目自研，仅支持 `${var}` 单变量插值。LangChain 生态（agent / chain / tool 调用方）默认识别 `PromptTemplate` / `ChatPromptTemplate` 体系，自研类需要手工适配 `input_variables`、`partial_variables`、与 `RunnableSequence` 拼接等。

2. **表达力贫乏**：`${var}` 仅支持单变量替换。条件分支、列表迭代、默认值等场景当前都靠手工拼字符串 + Python `if/else` 在调用方硬编码，模板与逻辑边界混乱。

3. **跨语言可移植性收益未兑现**：ADR-008 选 `${var}` 的核心理由是「Go `os.Expand` / JS 模板字面量 / Rust `envsubst` 同形」。一年后项目仍为纯 Python 单语言实现，`render.py` 跨语言可移植性收益为零。

4. **单字符串 prompt 不区分 system / user / few-shot**：当前所有 agent 把角色定义、用户数据、few-shot 示例揉在一个字符串里 → `llm.invoke(string)`。现代 LLM API（GPT-4 / Claude / GLM）原生是多消息结构，单字符串既不符合 LLM 训练分布（few-shot 效果打折），又难以区分「指令」和「数据」（易受 prompt 注入）。

## 决策

**统一使用 LangChain `PromptTemplate(template_format='jinja2')` + `ChatPromptTemplate` 多消息结构处理提示词**，删除自研渲染层。

> **选型修正（2026-07）**：原 ADR 草稿曾考虑 `langchain_core.prompts.MustacheTemplate`，经核实 `langchain_core` 1.3.x 未导出该独立类。mustache 通过 `PromptTemplate(template_format='mustache')` 调用，但 mustache 规范对 `{{var}}` 双花括号强制 HTML 转义，必须用 `{{{var}}}` 三花括号绕过——LLM 提示词场景无需 HTML 转义（消费者是模型不是浏览器，XSS 威胁模型不适用），三花括号写法啰嗦且不优雅。
>
> **改用 jinja2**：`PromptTemplate(template_format='jinja2')` 默认不转义（`SandboxedEnvironment` 默认 `autoescape=False`），双花括号 `{{ var }}` 即可，与 JSON `{}` 不冲突，写法优雅。底层 `SandboxedEnvironment` 还提供 prompt 注入防护（阻断 `__class__`/`__globals__` 等逃逸）。

### A. 渲染引擎统一为 `PromptTemplate(template_format='jinja2')`

#### A0. 核心原则：系统内不出现任何 HTML 转义

**LLM 提示词消费者是模型而非浏览器，HTML 转义（`<`→`&lt;`、`>`→`&gt;`、`&`→`&amp;`、`"`→`&quot;`）不仅无益，而且有害**——它会污染变量值（如 `if pe < 15:` → `if pe &lt; 15:`、JSON 字符串里的 `&` 被转义破坏 JSON 语义），破坏 LLM 对提示词的理解。XSS 防御的威胁模型（不可信输入插入 HTML 页面）在 LLM 提示词场景不存在。

**经实验确认**（langchain_core 1.3.0 + jinja2 3.1.6，2026-07）：

| 模板引擎 | 变量语法 | 输入 `<strategy> & y` 的输出 | 行为 |
|---------|---------|------------------------------|------|
| mustache `{{var}}` | 双花括号 | `&lt;strategy&gt; &amp; y` | HTML 转义（强制） |
| mustache `{{{var}}}` | 三花括号 | `<strategy> & y` | 不转义 |
| **jinja2 `{{ var }}`** | 双花括号 + 空格 | `<strategy> & y` | **默认不转义** |
| f-string `{var}` | 单花括号 | KeyError（与 JSON `{}` 冲突） | 不可用 |

**选 jinja2**：双花括号默认不转义、与 JSON `{}` 不冲突、写法优雅、有沙箱防护。

**为什么不选 f-string**：f-string 的 `{var}` 与本项目 prompt 中大量字面 JSON `{}` 冲突（[strategy_research_prompt.md](../../src/long_earn/strategy_rd/agents/strategy_research_prompt.md) 等 16 个文件含 134 处字面 `{}`），f-string 会把 `{"key": "value"}` 里的 `"key"` 当变量名抛 KeyError。这正是 ADR-008 之前废弃 f-string 的原因，重启等于走回头路。

#### A1. 占位符语法

- 变量占位符：`${var}` → **`{{ var }}`**（jinja2 双花括号 + 空格，默认不转义）。
- jinja2 控制结构：
  - `{% if show %}...{% endif %}` 条件分支
  - `{% for x in items %}...{% endfor %}` 列表迭代
  - `{{ var | default("N/A") }}` 默认值
  - `{{ var | length }}` / `{{ var | upper }}` 等过滤器
- 缺失变量语义：jinja2 默认输出空串（`Undefined` 渲染为 `""`），与 mustache 一致，与 `safe_substitute` 的"原样保留"不同——调用方需排查对"原样保留"的依赖（预期仅测试）。
- **不支持反向迁移**：`{{ var }}` → `${var}` 的回退路径不再维护。ADR-008 A 部分的 `${var}` 决策被本 ADR 整体废弃。

#### A2. 渲染引擎

- 渲染引擎从 `string.Template.safe_substitute` 切换为 `langchain_core` 内置 jinja2 formatter（`PromptTemplate(template_format='jinja2')` 调用 `SandboxedEnvironment().from_string(template).render(**kwargs)`）。
- 依赖：新增 `jinja2` 包（已在 `pyproject.toml`，~500KB，含 `markupsafe`）。
- 自研 `core/render.py` 删除。
- 全量提示词文件（`.md` prompt）与内联模板字符串从 `${var}` 批量迁移到 `{{ var }}`。

### B. `MarkdownPromptTemplate` 委托 jinja2，保留加载语义

`core/prompt_loader.py` 重写：

- `MarkdownPromptTemplate.__init__` 内部构造 `PromptTemplate(template=template_body, template_format='jinja2')`。
- `.format(**kwargs)` 委托 `PromptTemplate.format(**kwargs)`。
- `.input_variables` 委托 `PromptTemplate.input_variables`。
- `.partial_variables` 通过 `PromptTemplate.partial(**partial)` 实现。
- **保留**：从 `.md` 文件加载、frontmatter 元数据解析（version/description）、`caller_file` 相对路径推断、`from_file` 工厂方法——这些是「Markdown 提示词加载」语义，与渲染引擎无关，不删。
- **删除**：`validate_template` 兼容参数、`from long_earn.core.render import ...` 导入。

### C. 方案 B：`ChatPromptTemplate` 多消息结构（agent 调用模式升级）

当前所有 agent 用 `prompt.format(...) → str → llm.invoke(str)` 单字符串模式。升级为 `ChatPromptTemplate` 多消息结构：

```python
chat_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是{role}分析师，遵循以下规则：{rules}"),
    ("human", "股票数据：{stock_data}"),
    ("human", "市场事件：{event_context}"),
    ("human", "请分析"),
])
messages = chat_prompt.format_messages(role=..., stock_data=..., ...)
response = llm.invoke(messages)
```

**适用范围**：所有调用 `llm.invoke(str)` 的 agent（约 10 个），逐个迁移到 `llm.invoke(messages)`。

**消息划分约定**：
- `system`：角色定义、规则、输出格式约束（稳定，不随调用变化）
- `human`：用户数据（stock_data / events_json / query 等动态内容）
- `ai`：few-shot 示例的 AI 回答（与 `human` 配对）
- `MessagesPlaceholder("examples")`：动态 few-shot 列表（方案 E）

**向后兼容**：`MarkdownPromptTemplate` 保留 `.format()` 返回字符串（用于不需要多消息的场景，如 DSL YAML 渲染、纯文本模板）。新增 `MarkdownChatPromptTemplate` 类支持多消息（基于 `ChatPromptTemplate.from_messages`，从 frontmatter 解析消息划分）。

**frontmatter 扩展**：`.md` prompt 文件 frontmatter 新增 `messages` 字段声明消息划分：

```yaml
---
version: 2.0.0
description: 巴菲特价值投资分析
messages:
  system: |
    你是沃伦·巴菲特，从价值投资角度分析。
    ## 输出格式
    {output_format}
  human: |
    股票数据：{{ stock_data }}
    市场事件：{{ event_context }}
    请给出买入/持有/卖出建议。
---
```

无 `messages` 字段的 `.md` 文件退化为单字符串模式（向后兼容）。

### D. 方案 E：FewShot 动态注入（`MessagesPlaceholder` + `ExampleSelector`）

当前 few-shot 硬编码在 `.md` prompt 里（如 [buffett_prompt.md](../../src/long_earn/stock_analysis/agents/buffett_prompt.md) 第 64-73 行的 `### 示例 1/2/3`）。改为动态注入：

- `MessagesPlaceholder(variable_name="examples")` 在 `ChatPromptTemplate` 中预留 few-shot 槽位。
- 调用方传入 `examples=[HumanMessage(...), AIMessage(...), ...]` 列表。
- **`ExampleSelector`（可选增强）**：`SemanticSimilarityExampleSelector` 根据输入语义相似度动态选最相关的 k 个 few-shot。本 ADR 不强制实施，仅作为后续增强方向记录。

**本 ADR 范围**：仅做基础设施（`MessagesPlaceholder` 支持 + 现有硬编码 few-shot 抽离到 `examples` 参数）。`ExampleSelector` 留作后续独立工作。

### E. 范围边界：提示词 vs DSL YAML 参数插值

- **提示词模板**（`.md` prompt + 内联 prompt 字符串）：**强制切换**到 jinja2，无例外。
- **DSL YAML 参数插值**（`param_grid.render_template`）：**一并切换**到 jinja2。理由：ADR-008 强调「一个项目内只有一种变量语法」。jinja2 `{{ var }}` 与 YAML 字典 `key: value` / flow style `{key: value}` 均不冲突。
- `apply_struct_params`（对象层字段赋值）不依赖文本插值，无需改动。

### F. 退路：不保留 `${var}` fallback

直接替换，不保留 `${var}` 渲染路径作为 fallback。与 ADR-008 一致：「不保留旧路径，避免维护两套的长期成本」。

## 文件结构

### 删除

```
src/long_earn/core/render.py                          # 删除：自研纯函数渲染器
tests/unit/test_backtest/test_render.py               # 删除：render.py 已删
```

### 新增

```
src/long_earn/core/
└── chat_prompt_loader.py                             # 新增：MarkdownChatPromptTemplate（多消息版）
```

### 修改

```
src/long_earn/core/
└── prompt_loader.py                                  # 重写：委托 PromptTemplate(jinja2)

src/long_earn/backtest/engine/
└── param_grid.py                                     # render_template 改用 PromptTemplate(jinja2)

# prompt 批量迁移：${var} → {{ var }}
src/long_earn/stock_analysis/agents/*.md              # 5 个
src/long_earn/strategy_rd/agents/*.md                 # HTR 六步循环 + supervisor 等
src/long_earn/event_inference/agents/*.md             # extract / propagate
src/long_earn/agent.py                                # 内联模板
src/long_earn/stock_analysis/agents/extract_prompt.py
src/long_earn/strategy_rd/agents/strategy_research_prompt.py
src/long_earn/strategy_rd/agents/strategy_rd_supervisor_prompt.py

# agent 调用方迁移到 ChatPromptTemplate（方案 B）
src/long_earn/stock_analysis/agents/*_analyst.py      # 4 个分析师
src/long_earn/event_inference/agents/__init__.py      # EventExtractor / EventPropagator
src/long_earn/strategy_rd/agents/strategy_research_agent.py
src/long_earn/strategy_rd/agents/strategy_develop_agent.py
src/long_earn/agent.py                                # 主 agent

# 测试
tests/unit/test_prompt_loader.py                      # 改用 {{ var }} 断言
tests/integration/test_prompt_loader_integration.py   # 改用 {{ var }} 断言
tests/unit/test_backtest/test_param_grid.py           # 标量插值断言改 {{ var }}
```

### 不变

- `MarkdownPromptTemplate` 的公开 API（`format` / `input_variables` / `from_file` / `caller_file`）签名不变，单字符串调用方零改动。
- frontmatter 元数据解析逻辑不变（仅新增 `messages` 字段可选解析）。
- `caller_file` 相对路径推断不变。

## 理由

1. **回归 LangChain 生态**：`PromptTemplate` / `ChatPromptTemplate` 是 `langchain_core` 一等公民，与 `RunnableSequence` / `LLMChain` / `agent` 体系原生兼容，消除自研适配胶水。
2. **jinja2 默认不转义**：LLM 提示词无需 HTML 转义，jinja2 `SandboxedEnvironment` 默认 `autoescape=False` 是正确默认。mustache 强制转义是规范包袱，f-string 与 JSON `{}` 冲突不可用。
3. **多消息结构对齐 LLM 训练分布**：`ChatPromptTemplate` 把 system / user / few-shot 分离到不同消息，符合现代 LLM 的训练数据格式，few-shot 效果更好，且 system/user 分离降低 prompt 注入风险。
4. **表达力跃升**：jinja2 支持 `{% if %}` / `{% for %}` / `default` / 过滤器，当前调用方大量 `if/else` 拼字符串可以下沉到模板。
5. **沙箱防护**：`SandboxedEnvironment` 阻断 `__class__` / `__globals__` 等逃逸，对处理 LLM 生成的不可信内容有安全收益。
6. **删除而非保留自研**：`core/render.py` 仅 36 行，委托后自研渲染代码归零，维护面收缩。

## 后果

- **新增依赖**：`jinja2` 已加入 `pyproject.toml`（含传递依赖 `markupsafe`）。
- **prompt 文件批量迁移**：`${var}` → `{{ var }}` 全量替换。需配套 grep 确认无遗漏。
- **缺失变量语义变化**：jinja2 默认输出空串（不像 `safe_substitute` 原样保留 `${var}`）。`test_render.py::test_missing_safe` 断言需重写。调用方依赖"原样保留"语义的需排查（预期仅测试）。
- **agent 调用模式变化（方案 B）**：`llm.invoke(str)` → `llm.invoke(messages)`，约 10 个 agent 需迁移。每个 agent 的 prompt 需重新划分 system / human / few-shot 消息。
- **import-linter 合约调整**：ADR-008 的 `render_independent` 合约失效删除。`prompt_loader` 改为依赖 `langchain_core.prompts`。
- **不保留 fallback**：`core/render.py` 删除后，任何残留 `${var}` 模板将原样输出（jinja2 不识别 `${var}`），需迁移完成后通过 grep + 测试卡口防止回退。
- **与 ADR-008 的关系**：ADR-008 A 部分（模板渲染层）被本 ADR **废弃**（Superseded）。ADR-008 B 部分（并行回测编排层）**不受影响**，继续有效——`param_grid.render_template` 内部渲染引擎替换，但 `ParamGrid` / `apply_struct_params` / `ParallelRunner` / `SharedDataContext` / `BacktestService.run_grid` / `run_walk_forward_parallel` 全部不变。

## 分阶段实施计划

> 逐阶段交付，每阶段含测试 + lint。每阶段独立提交。

### 阶段 1：渲染层切换（jinja2 + PromptTemplate）

**目标**：`MarkdownPromptTemplate` 内部委托 `PromptTemplate(template_format='jinja2')`，删除 `core/render.py`。

- 重写 `prompt_loader.py`：构造 `PromptTemplate(template=..., template_format='jinja2')`，`.format` / `.input_variables` / `.partial` 委托。
- 删除 `core/render.py`。
- 删除 `tests/unit/test_backtest/test_render.py`。
- 改写 `test_prompt_loader.py`：`{{ var }}` 断言 + 缺失变量输出空串语义。
- 卡口：`grep -r '\${' src/long_earn/core/` 应无残留。
- 验证：`pytest tests/unit/test_prompt_loader.py tests/integration/test_prompt_loader_integration.py`。
- **提交 1**。

### 阶段 2：prompt 文件批量迁移 `${var}` → `{{ var }}`

**目标**：所有 `.md` prompt 与内联 prompt 字符串从 `${var}` 切换到 `{{ var }}`。

- `stock_analysis/agents/*.md`（5 个）。
- `strategy_rd/agents/*.md`（observe/ideate/select/backpropagate/decide + supervisor 等）。
- `event_inference/agents/*.md`（extract/propagate）。
- 内联模板：`agent.py` / `extract_prompt.py` / `strategy_research_prompt.py` / `strategy_rd_supervisor_prompt.py`。
- 批量替换后 grep 卡口：`grep -rn '\${[a-zA-Z_]' src/long_earn --include='*.md' --include='*.py'` 应无提示词残留（DSL YAML 模板在阶段 3 处理）。
- 验证：全量 prompt 加载测试 + 受影响 agent 的单测。
- **提交 2**。

### 阶段 3：DSL YAML 参数插值迁移

**目标**：`param_grid.render_template` 切换到 `PromptTemplate(template_format='jinja2')`，DSL YAML 模板 `${var}` → `{{ var }}`。

- `param_grid.render_template` 改用 `PromptTemplate(template=..., template_format='jinja2').format(**params)`。
- DSL YAML 模板文件（`best_strategy.yaml` / `profit_growth_strategy.yaml` / 测试用模板）批量迁移。
- `test_param_grid.py` 标量插值断言改 `{{ var }}`。
- 验证：`pytest tests/unit/test_backtest/test_param_grid.py tests/unit/test_backtest/test_parallel.py`。
- **提交 3**。

### 阶段 4：方案 B + E — `ChatPromptTemplate` 多消息 + FewShot

**目标**：agent 调用模式从单字符串升级到多消息；few-shot 从硬编码抽离到 `examples` 参数。

- 新增 `core/chat_prompt_loader.py`：`MarkdownChatPromptTemplate`（从 frontmatter `messages` 字段解析消息划分，基于 `ChatPromptTemplate.from_messages`）。
- 逐个迁移 agent（约 10 个）：
  - `stock_analysis/agents/*_analyst.py`（4 个）：frontmatter 加 `messages` 划分 system/human，`llm.invoke(str)` → `llm.invoke(messages)`。
  - `event_inference/agents/__init__.py`：`EventExtractor` / `EventPropagator` 同上。
  - `strategy_rd/agents/*.py`：HTR 各节点 agent。
  - `agent.py`：主 agent。
- 硬编码 few-shot（如 [buffett_prompt.md](../../src/long_earn/stock_analysis/agents/buffett_prompt.md) 第 64-73 行）抽离到调用方 `examples=[HumanMessage(...), AIMessage(...)]`，通过 `MessagesPlaceholder("examples")` 注入。
- 测试：每个迁移的 agent 加 `test_*_chat_prompt.py` 验证消息结构。
- 验证：全量测试套件。
- **提交 4**。

### 阶段 5：清理与卡口（已实施）

**目标**：固化新约定，防止回退。

- 删除 `import-linter` 的 `render_independent` 合约（若存在）。—— 实施时确认 pyproject.toml 已无该合约，无需操作。
- 新增 CI grep 卡口：`src/long_earn` 下 `.md` / `.py` / `.yaml` 不允许出现 `${var}` 形式的提示词占位符。—— 已加入 `.github/workflows/ci.yml` lint-and-type job。
- 更新 `CLAUDE.md` / 项目文档中关于「提示词模板语法」的说明，指向 ADR-011。—— CLAUDE.md「Prompt 管理」章节 + ADR 列表 + 已完成清单均已更新。
- 验证：全量测试套件 + lint。—— 534 passed。
- **提交 5**。

## 与其他 ADR 的关系

- **ADR-008**（并行回测 + 统一模板渲染）：**A 部分被本 ADR 废弃**（Superseded by ADR-011）。A1（`${var}` 语法）、A2（纯函数渲染器解耦 LangChain）、A3 中 `render_template` 的渲染引擎——三处均被替换。B 部分（并行回测编排层 B1/B2/B3/B4）**完全不受影响**，继续有效。
- **ADR-002**（partial 节点注入）：节点注入用 partial 绑定服务，与渲染引擎无关，本 ADR 不动。
- **ADR-010**（HTR 假设树）：HTR 各节点的 prompt（observe/ideate/backpropagate/decide）随阶段 2 一并迁移到 `{{ var }}`；阶段 4 agent 迁移时这些节点升级到 `ChatPromptTemplate`。executor 内部复用 ADR-008 并行回测的 `run_walk_forward_parallel`，该接口不变。
- **ADR-009**（算子目录）：算子 DSL 模板渲染若依赖 `render()`，随阶段 3 一并迁移。

## 参考文献

- LangChain PromptTemplate: https://python.langchain.com/api_reference/core/prompts/langchain_core.prompts.PromptTemplate.html
- LangChain ChatPromptTemplate: https://python.langchain.com/api_reference/core/prompts/langchain_core.prompts.ChatPromptTemplate.html
- Jinja2 Sandbox: https://jinja.palletsprojects.com/en/3.1.x/sandbox/
- Jinja2 Template Designer Docs: https://jinja.palletsprojects.com/en/3.1.x/templates/
- ADR-008 A 部分（被本 ADR 废弃）: `docs/adr/008-parallel-backtest-and-unified-templating.md`

---

## 附录：增强实时分析能力（原独立 ADR-011 分支，已并入）

> 原文件 `011-enhanced-realtime-analysis.md` 与本 ADR 共用 011 编号但主题不同。为消除编号冲突，将其作为附录并入本文件。状态：已实施。

### 上下文

系统在分析侧有两个能力缺口，二者同源——都依赖**实时数据**但当前缺失：

#### 缺口 1：无实时行情基础设施

数据层（ADR-006/008/009 重构后）的 `DataProvider` Protocol 面向**历史面板**回测：`get_price_panel` / `get_financial_panel` / `get_merged_panel` / `get_merged_panel_as_polars`，所有方法返回 `(date, symbol)` MultiIndex 的 pandas/polars DataFrame，适合批量历史数据喂入回测引擎。但「实时数据对接」要求的能力本质不同：

| 维度 | 历史面板（现有 `DataProvider`） | 实时行情（本附录） |
|------|--------------------------------|------------------|
| 数据形态 | 批量 DataFrame（日级） | 单点 dict（tick 级） |
| 调用模式 | 同步拉取 | 订阅-推送 + 同步快照 |
| 时间跨度 | 过去日期范围 | 当前时刻 |
| 消费方 | 回测引擎 | 告警/监控/实时分析 |

强行把实时能力塞进 `DataProvider` 会污染面向回测的接口。

#### 缺口 2：股票分析子图缺资金流向视角

`stock_analysis` 子图当前 4 视角并行（巴菲特/芒格/费雪/林奇）**全部聚焦基本面与估值**，缺失**主力资金博弈与情绪面**维度。数据能力其实已就绪：ADR-006 引入的 ciccwm 提供了资金流向独占能力，重构后已抽为第二组接口 `MarketIntelligenceProvider.get_fund_flow`，`RuntimeContext.market_intelligence` 已注入。**数据接口就绪但未被 `stock_analysis` 子图消费**。

### 决策

**核心洞察**：两个缺口同源——资金流向分析本质上就是实时行情数据在分析视角的应用。合并为一个 ADR：以**实时行情 Provider** 为基础设施，**资金流向分析师** 为第一个消费者，形成"数据层 → 分析层"的完整闭环。

### 三组数据接口对照

本附录新增第三组接口 `RealtimeDataProvider`，与现有两组并列：

| 接口 | 职责 | 降级链 | 返回类型 | ADR |
|------|------|--------|---------|-----|
| `DataProvider` | 历史面板（行情/财务） | DuckDB→miniqmt→ciccwm→akshare | pandas/polars DataFrame | 006/008 |
| `MarketIntelligenceProvider` | 市场情报（资金流向/排行/板块/资讯） | 无降级，ciccwm 独占 | pandas DataFrame / list | 006/009 |
| **`RealtimeDataProvider`** | 实时行情（快照/订阅） | miniqmt→ciccwm | dict（单点 tick） | **本附录** |

### 1. 实时行情 Provider（基础设施层）

落点：`src/long_earn/backtest/data/realtime.py` —— `RealtimeDataProvider` Protocol + MiniQmt/Ciccwm 实现 + Composite。

```python
class RealtimeDataProvider(Protocol):
    """实时行情数据提供者接口（第三组接口）。"""
    @property
    def is_available(self) -> bool: ...
    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        """获取最新行情快照（同步）。失败返回空 dict。"""
        ...
    def subscribe_quote(self, symbols: list[str], callback: Callable[[dict[str, Any]], None]) -> str:
        """订阅实时行情推送（异步）。不支持订阅时返回空字符串（调用方改用轮询）。"""
        ...
    def unsubscribe(self, subscription_id: str) -> None: ...
```

降级链：`CompositeRealtimeProvider` 优先 miniqmt（xtdata.subscribe_quote + get_full_tick，推送 + 快照），不可用则降级到 ciccwm（fetch_info HTTP 轮询，仅快照无订阅）。`RuntimeContext` 新增可选字段 `realtime_provider: "RealtimeDataProvider | None" = None`，`context_init.py` 构造时注入 `CompositeRealtimeProvider`。

### 2. 价格阈值告警（监控消费者）

作为实时行情的第一个旁路消费者，验证订阅能力：`src/long_earn/monitoring/realtime_alert.py` —— `PriceAlertMonitor`：订阅 `RealtimeDataProvider`，价格突破阈值时回调。告警为旁路，不参与回测主流程，失败不影响策略计算。

### 3. 资金流向分析师（分析消费者）

补齐 `stock_analysis` 子图第 5 视角。落点：`src/long_earn/stock_analysis/agents/fund_flow_analyst.py` + `fund_flow_prompt.md`。与现有 4 个分析师同构（context 依赖注入 + 单一 `analyze(stock_data)` 入口），通过 `MarketIntelligenceProvider.get_fund_flow` 取数。`market_intelligence` 为 `None` 时返回"数据暂不可用"占位（不抛异常、不阻塞其他 4 个分析师）。

`stock_analysis/subgraph.py` 从 4 视角并行扩展为 5 视角：

```
START → get_stock_data → [petter, charles_munger, buffett, fiske, fund_flow] → summarize → END
```

Prompt 聚焦 4 个维度（不重复基本面，只看主力资金博弈）：①主力净流入/流出方向与强度；②大单 vs 中小单背离；③资金流向与价格走势一致性；④当前阶段判断（建仓/拉升/派发/出货）。

### 实施依赖

- **阶段 1**（实时行情基础设施）：`realtime.py` + `realtime_alert.py` + DI 注入 + 测试
- **阶段 2**（资金流向分析师）：`fund_flow_analyst.py` + prompt + 子图接入 + 测试

阶段 2 可独立于阶段 1 实施（`FundFlowAnalyst` 只依赖 `MarketIntelligenceProvider`，不依赖 `RealtimeDataProvider`）；但两者共同构成"实时分析能力增强"的完整图景。

### 验收标准

1. `MiniQmtRealtimeProvider` 在 xtquant 可用时 `get_latest_quote` 返回非空 dict
2. `CiccwmRealtimeProvider` 在 ciccwm 可用时 `get_latest_quote` 返回非空 dict；`subscribe_quote` 返回空 ID
3. `CompositeRealtimeProvider` miniqmt 不可用时降级到 ciccwm
4. `PriceAlertMonitor` 价格突破阈值时触发回调
5. `FundFlowAnalyst.analyze()` 在 `market_intelligence` 可用时返回非空分析文本
6. `market_intelligence` 为 `None` 时返回"数据暂不可用"占位（不抛异常）
7. `stock_analysis` 子图编译成功，5 视角并行汇聚到 summarize
8. 单元测试 mock 数据源，不依赖真实 xtquant/ciccwm
9. `ruff check src/` 零错 + `pytest tests/unit/` 全绿

### 后续

- **近实盘策略接入**：将 `RealtimeDataProvider` 喂入事件驱动引擎的 `on_bar`
- **实时资金流向监控**：`FundFlowAnalyst` 接入 `RealtimeDataProvider`，从离线分析升级为实时监控
- **多源订阅合并**：当 miniqmt + ciccwm 同时订阅时，去重与源标记
- **与 `strategy_rd` 子图联动**：资金流向信号作为策略因子

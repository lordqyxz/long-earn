---
id: 11
title: 统一 jinja2 与 ChatPromptTemplate 提示词体系
status: Accepted
date: 2026-07
summary: 统一采用 jinja2 与 LangChain ChatPromptTemplate；取代 ADR-008 自研渲染层。
related: ["ADR-008"]
---


# ADR-011: 统一 jinja2 与 ChatPromptTemplate 提示词体系


## 背景

ADR-008 A 部分的自研渲染层（`${var}` + `core/render.py`）实现后暴露四项结构性成本：与 LangChain 生态脱节（agent/chain 体系不识别自研类）；单变量插值表达力贫乏（条件/迭代/默认值须由调用方拼字符串）；跨语言可移植收益未兑现（项目仍为纯 Python）；单字符串 prompt 不分 system/user/few-shot，不符 LLM 训练分布，且指令与数据混排难以防范 prompt 注入。

## 决策

### A. 渲染引擎：`PromptTemplate(template_format='jinja2')`

**核心原则：系统内不出现任何 HTML 转义**——prompt 消费者为模型而非浏览器，转义会污染变量值（`if pe < 15:` 变为 `if pe &lt; 15:`、破坏 JSON）且无收益。引擎对比结论：

| 引擎 | 语法 | 输入 `<strategy> & y` | 结论 |
|------|------|----------------------|------|
| mustache `{{var}}` | 双花括号 | HTML 强制转义 | 不可用 |
| mustache `{{{var}}}` | 三花括号 | 不转义 | 写法繁琐 |
| **jinja2** | `{{ var }}` | **默认不转义** | **选用** |
| f-string `{var}` | 单花括号 | 与字面 JSON `{}` 冲突 KeyError | 不可用 |

jinja2 附带 `SandboxedEnvironment` 沙箱（阻断 `__class__`/`__globals__` 逃逸）；支持 `{% if %}` / `{% for %}` / `default` / 过滤器，调用方 if/else 拼串逻辑下沉至模板。缺失变量渲染为空串（非原样保留）。**不保留 `${var}` 回退路径**，`scripts/check_deprecated_syntax.py` 静态检查防止回退。

### B. `MarkdownPromptTemplate` 委托 jinja2

保留 `.md` 加载语义（frontmatter version/description、`caller_file` 相对路径推断、`from_file` 工厂），渲染委托 `PromptTemplate(jinja2)`；公开 API（`format` / `input_variables` / `partial`）不变，单字符串调用方零改动。自研 `core/render.py` 删除。

### C. 多消息结构：`MarkdownChatPromptTemplate`

agent 从 `llm.invoke(str)` 升级至 `llm.invoke(messages)`：system（角色/规则/输出格式约束）+ human（动态数据）+ ai（few-shot 配对）。frontmatter 新增 `messages` 字段声明消息划分，无该字段退化为单字符串（兼容）。few-shot 从硬编码 prompt 抽离至调用方 `examples` 参数，经 `MessagesPlaceholder` 注入。

### D. 范围

提示词模板与 DSL YAML 参数插值（`param_grid.render_template`）一并切换，项目内仅有一种变量语法；`apply_struct_params` 对象层变换不涉及。ADR-008 A 部分整体废弃（Superseded），B 部分不受影响。

## 后果

**正面**

- 与 LangChain 生态一致，agent/chain 体系可直接识别模板类。
- jinja2 支持条件/迭代/默认值，表达力显著优于单变量插值。
- 多消息结构（system/human/ai）符合 LLM 训练分布，指令与数据分离，降低 prompt 注入风险。

**负面**

- 新增 jinja2 依赖；全部 prompt `.md` 与内联模板须批量迁移至 `{{ var }}`。
- 缺失变量语义变化（空串 vs 原样保留），调用方须排查残留依赖。
- `render_independent` 合约删除；`param_grid` 渲染引擎随迁。

**中性**

- ADR-008 A 部分 Superseded；B 部分不受影响。
- 具体实现细节以源码为准。

## 关联

- Supersedes: ADR-008（A 部分）

## 附录：实时分析能力

- **第三组数据接口 `RealtimeDataProvider`**（`backtest/data/realtime.py`）：`get_latest_quote`（同步快照，失败返回空 dict）/ `subscribe_quote`（订阅推送，不支持时返回空串改轮询）/ `unsubscribe`。与 `DataConnector`（历史面板）、`MarketIntelligenceProvider`（市场情报）三组并列；实时为单点 dict/tick 级，不纳入面向批量历史面板的接口。Composite 主源 miniqmt（推送+快照），显式切换次源 ciccwm（HTTP 轮询，仅快照）。
- **价格阈值告警**：`PriceAlertMonitor`（`monitoring/realtime_alert.py`）作为订阅能力第一消费者；旁路监控，不进回测主流程。
- **资金流向分析师**：`stock_analysis` 第 5 视角（`fund_flow_analyst.py` + `fund_flow_prompt.md`），经 `MarketIntelligenceProvider.get_fund_flow` 取数，与其他 4 分析师同构；不可用时返回「数据暂不可用」占位，不抛异常。Prompt 聚焦：主力净流入方向强度 / 大单与中小单背离 / 资金与价格一致性 / 阶段判断（建仓/拉升/派发/出货）。

---
id: 21
title: 确定性脚手架与语言模型推理分层
status: Accepted
date: 2026-08
summary: LLM 推理限于 agent 节点；确定性脚手架与 CI 静态检查守卫调用边界。
related: ["ADR-018", "ADR-022"]
---

# ADR-021: 确定性脚手架与语言模型推理分层


## 背景

open-codereview 的 delegate 模式表明：工具仅承担确定性工程（选定评审文件、解析适用规则），语言模型推理全部交给宿主 agent，工具侧不配置任何模型端点。对照审计 long_earn 全部语言模型调用点后，同一原则在系统内仅半成立。

**已符合分层的实例**

- ToG `ResearchAgent` 的 explore→prune 属语言模型推理，其余均为确定性：算子目录白名单、`prove_causality` 因果性证明、回测/OOS/统计验证门（ADR-022）、写回校验，均为语言模型不可绕过的工具闭包；
- operator_dev 的语言模型生成物过不了 `prove_registration_causality` 即无法进入算子目录；
- event_inference 的冲突分组为语言模型输出后的纯确定性后处理。

**违例（按严重程度排序）**

1. **服务层隐式推理**：`prepare_context` 在上下文准备服务内部经组合根回调触发事件推理子图（内含两次语言模型调用），调用方从服务签名无法感知将发生推理；
2. **数据节点内嵌语言模型且判定顺序颠倒**：stock_analysis 的 `get_stock_data`（数据基础设施位置）首选语言模型抽取标的，确定性字典查找仅为失败时的次级路径；
3. **语言模型用于本可由确定性规则判定的任务**：escape_hatch 用语言模型对 Exception 做失败分类（异常类型本身可确定性判定大半）；HTR 遗留线用语言模型做检索路由（`_should_retrieve`）与循环控制流决策（`decide`，非 JSON 时降级 continue，表明设计不当）。

语言模型调用点散布各层时，「何处发生推理、产生成本与不确定性」不可审计；ToG 线证明「语言模型提议加确定性门裁决」的分层可维护、可测试。

## 决策

### A. 分层原则

1. **语言模型推理只存在于 agent 节点层**（LangGraph 图节点、ReAct 工具闭包、persona 节点）。`services`、`tools`、数据基础设施等脚手架层只产出确定性、类型化的结构化中间态（dataclass），不得内嵌语言模型调用。
2. **确定性规则优先**：路由、分类、解析、文件/路径/标的选择等可用规则、正则、字典判定的，须确定性先行；语言模型仅作未命中时的次级路径，且该路径必须位于 agent 节点层。

### B. 三处违例的整改

| 违例 | 整改 |
|------|------|
| `prepare_context` 隐式推理链 | **修订 ADR-018 §C**：`ContextPreparationService` 降为纯确定性激活，返回结构化中间态 `ContextActivation`（含 `missed` 标记）；事件采集推理由调用方在 agent 层显式触发：ResearchAgent 入口与 `prepare_context` 工具内部显式构造并调用推理子图，app 事件管线同样显式调用。行为语义保持「缺失时自动补采集」，语言模型步骤在控制流中可见、可测 |
| stock_analysis 数据节点内嵌语言模型 | 新增 `resolve_stock_ref` 前置节点：6 位代码正则 → 名称字典查找 → 语言模型次级路径；`get_stock_data` 退为纯确定性取数 |
| escape_hatch 语言模型分类 | 确定性异常类型规则先行（语法、参数、IO 类异常直接判 `fixable`，不消耗语言模型）；未命中规则才走语言模型次级路径 |

HTR 遗留线的语言模型控制流决策（`_should_retrieve` / `decide`）随 HTR 退役专项处理（ADR-010 已 Deprecated，登记于 TODO 当前优先事项）；迁移前冻结：不得新增调用方或在遗留线内扩展功能。

### C. 静态合规检查

`scripts/check_llm_call_sites.py` 扫描语言模型调用标记（`llm_service.invoke`、`require_llm`、`create_llm`、`get_llm`、各家 Chat 客户端构造、`chat.completions`），在白名单（agent 节点与语言模型基础设施，逐条注明架构理由）之外出现即失败，纳入 CI。白名单扩容须同步修改脚本并注明理由，以防止推理点悄悄回流脚手架层。

## 后果

**正面**

- 语言模型成本、延迟与不确定性集中可审计；
- 脚手架层可脱离语言模型独立测试；
- 推理步骤在调用链与代码评审中可见。

**负面**

- `ContextPreparationService.prepare` 签名变化（移除 `force_refresh`，返回 `ContextActivation`）；
- 研究入口与 app 事件管线各自显式构造推理子图（构造成本可忽略）；
- 静态检查白名单须随 agent 层扩容维护。

**中性**

- 研究入口「缺失时自动补采集」语义保留，仅触发点显式化；
- escape_hatch 对确定性可判定异常的结论与语言模型版本一致（`fixable`），仅省去调用。

# ADR-021: 确定性脚手架与 LLM 推理分层

日期: 2026-08
状态: Accepted

## 背景

灵感来自 open-codereview 的 delegate 模式：工具只做确定性工程（选定评审文件、解析适用规则），LLM 推理全部交给宿主 agent，工具侧不配置任何模型端点。对照审计 long_earn 全部 LLM 调用点后发现，同一原则在系统内已「半成立」：

**正面实例**（模式已存在的证明）：

- ToG `ResearchAgent` 的 explore→prune 是 LLM 推理，其余全部确定性：算子目录白名单、`prove_causality` 因果性证明、回测 / OOS / DSR 门、写回校验，都是 LLM 不可绕过的工具闭包。
- operator_dev 的 LLM 生成物过不了 `prove_registration_causality` 就进不了算子目录。
- event_inference 的冲突分组是 LLM 输出后的纯确定性后处理。

**违例**（三类，按严重程度排序）：

1. **服务层隐式推理**：`prepare_context` 在上下文准备服务内部经组合根回调触发事件推理子图（内含两次 LLM 调用），调用方从服务签名完全无法感知会发生推理——正是 delegate 模式要消灭的「工具里藏推理」。
2. **数据节点内嵌 LLM + 兜底顺序颠倒**：stock_analysis 的 `get_stock_data`（数据基础设施位置）首选 LLM 抽取标的，确定性字典查找反而只是失败兜底。
3. **LLM 干确定性活**：escape_hatch 用 LLM 对 Exception 做失败分类（异常类型本身可确定性判定大半）；HTR 遗留线用 LLM 做检索路由（`_should_retrieve`）与循环控制流决策（`decide`，非 JSON 时降级 continue 的补丁即是症状）。

张力：LLM 调用点散布各层时，「哪里会发生推理 / 花钱 / 不确定」不可审计；而 ToG 线证明「LLM 提议 + 确定性门裁决」的分层是可维护、可测试的。

## 决策

### A. 分层铁律

1. **LLM 推理只存在于 agent 节点层**（LangGraph 图节点、ReAct 工具闭包、persona 节点）。`services` / `tools` / 数据基础设施等脚手架层只产出**确定性、类型化的结构化中间态**（dataclass），不得内嵌 LLM 调用。
2. **LLM 不做代码能确定性完成的事**：路由、分类、解析、文件/路径/标的选择等可用规则、正则、字典判定的，必须确定性先行；LLM 仅作未命中兜底，且兜底点必须位于 agent 节点层。

### B. 三处违例的整改

| 违例 | 整改 |
|------|------|
| `prepare_context` 隐式推理链 | `ContextPreparationService` 降为纯确定性激活，返回结构化中间态 `ContextActivation`（含 `missed` 标记）；事件采集推理由调用方在 **agent 层显式触发**：ResearchAgent 入口与 `prepare_context` 工具内部显式构造并调用推理子图，app 事件管线同样显式调用。行为语义保持「miss 自动补采集」，但 LLM 步骤在控制流中可见、可测 |
| stock_analysis 数据节点内嵌 LLM | 新增 `resolve_stock_ref` 前置节点：6 位代码正则 → 名称字典查找 → LLM 兜底；`get_stock_data` 退为纯确定性取数 |
| escape_hatch LLM 分类 | 确定性异常类型规则先行（语法 / 参数 / IO 类异常直接判 `fixable`，不消耗 LLM）；未命中规则才走 LLM 兜底 |

HTR 遗留线的 LLM 控制流决策（`_should_retrieve` / `decide`）随 HTR 清退专项处理（已登记 TODO），迁移前冻结：不得新增调用方或在遗留线内扩展功能。

### C. 执法卡口

`scripts/check_llm_call_sites.py` 扫描 LLM 调用标记（`llm_service.invoke` / `require_llm` / `create_llm` / `get_llm` / 各家 Chat 客户端构造 / `chat.completions`），在白名单（agent 节点与 LLM 基础设施，逐条注明架构理由）之外出现即失败；纳入 CI。白名单扩容必须同步修改脚本并注明理由——这是有意的摩擦，防止推理点悄悄回流脚手架层。

## 后果

- **正向**：LLM 成本 / 延迟 / 不确定性集中可审计；脚手架层可脱离 LLM 独立测试；推理步骤在调用链与代码评审中可见。
- **代价**：`ContextPreparationService.prepare` 签名变化（移除 `force_refresh`，返回 `ContextActivation`）；研究入口与 app 事件管线各自显式构造推理子图（构造成本可忽略）；卡口白名单需随 agent 层扩容维护。
- **行为不变式**：研究入口「miss 自动补采集」语义保留（仅触发点显式化）；escape_hatch 对确定性可判定异常的结论与 LLM 版本一致（`fixable`），仅省去调用。

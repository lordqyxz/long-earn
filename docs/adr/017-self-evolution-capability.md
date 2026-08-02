# ADR-017: 自我进化能力（经验回写/热启动/元指标/失败反思/prompt自审）

日期: 2026-07
状态: Deferred

## 背景

ADR-016 评审时将自我进化能力从分层智能体架构 ADR 中拆出独立评审。拆分理由：

1. **因果链间接**：自我进化优化的是任务执行过程（经验回写、热启动、元指标、失败反思、prompt 自审），不是策略本身。这些能力对策略质量的贡献是间接的、二阶的、未经验证的——而系统的根本目的是"找到最优交易策略"。
2. **时机过早**：ADR-015 的三道统计门刚于 2026-07-27 落地，系统尚未用这套门端到端验证产出过一个稳健策略。在"连稳健策略能不能稳定产出都未验证"的阶段构建"让系统自主改 prompt、自主调参、自主反思架构"的能力，是在未夯实的地基上盖楼。
3. **自我腐化风险**：全自主 prompt 修订与参数调整存在"LLM 修订的 prompt 可能不如人工修订质量高"的风险。需要先有稳健的基线产出作为对照，才能判断自我进化的调整是改善还是劣化。
4. **体量过大**：五项技能、10 个新工具、新增 `evolution/` 模块、版本追溯机制、安全护栏——足以独立成文，不应与架构 ReAct 化捆绑评审。

## 前置条件

本 ADR 的启动需满足以下前置条件，全部满足后方可从 Deferred 转为 Proposed：

1. **ADR-015 统计门端到端验证通过**：三道统计门（Walk-Forward 稳定性 / DSR / PBO）在真实 HTR 运行中至少拦截一次过拟合策略，且至少有一个策略通过全部三道门被合并为 current best——证明统计门有效。
2. **ADR-016 主智能体落地**：MasterAgent + executor 逃生口已交付并稳定运行，主智能体工具集薄封装的迁移已完成——自我进化工具将挂载到主智能体，需要主智能体先就位。
3. **稳健策略基线产出**：系统用 ADR-015 统计门 + MasterAgent / ResearchAgent（ADR-016 / ADR-018）端到端产出至少一个在验证集（forward set）表现稳健的策略——这是自我进化的起点基线，没有基线就无法判断"自我进化是在改善还是在劣化"。

## 决策

待前置条件满足后，为系统补充四项自我进化技能。**全自主落地 + 版本追溯**——不卡人工审批门，但所有自主变更可追溯、可回滚。

### 设计原则

1. **全自主 + 可追溯**：系统自主决策、自主落地，但每次变更写入版本谱系（`metadata.lineage` / `parent_sid` / `version`），任何变更可回溯到触发任务、决策理由、前后差异。
2. **复用现有基建**：记忆系统（ADR-007 Substance）、本体论连接器（ADR-014）已提供物质基础，自我进化是把这些基建接入主智能体作为"进化工具"，而非另起炉灶。
3. **硬约束不降级**：自我进化不得跳过量化铁律——自主回写的经验仍走 OOS 门校验后的真实业绩，自主修订的 prompt 仍需通过 DSL 解析验证。
4. **元认知分离**：执行任务用任务工具，反思任务用进化工具——两类工具在 ReAct 循环中角色不同，避免执行与反思混淆。

### 技能 1：任务级经验回写与热启动

**问题**：当前 `save_experience` 只在策略研发子图内部触发，主智能体完成任务后不反思、不回写，下次相似任务零热启动。

**设计**：主智能体在工具调用结束后，自主调用 `reflect_and_record` 进化工具：

| 工具 | 触发时机 | 行为 |
|------|---------|------|
| `reflect_and_record(task, tool_calls, outcomes)` | 主智能体完成任务、汇总前 | LLM 反思本次任务路径：哪些工具组合有效、哪些调用浪费、失败根因；产出结构化 `TaskExperience` 写入 MemoryService |
| `hot_start(task_query)` | 主智能体接到新任务、分解前 | 检索相似历史任务经验，注入 ReAct system prompt 的"历史经验"段，指导本次工具选择 |

**TaskExperience 数据结构**（存为 Substance `form=KNOWLEDGE`，`metadata` 含结构化字段）：

```python
@dataclass
class TaskExperience:
    task_signature: str          # 任务意图签名（用于相似度匹配）
    task_query: str              # 原始用户查询
    tools_used: list[str]        # 实际调用的工具序列
    outcome: str                 # success / partial / failure
    outcome_metrics: dict        # 关键指标（策略 sharpe / 分析覆盖视角数等）
    lessons: list[str]           # LLM 反思产出的教训
    effective_patterns: list[str]# 有效的工具组合模式
    ineffective_patterns: list[str] # 浪费或失败的调用
    parent_sid: str | None       # 谱系：上一次相似任务的 experience sid
    version: int                 # 版本号，同 task_signature 下递增
```

**版本追溯**：`metadata.version` 同 `task_signature` 下单调递增；`metadata.parent_sid` 指向上版本经验，形成经验谱系树。`hot_start` 检索时优先取最新版本，但可回溯历史版本对比"这次比上次进步了吗"。

**热启动注入**：`hot_start` 返回的结构化经验由主智能体 system prompt 的"历史经验"段渲染，LLM 看到"上次类似任务，先 analyze_stock 再 research_strategy 成功率高，单独 research_strategy 失败率高"这类元知识。

### 技能 2：进化元指标与报告

**问题**：系统跑了很久也没自己发现 prompt 契约不一致、策略研发成功率下降等问题——缺乏元认知指标。

**设计**：新增 `EvolutionMetrics` 收集器 + `report_evolution` 工具：

| 工具 | 行为 |
|------|------|
| `record_task_metric(task, outcome, elapsed, tool_calls)` | 每次任务结束自动记录（由 `reflect_and_record` 内部调用） |
| `report_evolution(window_days=30)` | LLM 聚合窗口内所有 TaskExperience + 策略研发指标，产出可读进化报告：成功率趋势、平均耗时趋势、失败模式 TOP3、有效工具组合、算子目录增长 |

**元指标维度**：

- **策略研发**：成功率（OOS 门通过率）、平均耗时、平均回测次数、最佳 sharpe 趋势、家族切换频率
- **股票分析**：覆盖视角完整度、用户满意度代理（是否有 follow-up 问题）
- **算子进化**：算子目录增长率、自主注册算子使用频次、因果证明通过率
- **工具使用**：各工具调用频次、平均调用链长度、无效调用率
- **prompt 健康**：契约不一致检测数、退役语法回退数（由技能 4 产出）

**存储**：元指标存为 Substance `form=KNOWLEDGE`，`category="进化指标"`，按日聚合。`report_evolution` 检索窗口内指标物质，LLM 聚合为可读报告。

**版本追溯**：每份报告自身也是 Substance，`metadata.window_days` + `metadata.report_date` 标识，可对比"本月 vs 上月"。

### 技能 3：失败信号驱动的自动反思

**问题**：连续多次任务失败或指标下降时，系统不会自动触发架构/prompt/参数的反思与调整——没有"痛觉神经"。

**设计**：双层失败信号检测 + 自动反思：

**第一层（任务内）**：`reflect_and_record` 工具内嵌失败检测——若 `outcome=failure` 且与近期同 `task_signature` 失败率 > 阈值，自动产出 `FailureAnalysis` 物质（`form=KNOWLEDGE`，`category="失败分析"`），含根因假设 + 建议调整方向。

**第二层（跨任务）**：新增 `detect_stagnation(window_days=7)` 工具——主智能体可定期或在 `report_evolution` 发现指标下降时调用，检测：
- 策略研发连续 N 次无 OOS 改善 → 触发"因子族失效"反思
- 某工具调用失败率 > 阈值 → 触发"工具契约问题"反思
- 平均耗时上升趋势 > 阈值 → 触发"效率退化"反思

**自动反思产物**：`AdjustmentProposal` 物质（`form=KNOWLEDGE`，`category="调整提案"`），含：
- `signal_type`：stagnation / contract_violation / efficiency_decay
- `evidence`：触发信号的具体指标与数据
- `hypothesis`：LLM 反思的根因假设
- `proposed_adjustment`：建议调整（调参 / 改 prompt / 切换工具组合 / 研发新算子）
- `expected_effect`：预期改善

**全自主落地**：`AdjustmentProposal` 产出后，主智能体可自主决定是否执行调整——若是调参（如 `HTR_MAX_CYCLES`），直接写入 AppConfig 缓存并记录变更；若是改 prompt，走技能 4 的 prompt 自审计流程。

### 技能 4：Prompt 自我审计与修订

**问题**：实测发现的 6 项 prompt 问题（ADR-009 退役语法、字段表不一致、frontmatter 截断、示例日期过时等）是人工发现的，系统跑了很久也没自己发现——缺乏 prompt 自检能力。

**设计**：`audit_prompts` 工具 + 版本化 prompt 修订：

| 工具 | 行为 |
|------|------|
| `audit_prompts()` | 扫描所有 prompt .md 文件，检查：(a) 是否含已退役语法（`factors:` / `type: filter` / `type: rank` / `type: expression` / `${var}`）；(b) 字段表是否与 `strategy_develop_prompt.md` 权威表一致；(c) frontmatter 是否完整；(d) few-shot 示例日期是否在训练集区间内。产出 `PromptIssue` 列表 |
| `revise_prompt(prompt_file, issue)` | LLM 基于 issue 产出修订版 prompt，写入新版本文件（`xxx_v2.md`），原文件保留；更新 `metadata.version`；记录 `parent_version` 形成版本谱系 |

**版本追溯**：prompt 文件版本化——原文件 `foo.md` 保留，修订版写 `foo_v2.md`，`MarkdownPromptTemplate` 加载时取最高版本。`metadata` 记录 `version` / `parent_version` / `revised_by`（"self_evolution"）/ `revision_reason`。

**全自主落地**：主智能体可定期或在 `detect_stagnation` 发现"工具契约问题"信号时自主调用 `audit_prompts`，发现问题后自主调用 `revise_prompt` 修订并落地，无需人工审批。但修订版与原版都保留，可对比、可回滚。

**安全护栏**（即使全自主也需要的底线）：
- 修订版 prompt 必须通过 `MarkdownPromptTemplate` 加载验证（变量能渲染）
- 修订版 prompt 的 few-shot 示例必须通过算子目录 DSL 解析验证（不引入退役语法）
- 若修订版导致工具调用失败率上升（由元指标监测），自动回滚到上一版本并标记"此修订有害"

### 版本追溯机制

所有自主变更统一用 Substance `metadata` 字段记录版本谱系：

| 变更类型 | Substance form | metadata 字段 |
|---------|---------------|--------------|
| 任务经验 | KNOWLEDGE | `version`, `parent_sid`, `task_signature` |
| 进化指标 | KNOWLEDGE | `window_days`, `report_date` |
| 失败分析 | KNOWLEDGE | `signal_type`, `evidence`, `trigger_task` |
| 调整提案 | KNOWLEDGE | `signal_type`, `proposed_adjustment`, `applied`（落地后置 true） |
| Prompt 修订 | 文件系统 | `foo_v2.md` 文件 + `metadata.version`/`parent_version` |

任何变更可通过 `metadata.parent_sid` / `parent_version` 回溯到触发任务与决策理由，形成完整的进化谱系树。

### 自我进化工具集总览

**主智能体**新增 7 个进化工具（与 ADR-016 的 6 个任务工具并列，主智能体工具总数 13）：

| 工具 | 类型 | 触发 |
|------|------|------|
| `reflect_and_record` | 进化 | 每次任务结束自动 |
| `hot_start` | 进化 | 每次新任务开始自动 |
| `record_task_metric` | 进化 | reflect_and_record 内部自动 |
| `report_evolution` | 进化 | 用户请求 / 定期 / stagnation 触发 |
| `detect_stagnation` | 进化 | 定期 / report_evolution 发现下降时 |
| `audit_prompts` | 进化 | 定期 / detect_stagnation 发现契约问题时 |
| `revise_prompt` | 进化 | audit_prompts 发现问题时 |

> **工具数风险**：主智能体工具总数达 13 个，可能超出弱模型的工具选择能力。落地时需配套工具分组或两级分发设计（任务工具组 + 进化工具组），缓解工具选择质量下降风险。

## 理由

1. **自我进化是二阶能力**：它优化任务执行过程而非策略本身，对策略质量的贡献是间接的。在系统尚未证明能稳定产出稳健策略之前，自我进化的 ROI 无法验证。
2. **前置条件是安全阀**：要求先产出稳健策略基线再启动自我进化，确保有对照来判断"调整是改善还是劣化"。没有基线的自我进化等于盲改。
3. **版本追溯是底线**：即使全自主，所有变更可回溯、可回滚——这是"全自主 + 可追溯"的核心承诺。
4. **拆分独立评审**：自我进化体量大、风险高（自我腐化、误判风险），独立 ADR 让它接受独立的评审与排期，不被架构 ReAct 化抢焦点。

## 后果

**正面（前置条件满足后）**：
- 系统具备元认知能力，能自主发现 prompt 契约问题、成功率下降、工具失败模式
- 任务级经验回写使相似任务热启动，减少重复探索
- prompt 自审计能提前发现退役语法回退与字段不一致

**负面**：
- 主智能体工具数达 13 个，弱模型工具选择质量风险——需配套分组设计
- 全自主 prompt 修订有自我腐化风险——安全护栏是底线但非充分保证
- 自我进化误判风险——系统可能误判失败根因、产出错误的调整提案并自主执行。版本追溯保证可回滚，但需配套"误判监测"（元指标发现调整后指标下降则自动回滚）
- 总 token 成本上升——进化工具的 LLM 调用增加开销

**中性**：
- 本 ADR 从 ADR-016 拆出，独立评审与排期，不影响 ADR-016 的实施
- 算子自研发（原 ADR-016 I.2 技能 2）已留在 ADR-016 中，因它是策略研发核心闭环而非元认知能力

## 与其他 ADR 的关系

- **ADR-007**（物质-运动架构）：`reflect_and_record` / `hot_start` 等工具委托 MemoryService，自我进化的经验/指标/失败分析/调整提案统一存为 Substance，复用双通道检索与版本追溯
- **ADR-011**（jinja2 prompt 模板）：`audit_prompts` / `revise_prompt` 工具检查 prompt .md 文件是否含 `{{ var }}` 语法（非退役 `${var}`），修订版仍用 `MarkdownPromptTemplate` 加载
- **ADR-015**（统计过拟合门）：自我进化的"策略研发成功率"指标以 OOS 门通过率为准，统计门是成功与否的裁判
- **ADR-016**（分层智能体架构）：自我进化工具挂载到 ADR-016 的主智能体，与任务工具并列。算子自研发（原技能 2）已留在 ADR-016 中

## 参考资料

- [Arbor 论文](https://arxiv.org/abs/2606.11926) — insight propagation 是 HTR 核心 driver（自我进化的经验回写受此启发）
- ADR-016 分层智能体架构（本 ADR 的母 ADR）
- ADR-015 统计过拟合门（前置条件依赖）

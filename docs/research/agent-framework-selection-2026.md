# Agent 框架选型调研报告：LangGraph / DeepAgent / Pi Agent 对比

> 调研日期：2026-05-21
> 调研目标：为 long_earn 自我进化量化交易系统选择最符合"自动进化量化金融智能体"长期发展规划的底层 Agent 框架

---

## 1. 执行摘要

**结论：强烈建议继续基于 LangGraph 构建，无需迁移至其他框架。**

LangGraph 是目前唯一能够同时满足量化金融智能体对**复杂状态机编排、确定性执行、生产级持久化、可审计性、多智能体协作**这几大核心需求的框架。DeepAgent 是学术研究性质的通用推理代理，Pi Agent 是编码助手 Harness，两者均不适合替代 LangGraph 作为量化系统的 workflow 编排核心。

建议方向是：**保持 LangGraph 核心，吸收各框架的长处作为增强模块**（如 DeepAgent 的记忆折叠思想、MCP/A2A 协议生态），并在 LangGraph v1 正式版发布后稳步升级。

---

## 2. 当前项目架构分析

### 2.1 现有技术栈

```
long_earn/
├── 主图 (agent.py): 意图分析 → 路由 → 子图
├── strategy_rd 子图: init → retrieval → develop → backtest → reflection → supervisor
├── stock_analysis 子图: 4 视角并行分析 → 汇总
├── 记忆系统: 自研 3-Tier (Working/Core/Archival) + TF-IDF + 关系图
├── 回测引擎: 事件驱动、AST 安全求值器、可见性守护
└── 部署: langgraph.json, RuntimeContext 依赖注入
```

### 2.2 架构关键特征

| 特征 | 说明 | 对框架的要求 |
|------|------|-----------|
| **复杂循环 Workflow** | strategy_rd 包含自适应检索循环、代码修复循环（最多3次）、反射循环 | 必须支持带状态的循环图 |
| **并行多智能体** | stock_analysis 4个分析师视角并行执行 | 必须支持安全的并行节点执行 |
| **持久化与恢复** | 策略研发迭代可能跨会话运行 | 必须支持 checkpoint/中断恢复 |
| **确定性执行** | 回测时间线、风控触发不可随意变化 | 必须支持精确的状态机控制 |
| **可审计性** | 金融级可信要求，杜绝未来函数 | 每一步状态必须可追溯、可重放 |
| **工具安全** | AST 白名单求值，非开放式 eval | 框架不应强制开放式工具发现 |
| **领域耦合** | 回测引擎、记忆系统深度耦合 | 框架需允许自定义节点和状态管理 |

---

## 3. 候选框架深度对比

### 3.1 LangGraph（当前框架）

**定位**：低层级、生产级、状态图驱动的 Agent 编排框架

**核心能力**：
- **状态图 (StateGraph)**：基于 BSP/Pregel 算法的确定性并发执行
- **持久化 (Checkpointing)**：每步自动快照，支持暂停/恢复/时间旅行
- **Human-in-the-Loop**：原生 `interrupt()` / `Command(resume=...)` 模式
- **流式输出**：6 种 stream mode（values/updates/messages/tasks/checkpoints/custom）
- **子图组合**：原生支持嵌套子图、Subgraph → Tool 转换
- **生态整合**：LangSmith（观测）、LangGraph Platform（部署）、MCP（工具协议）、A2A（智能体协议）

**2026 年关键进展**：
- v1.0 里程碑（2025年10月发布）标志着进入稳定生产期
- 功能 API (`@entrypoint`/`@task`) 简化简单工作流
- 原生 MCP 支持，可动态接入外部金融数据工具服务器
- A2A 协议桥接，支持与其他框架的 Agent 协作
- LangGraph Studio：可视化调试 IDE
- 业界已确立为 "Agentic Stack" 的编排层标准（与 MCP 工具层、A2A 协作层并称）

**采用情况**：Uber（代码迁移）、LinkedIn（SQL Bot）、JP Morgan、BlackRock、Klarna、Elastic 等生产级部署。

**劣势**：
- API 学习曲线较陡，嵌套子图的类型安全性仍有提升空间
- 文档碎片化，社区反馈中常提到状态管理直觉性不足
- Checkpoint 序列化在含 pandas DataFrame 等复杂状态时可能失败
- v0 → v1 的升级需要一定适配成本

**与项目匹配度**：⭐⭐⭐⭐⭐（5/5）

---

### 3.2 DeepAgent（RUC-NLPIR，WWW 2026 Oral）

**定位**：端到端深度推理通用 Agent，学术研究成果

**核心创新**：
- **统一推理过程**：在一个连贯的推理流中自主思考、工具发现、动作执行（非 ReAct 的分离循环）
- **自主记忆折叠 (Autonomous Memory Folding)**：将交互历史压缩为结构化记忆（Episodic/Working/Tool），允许 Agent "停下来重新思考"
- **ToolPO**：端到端 RL 训练方法，通过工具模拟器和工具调用优势归因来训练一般性工具使用能力
- **动态工具发现**：支持从 16,000+ API 的工具库中按需求检索

**实验结果**：
- 在 ToolBench、API-Bank、TMDB、Spotify、ToolHop 等通用工具任务上显著超越 ReAct/CodeAct
- 需要 QwQ-32B 等推理模型作为主干，训练需 64x NVIDIA H20 GPU
- GitHub Stars：~1,070，社区规模小

**为什么不适合本项目**：

| 维度 | DeepAgent 设计 | 量化系统需求 | 冲突分析 |
|------|--------------|------------|---------|
| **执行范式** | 开放式自主推理，动态工具发现 | 确定性状态机，预设工具集 | 回测引擎需要严格的事件流控制，开放式工具调用会破坏金融级可信性 |
| **工具安全** | 动态检索并调用任意工具 | AST 白名单，沙箱求值 | 量化系统要求策略代码必须受控执行，不能由 Agent 随意发现新工具 |
| **训练成本** | ToolPO RL 训练需 64x H20 GPU | CPU/consumer GPU 即可运行 | 量化系统的"进化"应体现在策略迭代和记忆积累，而非对基础模型的 RL fine-tune |
| **可控性** | 记忆折叠由 Agent 自主触发 | 风控/止损必须按规则触发 | 金融系统需要将关键决策节点（如风控）置于精确控制之下，而非 Agent 自主决定 |
| **生产支持** | 研究原型，无部署/监控/审计 | 需 LangGraph Platform 级别支持 | 缺乏 dashboard、tracing、企业级运维能力 |

**可借鉴之处**：
- **记忆折叠思想**：本项目自研 3-Tier 记忆可以引入"主动压缩"机制，当上下文过长时由 Agent 触发结构化折叠（JSON schema），减少信息丢失
- **工具使用经验积累**：Tool Memory 的概念可以融入知识库，记录策略参数-表现关系

**与项目匹配度**：⭐⭐（2/5）——仅可作为思想参考，不宜作为替代框架

---

### 3.3 Pi Agent（earendil-works/pi）

**定位**：极简终端编码 Agent Harness / CLI 工具

**核心特点**：
- 面向编码场景的交互式 TUI（树状历史、`/tree` 导航、session 分支）
- 扩展系统（Extensions）：TypeScript 模块，可自定义命令、快捷键、TUI 组件
- Skills 渐进式加载、Prompt Templates、AGENTS.md 项目指令
- 多提供商统一接口（pi-ai 包），支持 15+ providers
- 设计哲学：**Primitives, not features**（提供基础原语，不内置高级功能）

**生态现状**：
- 主仓库是 TypeScript 单仓（pi-mono），Python 生态极弱
- PyPI 上 `pi-agent` 0.1.0（aniketmaurya 实现）为早期实现，仅含基础 agent loop
- 没有 StateGraph、子图、持久化 checkpoint 等 workflow 编排能力
- 没有 LangSmith 级别的观测性

**为什么不适合本项目**：

| 维度 | Pi Agent 设计 | 量化系统需求 |
|------|--------------|------------|
| **运行模式** | 交互式 CLI / TUI | 后台服务 + API + Dashboard |
| **核心能力** | 文件编辑、shell 命令、代码生成 | 复杂状态机、循环 workflow、回测引擎集成 |
| **生态语言** | TypeScript/Node.js | Python（回测、数据、ML） |
| **持久化** | 树状 session 文件 | PostgreSQL/SQLite checkpoint、DuckDB 缓存 |
| **多智能体** | 通过 tmux spawn 实例 | 内置 Supervisor/Worker 子图模式 |

**可借鉴之处**：
- AGENTS.md / SYSTEM.md 的项目指令自动加载模式（本项目已有类似 AGENTS.md 实践）
- Session tree 的可视化导航思想（可作为 LangGraph Studio 的补充）

**与项目匹配度**：⭐（1/5）——生态和定位完全不符

---

### 3.4 其他框架简要评估

| 框架 | 定位 | 为什么不适合 |
|------|------|-----------|
| **Deep Agents (langchain-ai/deepagents)** | LangChain 官方高层 Agent Harness | 底层仍是 LangGraph，是补充而非替代 |
| **CrewAI** | 角色扮演多智能体，快速原型 | 复杂编排天花板低，无 checkpoint |
| **AutoGen** | 微软对话式多智能体 | 研究导向，对话流转为主，非严格状态机 |
| **OpenAI Agents SDK** | OpenAI 生态 Agent | 供应商锁定，与其他框架互操作性差 |

---

## 4. 量化金融场景的特殊考量

### 4.1 "自动进化"不等于"开放式自主"

量化交易系统的"进化"应体现在：
- **策略迭代**：在受控的研发子图中，通过回测反馈闭环优化策略参数和逻辑
- **经验积累**：记忆系统积累过往回测结果、市场规律、有效/无效策略特征
- **参数寻优**：遗传算法/Bayesian Optimization 等结构化方法

而不是让 Agent 无约束地探索"任意工具"或"重写自身代码"。DeepAgent 的"自主发现工具"在金融场景反而是**风险源**：Agent 可能调用未经审计的数据源、生成非安全代码、或在风控逻辑中引入未来函数。

### 4.2 为什么 LangGraph 的"低层级控制"是优势

量化系统需要的不是"最智能的 Agent"，而是"最可控的 Agent 编排"。

LangGraph 的显式状态图让每个决策点、每次状态转换、每个工具调用都**可见、可审计、可回滚**。这与回测引擎的事件溯源 (event sourcing) 设计理念高度一致。

### 4.3 与回测引擎的架构契合

当前项目的事件驱动回测引擎采用严格的事件流隔离：

```
Event Loop → DataHandler → Strategy(on_bar) → Portfolio → Broker
```

LangGraph 的 Pregel 执行模型同样基于离散步骤和状态隔离，两者在架构哲学上完全契合。迁移到 DeepAgent 的"统一推理流"反而会破坏这种严格的时序控制。

---

## 5. 发展路线建议

### 5.1 短期（1-3 个月）：巩固 LangGraph 核心

1. **稳定当前 LangGraph 0.2.x 代码基**
   - 确保所有子图编译通过，state schema 类型安全
   - 利用 `Command` API 减少 conditional edges 的样板代码（v0.2+ 新增）

2. **引入 MCP 协议**
   - 将 `akshare` 数据获取、`tavily` 搜索等工具封装为 MCP Server
   - 通过 `langchain-mcp-adapters` 在 LangGraph 中动态加载
   - 好处：工具标准化、可被其他 MCP Client（Cursor、Claude Desktop 等）复用

3. **增强记忆系统**
   - 吸收 DeepAgent "记忆折叠"思想：当 Working Memory 过长时，触发结构化压缩
   - 引入轻量级本地嵌入模型（`all-MiniLM-L6-v2`）与 TF-IDF 混合检索，替代纯关键词匹配

### 5.2 中期（3-6 个月）：拥抱 v1 和生态

1. **LangGraph v1 升级**
   - 关注 issue #4973 的进展，v1 将改善 API 一致性和类型安全
   - 评估 Functional API (`@entrypoint`/`@task`)：适用于简单分析链路，StateGraph 保留给复杂 workflow

2. **A2A 协议实验**
   - 将 `stock_analysis` 子图暴露为 A2A Agent Card
   - 探索与外部研究 Agent（如行业研报生成 Agent）的协作

3. **LangSmith 全面接入**
   - 对 strategy_rd 和 stock_analysis 建立自动化评估数据集
   - 监控 LLM Token 消耗和回测耗时（符合 TODO 中的性能监控目标）

### 5.3 长期（6-12 个月）：生态融合

1. **DeepAgent 思想借鉴（非替换）**
   - 在策略研发子图引入"策略记忆折叠"：将失败/成功的回测轨迹压缩为结构化经验
   - 探索 Tool Memory：记录不同策略模板在不同市场环境下的成功率

2. **Pi Agent 思想借鉴（非替换）**
   - 评估 AGENTS.md 自动加载机制对本项目开发流程的提升
   - Pi 的 session tree 可视化可作为 LangGraph Studio 的辅助

3. **自进化闭环**
   - 在 LangGraph 框架内实现自动化参数寻优节点（ Bayesian / 遗传算法）
   - 实现策略级别的 A/B 测试和自动推广（类似 inngest-self-learning-agent 的 prompt versioning 模式）

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| LangGraph v1  breaking changes | 中 | 升级成本 | 关注官方迁移指南，利用 v0 → v1 兼容层 |
| LangGraph 状态序列化 bug | 中 | 回测中断 | 自定义 checkpoint serializer，处理 DataFrame/numpy array |
| 社区对 LangGraph 的长期支持 | 低 | 生态停滞 | 业界已标准化为"Agentic Stack"编排层，Sequoia 等顶级 VC 背书 |
| 其他框架出现颠覆性优势 | 低 | 需重新评估 | 持续跟踪框架发展，每季度 review 一次 |

---

## 7. 结论

> **最符合 long_earn "自动进化量化金融智能体"发展规划的框架选择是：继续以 LangGraph 为核心，吸收其他框架的思想和协议生态作为增强，而非替换。**

### 核心论据

1. **LangGraph 是唯一满足全部生产级需求的框架**：复杂循环子图、checkpoint 持久化、确定性并发、Human-in-the-Loop、可观测性、MCP/A2A 协议支持。

2. **DeepAgent 的范式与量化系统冲突**：其"开放式自主推理+动态工具发现"的设计目标与量化系统要求的"严格可控+确定性执行"背道而驰。仅记忆折叠和工具经验记录两个思想值得借鉴。

3. **Pi Agent 定位不同**：编码 CLI Harness，非 workflow 编排框架，且主生态为 TypeScript，与 Python 量化栈不匹配。

4. **迁移成本极高**：当前项目已有 strategy_rd、stock_analysis 两个深度定制的子图，内含自适应检索、代码修复、反射监督等复杂模式。迁移框架意味着重写全部 workflow 逻辑，风险远大于收益。

5. **未来生态兼容**：LangGraph 作为"Agentic Stack"的编排层标准，可无缝接入 MCP 金融数据工具、A2A 外部研究 Agent，确保长期生态兼容性。

### 一句话策略

**"LangGraph 筑基，DeepAgent 取智，MCP/A2A 连横，自研记忆与回测为壁垒。"**

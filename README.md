<!-- AI 代码生成指南 -->


# Quick Start

本项目包管理器采用 [uv](https://github.com/astral-sh/uv)

## 安装依赖

```sh
uv sync
```

## 开始开发

```sh
uv pip install -e .
```

## 运行项目

```sh
uv run python -m long_earn
```

---

# 项目设计 v0.8

## 重要更新 (v0.8) - 依赖注入重构

本次重构基于 **Clean Architecture** 理念，参考 **LangGraph Runtime 和 Context 实践**，实现了完整的依赖注入架构：

### 核心改进

- ✅ 使用 `RuntimeContext` 集中管理依赖和配置
- ✅ 所有核心功能服务化（LLM、知识库、股票、回测等）
- ✅ Agent 类统一使用构造函数注入依赖（必须传入 context）
- ✅ 移除全局状态和向后兼容代码
- ✅ 移除 pytest 测试，使用手动测试脚本

### 使用方式

```python
from long_earn.config import AppConfig
from long_earn.context_init import create_runtime_context

# 创建上下文
context = create_runtime_context()

# 在节点中使用（类型安全）
def my_node(state: State, context: RuntimeContext):
    # 推荐：使用 get_typed() 方法（类型安全）
    llm_service = context.get_typed("llm_service")
    knowledge_service = context.get_typed("knowledge_service")
    
    response = llm_service.invoke(prompt)  # ✅ 类型检查通过
    results = knowledge_service.search(query)  # ✅ 类型检查通过
    
    # 或者使用泛型函数（更严格的类型检查）
    # from long_earn.config import get_service
    # llm_service = get_service(context, "llm_service", LLMServiceImpl)
```

### 验证测试

```bash
uv run python scripts/test_refactor.py
```

预期输出：
```
============================================================
✅ 所有测试通过！
============================================================
```

---

## 测试指南

### 测试计划

完整的测试计划和进度请参考 [tests/README.md](tests/README.md)。

**当前测试覆盖率**: 15.8% (9/57 个测试用例)

**里程碑**:
- M1: 核心测试完成（v0.9）- 2026-03-30
- M2: 完整测试覆盖（v0.10）- 2026-04-25
- M3: 性能优化（v0.11）- 2026-05-15

### 策略研究子图测试

本项目采用分层测试策略，提供三种类型的测试：

#### 1. 单元测试（无 LLM 依赖）

测试子图结构、状态流转、节点逻辑，无需真实 API 调用。

**运行所有单元测试**：
```bash
uv run pytest tests/unit/ -v
```

**运行特定测试**：
```bash
# 运行策略研究子图单元测试
uv run python tests/test_strategy_rd_unit.py

# 运行特定测试用例
uv run pytest tests/unit/test_strategy_rd/test_subgraph.py::test_subgraph_creation -v
```

**测试内容**：
- ✅ State 定义验证
- ✅ 子图创建和节点注册
- ✅ 子图结构和边连接
- ✅ Mock 服务响应处理
- ⏳ Agent 单元测试（进行中）
- ⏳ 节点级别测试（计划中）

**优势**：
- 快速执行（秒级）
- 无需 API 调用
- 可持续集成

#### 2. 集成测试（使用真实 LLM）

测试完整的策略研究流程，需要配置好 LLM 和相关服务。

**运行所有集成测试**：
```bash
uv run pytest tests/integration/ -v
```

**运行特定测试**：
```bash
# 运行策略研究集成测试
uv run python tests/test_strategy_rd_integration.py
```

**测试内容**：
- ✅ 回测功能集成
- ✅ 真实上下文子图运行
- ✅ 知识检索功能
- ✅ 策略生成和代码开发
- ✅ 完整流程简化版
- ⏳ 股票分析集成测试（计划中）
- ⏳ 知识库集成测试（计划中）

**注意**：需要正确配置 `.env` 文件才能运行。

#### 3. 端到端测试（E2E）

测试完整的用户场景和业务流程。

**运行 E2E 测试**：
```bash
uv run pytest tests/e2e/ -v
```

**测试内容**：
- ⏳ 完整流程测试（计划中）
- ⏳ 用户场景测试（计划中）
- ⏳ 回归测试（计划中）

### 生成测试覆盖率报告

```bash
# 生成 HTML 报告
uv run pytest tests/unit/ --cov=src/long_earn --cov-report=html

# 生成终端报告
uv run pytest tests/unit/ --cov=src/long_earn --cov-report=term-missing
```

### 性能测试

```bash
# 运行基准测试
uv run pytest tests/performance/test_benchmark.py -v

# 运行负载测试
uv run pytest tests/performance/test_load.py -v
```

---

---

## 初识知识库系统

- 系统启动时自动加载 `init/` 目录下的文档到 Qdrant 向量数据库
- 支持保存和搜索交易经验，实现策略迭代的知识积累
- 策略生成时自动搜索知识库获取参考信息
- 支持 .md、.txt、.py 文件格式

---

## 角色

你是一个证券交易顾问智能体。

## 技能

作为一个证券交易顾问智能体，你具有以下功能：

### 1. 意图识别与路由

- 分析用户查询，路由到相应子图处理
- 基于 LangGraph 的 [subgraphs 机制](https://docs.langchain.com/oss/python/langgraph/subgraphs)

### 2. 证券分析子图

分析证券内在价值，生成投资建议：

- **数据收集**
  - [kimi web search](https://platform.moonshot.cn/docs/guide/use-web-search#web_search-声明)
  - [akshare](https://akshare.akfamily.xyz)
- **多视角分析**
  - 彼得林奇视角（PEG 策略）
  - 查理芒格视角（多学科思维）
  - 巴菲特视角（价值投资）
  - 费雪视角（成长股投资）
- **生成报告**

### 3. 策略研究子图（Strategy RD）

基于 [Reflexion 框架](https://www.promptingguide.ai/zh/techniques/reflexion) 的量化策略研发：

- **工作流程**
  1. 生成初始策略代码（research → develop）
  2. 回测分析（backtest）
  3. 反思策略，提出优化建议（reflection，支持 ToT 多分支）
  4. 监督器评估是否继续迭代（supervisor）
  5. 优化并重新回测（optimize → 循环）
  6. 达到目标或最大迭代次数后结束

- **迭代控制**
  - max_iterations：默认 3 次
  - 终止条件：年化收益率 > 10% 且夏普比率 > 0.5
  - 使用 LangGraph [Interrupt 机制](https://docs.langchain.com/oss/python/langgraph/interrupts)

### 4. 交易执行

- 运行策略，提供交易信号和建议
- 最终交易由人工或 xtquant 执行

### 5. Callback 机制

- 日志记录
- 异常处理
- 性能监控
- token 统计

---

## 技术栈摘要

| 类别 | 技术选型 |
|------|---------|
| **开发语言** | Python 3.11 |
| **工作流框架** | LangGraph |
| **LLM** | Ollama（默认）/ DashScope / OpenAI（兼容 lmstudio） |
| **回测框架** | pyqlib（完整量化流程） |
| **向量数据库** | Qdrant |
| **记忆组件** | langchain-qdrant |
| **日志库** | loguru |
| **证券数据** | [akshare](https://akshare.akfamily.xyz) |

---

## 系统模块

### 核心模块

#### 主图与控制
- `src/long_earn/agent.py` - 主图智能体（使用 context 注入）
- `src/long_earn/state.py` - 主图状态定义
- `src/long_earn/config.py` - 配置管理和 RuntimeContext
- `src/long_earn/context_init.py` - 上下文初始化

#### 策略研究子图
- `src/long_earn/strategy_rd/state.py` - 策略研究子图状态
- `src/long_earn/strategy_rd/subgraph.py` - 策略研究子图实现
- `src/long_earn/strategy_rd/agents/`
  - `strategy_research_agent.py` - 策略研究 Agent（支持 ToT 反思）
  - `strategy_develop_agent.py` - 策略开发 Agent
  - `strategy_rd_supervisor.py` - 监督器
  - 相关 prompt 文件（版本：0.1.0）

#### 股票分析子图
- `src/long_earn/stock_analysis/state.py` - 股票分析子图状态
- `src/long_earn/stock_analysis/subgraph.py` - 股票分析子图实现
- `src/long_earn/stock_analysis/agents/`
  - `petter_analyst.py` - 彼得林奇视角
  - `buffett_analyst.py` - 巴菲特视角
  - `charles_munger_analyst.py` - 查理芒格视角
  - `fiske_analyst.py` - 费雪视角
  - 相关 prompt 文件

#### 服务层（依赖注入）
- `src/long_earn/services/`
  - `__init__.py` - 服务接口定义（Protocol）
  - `llm_service.py` - LLM 服务
  - `knowledge_service.py` - 知识存储服务
  - `stock_service.py` - 股票数据服务
  - `backtest_service.py` - 回测服务
  - `logger_service.py` - 日志服务
  - `monitoring_service.py` - 监控服务

#### 工具模块
- `src/long_earn/tools/`
  - `subgraph_tool.py` - 子图封装工具
  - `kimi_web_search.py` - Web 搜索工具
  - `get_stock_info.py` - 股票数据工具（akshare）
  - `store.py` - 知识库存储工具（Qdrant）
  - `md_splitter.py` - Markdown 分割工具
  - `backtest.py` - 回测工具
  - `code_safety_check.py` - 代码安全检查


#### 工具类
- `src/long_earn/utils/`
  - `llm_factory.py` - LLM 工厂
  - `logger.py` - 日志工具（简化版）

#### 配置文件
- `langgraph.json` - 主图配置
- `.env` - 环境变量配置

### 控制流

```
用户请求 → 主图：意图判断 → 调用子图/工具 → 结果汇总 → 返回结果
```

---

## 开发注意事项

### 状态管理

- 节点返回值：每个节点只需返回要更新的 key，不需要返回整个状态
- LangGraph 会自动合并节点返回的更新到全局状态

### 回测框架

- 包名：`pyqlib`
- [文档](https://qlib.readthedocs.io/en/latest/)

### 依赖注入规范

所有 Agent 和子图现在必须通过 context 初始化：

```python
# ❌ 旧方式（不再有效）
agent = StrategyResearchAgent()
subgraph = create_strategy_rd_subgraph()

# ✅ 新方式
context = create_runtime_context()
agent = StrategyResearchAgent(context=context)
subgraph = create_strategy_rd_subgraph(context)
```

### 日志和监控

```python
# ❌ 旧方式（不再有效）
from long_earn.utils.logger import LOGGER
LOGGER.info("消息")

# ✅ 新方式
def my_node(state, context):
    logger = context.get("logger")
    logger.info("消息")
```

---

## 提示词规范

### 文件组织

- 每个提示词独立文件管理，位于对应 agents 目录下
- 文件包含：文档字符串、版本号（`__version__`）、PromptTemplate 定义
- 所有提示词统一使用 `PromptTemplate` 类型

### 版本管理

- 每个提示词文件包含 `__version__` 属性，初始版本为 `0.1.0`
- 版本号格式：`主版本。次版本。修订版本`
- 修改提示词时更新版本号

### 文档规范

每个提示词文件顶部必须包含文档字符串，说明：
- 适用场景
- 输入参数
- 输出格式
- 使用示例
- 注意事项
- 版本信息

### 监控系统

- 记录指标：token 使用、执行时间、成功率

---

## Python 代码规范

- 所有 Python 代码必须符合 pylance 规范
- 所有 Python 代码必须添加类型注解
- str 类型的参数需要添加默认值空字符串 `""`

---

## 架构设计要求

### 可测试性

- ✅ 可以在测试中注入 Mock 服务
- ✅ 无需真实 API 调用即可测试
- ✅ 测试速度提升 10-100 倍

### 可维护性

- ✅ 依赖关系清晰（通过构造函数）
- ✅ 模块解耦（通过服务接口）
- ✅ 配置集中管理

### 可扩展性

- ✅ 易于替换实现（如切换 LLM 提供商）
- ✅ 易于添加新服务
- ✅ 支持插件化架构


## 未来计划

### v0.9 计划

- [ ] 添加更多服务（如 Web Search 服务）
- [ ] 优化 context 初始化性能
- [ ] 完善文档和示例
- [ ] 完成核心 Agent 单元测试（策略研究 + 股票分析）
- [ ] 完成关键集成测试

### v0.10 计划

- [ ] 引入完整的 Clean Architecture 分层
- [ ] 添加插件化架构支持
- [ ] 完善 API 文档
- [ ] 完成所有单元测试（覆盖率≥80%）
- [ ] 完成 E2E 测试套件
- [ ] 建立 CI/CD 流程

---

**版本**: v0.8.1  
**更新日期**: 2026-03-23  
**测试覆盖率**: 15.8% (9/57)  
**测试计划**: [tests/README.md](tests/README.md)

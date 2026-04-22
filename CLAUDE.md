# long\_earn

自我进化的量化交易系统（v0.8.0）。基于 LangGraph 的证券交易顾问智能体，支持策略研发和股票分析。

## 常用命令

```sh
uv sync                                    # 安装依赖
uv run python -m long_earn                 # 运行项目
uv run pytest tests/ -v                    # 运行全部测试（含根级测试文件）
uv run pytest tests/unit/ -v               # 仅运行单元测试
uv run pytest tests/integration/ -v        # 仅运行集成测试（需 .env 配置）
uv run ruff check .                        # 单独运行代码检查
uv run ruff format .                       # 单独运行代码格式化
```

## 架构

```
long_earn/
├── src/long_earn/          # 主项目源码
│   ├── core/               # 核心工具（prompt_loader, llm_utils）
│   ├── services/           # 服务接口与实现
│   ├── strategy_rd/        # 策略研发子图
│   │   └── agents/         # 策略研发 Agent（含同目录 .md prompt）
│   ├── stock_analysis/     # 股票分析子图
│   │   └── agents/         # 多视角分析师 Agent（含同目录 .md prompt）
│   └── tools/              # 工具函数（回测、知识库、股票信息）
├── tests/                  # 测试（含根级测试文件）
│   ├── unit/
│   └── integration/
├── backtest_service/       # 独立回测子项目（HTTP API 服务）
└── langgraph.json          # LangGraph 部署配置
```

依赖注入架构，所有服务通过 `RuntimeContext` 传递：

```
AppConfig.from_env()
    ↓
create_runtime_context(config)
    ↓
RuntimeContext(dataclass)
    ├── llm_service: LLMService (Protocol)
    ├── knowledge_service: KnowledgeService (Protocol)
    ├── stock_service: StockService (Protocol)
    ├── backtest_service: BacktestService (Protocol)
    ├── service_manager: ServiceManager (Protocol)
    ├── logger: LoggerService
    ├── monitoring: MonitoringService
    └── config: AppConfig
```

主图（`agent.py`）路由到子图：

- **strategy\_rd**：策略研发（init → 自适应检索循环 → research → develop → backtest → 代码修复循环（最多3次）→ reflection → save\_experience → supervisor → optimize 循环）
- **stock\_analysis**：股票分析（4 视角并行分析后汇总）

## 编码规范

- Python 3.11 严格版本（`requires-python = "==3.11.*"`）
- 所有函数和参数必须添加类型注解
- `str` 类型参数默认值 `""`
- 代码格式和检查：ruff（format + check，88 字符行宽）
- 中文注释和文档字符串

### 依赖注入

所有 Agent 和子图必须通过 `context` 参数初始化：

```python
# 正确
context = create_runtime_context()
agent = StrategyResearchAgent(context=context)

# 错误 — 禁止无 context 创建
agent = StrategyResearchAgent()
```

### 节点返回值

LangGraph 节点只需返回要更新的 key，不需要返回完整状态：

```python
def my_node(state: State, context: RuntimeContext):
    return {"result": "..."}  # 自动合并到全局状态
```

### Prompt 管理

使用 `MarkdownPromptTemplate` 加载 `.md` 文件。变量使用 `{{variable}}` 双花括号语法（自动转换为 LangChain 的 `{variable}`）；代码块内大括号自动转义。frontmatter 可选，支持 `version`/`description` 字段。

```python
from long_earn.core.prompt_loader import MarkdownPromptTemplate
prompt_template = MarkdownPromptTemplate("my_prompt.md", caller_file=__file__)
prompt = prompt_template.format(query=query)
```

**约定**：每个 Agent 的 prompt `.md` 文件与该 Agent 的 `.py` 文件放在同一目录下（例如 `strategy_research_agent.py` 与 `strategy_research_prompt.md` 同在 `agents/` 目录）。

## Gotchas

- **回测服务自动管理**：`local` 模式下 `initialize_context()` 会自动启动回测服务；`remote` 模式下依赖外部运维。默认 `service_manager_type=local`
- **qlib 数据依赖**：回测服务依赖 `~/.qlib_data/cn_data`，如不存在会降级为默认配置
- **Prompt 文件路径**：`MarkdownPromptTemplate` 基于 `caller_file` 解析相对路径，移动 `.md` 文件后需同步修改对应 Agent 中的文件名
- **集成测试需 `.env`**：运行 `tests/integration/` 或根级集成测试文件前需配置环境变量（见下方环境变量表）

## 测试说明

- **根级测试文件**：`tests/` 根目录下存在独立的集成测试文件（如 `test_develop_backtest.py`），运行 `uv run pytest tests/ -v` 时会一并执行
- **集成测试依赖 `.env`**：在项目根目录创建 `.env` 文件，至少配置以下变量：

```sh
LLM_TYPE=ollama
LLM_MODEL=qwen3.5:cloud
LLM_BASE_URL=http://localhost:11434
BACKTEST_SERVICE_URL=http://localhost:8001
```

如使用本地回测服务，确保 `~/.qlib_data/cn_data` 数据已准备，或回测服务会自动降级为默认配置。

## backtest\_service（回测子项目）

`backtest_service/` 是一个具有**完全独立依赖环境**的子项目，通过 HTTP API 远程调用实现回测功能。

- 包qlib安装时名称为pyqlib

**子项目结构：**

```
backtest_service/
├── pyproject.toml              # 独立依赖（含 pyqlib）
├── uv.lock
├── src/long_earn_backtest/     # 包入口
│   ├── __main__.py             # 服务启动入口
│   └── server.py               # FastAPI 服务实现
└── tests/                      # 子项目测试
```

* **依赖隔离**：拥有独立的 `pyproject.toml`、`uv.lock` 和虚拟环境，避免 qlib、protobuf 等包与主项目版本冲突。**主项目不再 import qlib**，所有回测通过 HTTP API 远程调用
* **远程调用**：主项目通过 `httpx` 调用回测服务 API（默认 `http://localhost:8001`），地址由 `BACKTEST_SERVICE_URL` 环境变量控制
* **Unix Domain Socket（推荐单机部署）**：设置 `BACKTEST_SERVICE_UDS=/tmp/backtest.sock` 启动服务，并将 `BACKTEST_SERVICE_URL=http+unix:///tmp/backtest.sock` 设到主项目，可完全绕过 TCP 协议栈，延迟从 ~15ms 降至 <1ms，且无端口占用问题
* **连接池复用**：主项目 `src/long_earn/tools/backtest.py` 内建模块级 `httpx.Client` 单例，多次回测请求复用同一连接池，避免反复 TCP 握手
* **断路器**：内建简易断路器（`_CircuitBreaker`），连续失败 3 次后自动打开，30 秒冷却期后尝试恢复，防止回测服务卡死时拖垮主图循环
* **超时控制**：HTTP 请求默认超时 30 秒，可通过 `BACKTEST_TIMEOUT` 环境变量自定义
* **启动方式**：
  * TCP（默认）：`cd backtest_service && uv sync && uv run python -m long_earn_backtest`
  * UDS：`BACKTEST_SERVICE_UDS=/tmp/backtest.sock uv run python -m long_earn_backtest`
* **服务管理器**：`src/long_earn/services/service_manager.py`
  * `LocalServiceManager`（默认）：通过 `subprocess.Popen` 自动启动/停止本地回测服务，支持 UDS 和 TCP，启动失败时降级为手动提醒
  * `RemoteServiceManager`（`SERVICE_MANAGER_TYPE=remote`）：空实现，不管理进程生命周期
* **API 端点**：`POST /api/v1/backtest`（执行回测）、`GET /health`（健康检查）
* **主项目入口**：`src/long_earn/tools/backtest.py` 中的 `run_backtest()` 和 `check_service_health()`；程序退出前可调用 `close_client()` 释放连接池
* **TODO**：提供面向大模型的 CLI 实现，支持主项目与子服务之间通过命令行互相调用（替代当前 HTTP API 方案，降低部署复杂度）

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| LLM_TYPE | ollama | LLM 类型（ollama/dashscope/openai）|
| LLM_MODEL | qwen3.5:cloud | 模型名称 |
| LLM_BASE_URL | http://localhost:11434 | API 基础 URL |
| QDRANT_URL | :memory: | Qdrant 向量库地址 |
| QDRANT_KEY | - | Qdrant API Key |
| EMBEDDING_MODEL | - | 嵌入模型名称 |
| BACKTEST_SERVICE_URL | http://localhost:8001 | 回测服务地址（支持 `http+unix://` UDS）|
| BACKTEST_SERVICE_UDS | - | 回测服务 Unix Domain Socket 路径（如 `/tmp/backtest.sock`）|
| BACKTEST_TIMEOUT | 30.0 | 回测 HTTP 请求超时（秒）|
| SERVICE_MANAGER_TYPE | local | 服务管理器类型（local/remote）|
| MAX_ITERATIONS | 3 | 策略研发最大迭代次数 |

## 关键约束

- 服务接口定义为 `Protocol` 类（`services/__init__.py`），具体实现在各 `*_service.py` 中
- `context_init.py` 中 `initialize_context()` 会额外调用 `knowledge_service.initialize()` 加载知识库
- 测试中使用 Mock 替代真实服务，无需 API 调用

## 关键文件

| 用途                  | 路径                                         |
| ------------------- | ------------------------------------------ |
| 入口                  | `src/long_earn/__main__.py`                |
| 主图                  | `src/long_earn/agent.py`                   |
| 主图状态                | `src/long_earn/state.py`                   |
| 配置 & RuntimeContext | `src/long_earn/config.py`                  |
| 上下文初始化              | `src/long_earn/context_init.py`            |
| Prompt 加载器          | `src/long_earn/core/prompt_loader.py`      |
| LLM 工具               | `src/long_earn/core/llm_utils.py`          |
| 服务接口                | `src/long_earn/services/__init__.py`       |
| 回测服务实现            | `src/long_earn/services/backtest_service.py` |
| 服务管理器实现            | `src/long_earn/services/service_manager.py` |
| LLM 服务实现            | `src/long_earn/services/llm_service.py`  |
| 知识库服务实现           | `src/long_earn/services/knowledge_service.py` |
| 股票信息服务            | `src/long_earn/services/stock_service.py` |
| 策略研发子图              | `src/long_earn/strategy_rd/subgraph.py`    |
| 策略研发状态              | `src/long_earn/strategy_rd/state.py`       |
| 策略开发 Agent          | `src/long_earn/strategy_rd/agents/strategy_develop_agent.py` |
| 策略研究 Agent          | `src/long_earn/strategy_rd/agents/strategy_research_agent.py` |
| 策略监督器              | `src/long_earn/strategy_rd/agents/strategy_rd_supervisor.py` |
| 股票分析子图              | `src/long_earn/stock_analysis/subgraph.py` |
| 股票分析状态              | `src/long_earn/stock_analysis/state.py`    |
| 知识库工具               | `src/long_earn/tools/store.py`             |
| 回测工具                | `src/long_earn/tools/backtest.py`          |
| 股票信息工具             | `src/long_earn/tools/get_stock_info.py`    |
| 文本分割工具             | `src/long_earn/tools/md_splitter.py`       |
| 回测子项目               | `backtest_service/`（独立依赖，HTTP API 服务）      |
| LangGraph 部署配置      | `langgraph.json`                           |


# long\_earn

自我进化的量化交易系统（v0.7.0）。基于 LangGraph 的证券交易顾问智能体，支持策略研发和股票分析。

## 常用命令

```sh
uv sync                                    # 安装依赖
uv run python -m long_earn                 # 运行项目
uv run pytest tests/unit/ -v               # 运行单元测试
uv run pytest tests/integration/ -v        # 运行集成测试（需 .env 配置）
bash format.sh                             # 代码格式化和检查（ruff format + ruff check）
```

## 架构

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
    ├── logger: LoggerService
    ├── monitoring: MonitoringService
    └── config: AppConfig
```

主图（`agent.py`）路由到子图：

- **strategy\_rd**：策略研发（research → develop → backtest → reflection → optimize → supervisor 循环）
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

使用 `MarkdownPromptTemplate` 加载 `.md` 文件（含 frontmatter），每个 prompt 文件包含 `__version__` 属性。

```python
from long_earn.core.prompt_loader import MarkdownPromptTemplate
prompt_template = MarkdownPromptTemplate("my_prompt.md", caller_file=__file__)
prompt = prompt_template.format(query=query)
```

## backtest\_service（回测子项目）

`backtest_service/` 是一个具有**完全独立依赖环境**的子项目，通过 HTTP API 远程调用实现回测功能。

- 包qlib安装时名称为pyqlib

* **依赖隔离**：拥有独立的 `pyproject.toml`、`uv.lock` 和虚拟环境，避免 qlib、protobuf 等包与主项目版本冲突。**主项目不再 import qlib**，所有回测通过 HTTP API 远程调用
* **远程调用**：主项目通过 `httpx` 调用回测服务 API（默认 `http://localhost:8001`），地址由 `BACKTEST_SERVICE_URL` 环境变量控制
* **超时控制**：HTTP 请求默认超时 30 秒，可通过 `BACKTEST_TIMEOUT` 环境变量自定义
* **启动方式**：`cd backtest_service && uv sync && uv run python -m long_earn_backtest`
* **API 端点**：`POST /api/v1/backtest`（执行回测）、`GET /health`（健康检查）
* **主项目入口**：`src/long_earn/tools/backtest.py` 中的 `run_backtest()` 和 `check_service_health()`

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
| 兼容性补丁               | `src/long_earn/compat.py`（已移除）             |
| Prompt 加载器          | `src/long_earn/core/prompt_loader.py`      |
| 服务接口                | `src/long_earn/services/__init__.py`       |
| 策略研发子图              | `src/long_earn/strategy_rd/subgraph.py`    |
| 策略研发状态              | `src/long_earn/strategy_rd/state.py`       |
| 股票分析子图              | `src/long_earn/stock_analysis/subgraph.py` |
| 股票分析状态              | `src/long_earn/stock_analysis/state.py`    |
| 知识库工具               | `src/long_earn/tools/store.py`             |
| 回测工具                | `src/long_earn/tools/backtest.py`          |
| 回测子项目               | `backtest_service/`（独立依赖，HTTP API 服务）      |
| LangGraph 部署配置      | `langgraph.json`                           |


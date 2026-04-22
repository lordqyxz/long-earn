# Long Earn

自我进化的量化交易系统。基于 LangGraph 的证券交易顾问智能体，支持策略研发和股票分析。

## 系统概览

Long Earn 是一个 AI 驱动的量化交易研究平台，核心能力包括：

- **智能意图路由** — 自动识别用户查询意图，路由到策略研发或股票分析子图
- **策略研发** — 基于 Reflexion 框架的闭环策略研发：研究 → 开发 → 回测 → 反思 → 优化，支持多轮迭代和自适应知识检索
- **多视角股票分析** — 从巴菲特、查理芒格、彼得林奇、费雪四个投资大师视角并行分析股票
- **自我进化** — 策略经验自动沉淀到知识库，后续研发可检索历史经验作为参考
- **独立回测服务** — 回测引擎作为独立 HTTP 服务运行，避免 qlib 等依赖与主项目冲突

## 工作流

```
用户查询
  └─ 主图（意图识别 + 路由）
       ├─ 策略研究子图
       │    └─ 初始检索 → 自适应检索 → 研究 → 开发 → 回测
       │         └─ 回测成功 → 反思 → 保存经验 → 监督器 → 优化（循环）
       │         └─ 回测失败 → 代码修复 → 重新回测
       └─ 股票分析子图
            └─ 数据获取 → 四视角并行分析 → 汇总
                 ├─ 巴菲特视角（价值投资）
                 ├─ 查理芒格视角（多学科思维）
                 ├─ 彼得林奇视角（PEG 策略）
                 └─ 费雪视角（成长股投资）
```

## 快速开始

### 前置条件

- Python 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器
- LLM 服务（默认 Ollama，也支持 DashScope / OpenAI 兼容 API）
- Qdrant 向量数据库（默认内存模式，生产环境建议独立部署）

### 安装

```sh
# 克隆仓库
git clone https://github.com/lordqyxz/long-earn.git
cd long-earn

# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env，至少配置 LLM 相关变量
```

### 运行

```sh
# 启动主项目
uv run python -m long_earn

# 如需回测功能，另启回测服务
cd backtest_service && uv sync && uv run python -m long_earn_backtest
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_TYPE` | LLM 类型 | `ollama` |
| `LLM_MODEL` | 模型名称 | `qwen3.5:cloud` |
| `LLM_BASE_URL` | LLM API 地址 | `http://localhost:11434` |
| `QDRANT_URL` | Qdrant 地址 | `:memory:` |
| `QDRANT_KEY` | Qdrant API 密钥 | 无 |
| `EMBEDDING_MODEL` | 嵌入模型 | `qwen3-embedding:0.6b` |
| `INIT_DIR` | 知识库初始化目录 | `./init` |
| `MAX_ITERATIONS` | 策略研发最大迭代次数 | `3` |
| `BACKTEST_START_DATE` | 回测开始日期 | `2020-01-01` |
| `BACKTEST_END_DATE` | 回测结束日期 | `2023-12-31` |
| `BACKTEST_TIMEOUT` | 回测 HTTP 请求超时（秒） | `30.0` |
| `BACKTEST_SERVICE_URL` | 回测服务地址 | `http://localhost:8001` |

## 架构

系统采用依赖注入架构，所有服务通过 `RuntimeContext` 统一传递：

```
AppConfig.from_env()
    ↓
create_runtime_context(config)
    ↓
RuntimeContext
    ├── llm_service      — LLM 调用
    ├── knowledge_service — 知识库检索与存储
    ├── stock_service     — 股票数据获取
    ├── backtest_service  — 远程回测
    ├── logger            — 日志记录
    ├── monitoring        — 性能监控
    └── config            — 应用配置
```

服务接口定义为 `Protocol` 类，可在测试中轻松替换为 Mock 实现。

### 策略研究子图

基于 [Reflexion](https://www.promptingguide.ai/zh/techniques/reflexion) 框架，闭环迭代优化策略：

1. **自适应检索** — 根据查询自动从知识库检索相关信息，评估是否需要补充检索
2. **策略研究** — LLM 结合知识上下文生成投资策略
3. **策略开发** — 将策略转化为可回测的 pyqlib 代码
4. **回测** — 通过独立回测服务执行回测，失败时自动修复代码（最多 3 次）
5. **反思** — 基于 Tree of Thought 多分支反思，分析回测结果并提出改进方向
6. **保存经验** — 将策略和反思沉淀到知识库
7. **监督器** — 评估是否继续迭代（终止条件：年化收益率 > 10% 且夏普比率 > 0.5）

### 股票分析子图

从四个投资大师视角并行分析股票内在价值，通过 akshare 获取股票和财务数据，支持指数退避重试。

### 回测服务

`backtest_service/` 是独立子项目，拥有自己的依赖环境（pyqlib 等），通过 HTTP API 提供回测能力：

- `POST /api/v1/backtest` — 执行回测
- `GET /health` — 健康检查

主项目通过 `httpx` 调用回测服务，实现依赖隔离。

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.11 |
| 工作流框架 | LangGraph |
| LLM | Ollama（默认）/ DashScope / OpenAI 兼容 API |
| 回测引擎 | pyqlib |
| 向量数据库 | Qdrant |
| 证券数据 | akshare |
| Web 搜索 | Kimi Web Search / Tavily |
| 日志 | loguru |
| 包管理 | uv |

## 测试

```sh
# 单元测试（无需 API 调用）
uv run pytest tests/unit/ -v

# 集成测试（需要 .env 配置）
uv run pytest tests/integration/ -v

# 代码格式化和检查
bash format.sh
```

## 知识库

系统启动时自动加载 `init/` 目录下的文档到 Qdrant 向量数据库，支持 .md、.txt、.py 格式。策略研发过程中自动检索相关知识，成功的策略经验也会沉淀回知识库，实现系统的自我进化。

## 许可

MIT
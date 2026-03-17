<!-- AI 代码生成指南 -->


# Quic Start
本项目包管理器采用 (uv)[]
## 安装依赖

```bash
uv sync
```

## 开始开发
uv pip install -e .

## 运行项目

```bash
uv run 
```

# 项目设计 v0.6

## 知识库系统
- 系统启动时自动加载 `init/` 目录下的文档到 Qdrant 向量数据库
- 策略生成时自动搜索知识库获取参考信息
- 支持 .md、.txt、.py 文件格式

## 角色
你是一个证券交易顾问智能体。

## 技能
作为一个证券交易顾问智能体，你具有以下功能：
- 能根据用户意图执行规划执行相应任务
    - subgraph 实现，[subgraphs 文档](https://docs.langchain.com/oss/python/langgraph/subgraphs)
- 证券分析子图
    分析证券内在价值，实现自动进化的量化策略生成的证券交易顾问。
    - 收集查询证券信息
        - [kimi web search](https://platform.moonshot.cn/docs/guide/use-web-search#web_search-声明)
        - [akshare](https://akshare.akfamily.xyz)
    - 分析证券内在价值
        - 彼得林奇视角
        - 查理芒格视角
        - 巴菲特视角
        - 费雪视角
    - 生成报告
- strategy rd 研究量化策略子图
    基于 Reflexion 框架，[文档](https://www.promptingguide.ai/zh/techniques/reflexion)
    - 生成初始策略代码 (research → develop)
    - 回测分析 (backtest)
    - 反思策略，提出优化建议 (reflection)
    - 监督器评估是否继续迭代 (supervisor)
    - 优化并重新回测 (optimize → 循环)
    - 达到目标或最大迭代次数后结束
        - 迭代控制：max_iterations 默认 3 次
        - 终止条件：年化收益率 > 10% 且夏普比率 > 0.5
        - TODO 用户介入点超时自动进行
        - langgraph 工作流Interrupt 机制实现，[文档](https://docs.langchain.com/oss/python/langgraph/interrupts)
- 运行策略，提供交易信号和建议，最终交易由人工或 xtquant 执行
- callback 设计，
    - 日志记录
    - 异常处理
    - 性能监控
    - token 统计

## 技术栈摘要
- 开发语言：Python3.11
- 工作流框架：LangGraph
- LLM：ollama（默认）/dashcope / openAi兼容本地lmstudio
- 交易框架：pyqlib（完整量化流程：数据、因子、回测）
- 回测数据源：pyqlib 内置
- 记忆组件：langchain-qdrant
- 日志库 ：loguru
- 证券数据获取：[akshare](https://akshare.akfamily.xyz)

## 系统模块

### 核心模块
- src/long_earn/agent.py 实现主图的智能体
- src/long_earn/state.py 定义主图的状态
- src/long_earn/strategy_rd/state：策略研究子图的状态（Reflexion 模式）
    - state.py 定义策略研究子图的状态
    - subgraph.py 实现策略研究子图的文件
    - agents/ 实现策略研究子图的智能体
        - strategy_research_agent.py 反思策略表现，提出优化建议，生成策略，优化目标（收益权重 > 回撤 > 其他）。
        - strategy_research_prompt.py 实现策略研究子图的智能体的提示模板
        - strategy_rd_supervisor.py 实现策略研究子图的监督器，判断是否接受优化建议并重新回测。
        - strategy_rd_supervisor_prompt.py 实现策略研究子图的监督器的提示模板
        - strategy_develop_agent.py 将策略转化成能让 pyqlib 回测的格式：Python 代码文件 + qlib Strategy 注册
        - strategy_develop_prompt.py 实现策略开发子图的智能体的提示模板
    - pyqlib 数据获取、因子计算、回测分析
- src/long_earn/stock_analysis：股票分析子图，用于获取股票数据和计算因子
    - state.py 定义股票分析子图的状态
    - subgraph.py 实现股票分析子图的文件
    - agents/ 实现股票分析子图的智能体
        - petter_analyst.py 实现彼得林奇视角的股票分析智能体
        - petter_prompt.py 实现彼得林奇视角的股票分析智能体的提示模板
        - charles_munger_analyst.py 实现查理芒格视角的股票分析智能体
        - charles_munger_prompt.py 实现查理芒格视角的股票分析智能体的提示模板
        - buffett_analyst.py 实现巴菲特视角的股票分析智能体
        - buffett_prompt.py 实现巴菲特视角的股票分析智能体的提示模板
        - fiske_analyst.py 实现费雪视角的股票分析智能体
        - fiske_prompt.py 实现费雪视角的股票分析智能体的提示模板
- src/long_earn/tools/：自定义工具
    - subgraph_tool.py 将子图封装为工具，用于在主图中调用
    - kimi_web_search.py 实现kimi web search工具，用于搜索互联网
    - get_stock_info.py 实现获取股票/财务数据的工具，使用akshare库
    - tavily_search.py 实现tavily search工具，用于搜索互联网
    - code_safety_check.py 实现代码安全检查工具
- src/long_earn/callbacks/：回调函数包
    - logger.py 实现日志记录回调函数
    - exception.py 实现异常处理回调函数
    - performance.py 实现性能监控回调函数
    - token.py 实现token统计回调函数
- src/long_earn/utils/
    - logger.py 实现日志记录工具,使用loguru库
    - llm_factory.py 实现LLM工厂，用于创建LLM实例
- tests/ 测试用例
    - test_strategy_rd.py 测试策略研究子图
    - test_stock_analysis.py 测试股票分析子图
    - test_tools.py 测试自定义工具
    - test_memory.py 测试向量数据库记忆组件
    - test_strategy_develop.py 测试策略开发子图
    - test_main.py 测试主图
    - test_backtest.py 测试回测机制
- langgraph.json 主图配置文件
- .env 环境变量配置文件


### 控制流
用户请求 → 主图：意图判断 → 调用子图/工具 → 结果汇总 → 返回结果


# 开发注意事项
- 回测框架包名：pyqlib，[文档](https://qlib.readthedocs.io/en/latest/)
- 节点返回值：每个节点只需要返回要更新的key，不需要返回整个状态
- 状态管理：LangGraph会自动合并节点返回的更新到全局状态中
<!-- - 检查点保存：使用SqliteSaver作为检查点保存器，确保工作流状态持久化 -->
- 提示词通过 PromptTemplate 管理

# python 代码规范
- 所有python代码都需要符合PEP8规范
- 所有python代码都需要添加类型注解,str 类型的参数需要添加默认值空字符串 ""

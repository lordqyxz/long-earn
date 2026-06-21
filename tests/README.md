# 测试计划

本文档描述了 Long-Earn 证券交易顾问智能体的完整测试计划和策略。

---

## 目录

- [测试目标](#测试目标)
- [测试策略](#测试策略)
- [测试架构](#测试架构)
- [单元测试计划](#单元测试计划)
- [集成测试计划](#集成测试计划)
- [端到端测试计划](#端到端测试计划)
- [性能测试计划](#性能测试计划)
- [回归测试计划](#回归测试计划)
- [测试环境配置](#测试环境配置)
- [持续集成](#持续集成)
- [测试进度跟踪](#测试进度跟踪)

---

## 测试目标

### 核心目标

1. **确保代码质量**：通过自动化测试保证代码的正确性和可靠性
2. **快速反馈**：测试执行速度快，提供即时反馈
3. **易于维护**：测试代码清晰、易于理解和维护
4. **高覆盖率**：关键业务逻辑测试覆盖率达到 80% 以上
5. **零 LLM 依赖**：单元测试无需真实 API 调用

### 质量指标

| 指标 | 目标值 | 当前状态 |
|------|--------|---------|
| 单元测试覆盖率 | ≥80% | 待统计 |
| 关键路径覆盖率 | 100% | 待统计 |
| 测试执行时间 | <5 分钟 | 待优化 |
| 测试通过率 | 100% | - |
| 回归测试通过率 | 100% | - |

---

## 测试策略

采用分层测试策略，参考 LangGraph 测试最佳实践：

### 测试金字塔

```
        /\
       /  \      E2E 测试 (10%)
      /----\     - 完整流程测试
     /      \    - 用户场景测试
    /--------\   
   /  集成测试 \  - 组件集成
  /------------\ - 服务集成
 /              \
/----------------\ 单元测试 (70%)
```

### 测试分层

#### 1. 单元测试（Unit Tests）
- **目标**：测试单个函数、方法、类
- **特点**：快速、独立、无外部依赖
- **工具**：unittest.mock, pytest
- **覆盖率目标**：80%+

#### 2. 集成测试（Integration Tests）
- **目标**：测试组件间的交互
- **特点**：中等速度、需要部分外部服务
- **工具**：真实服务 + Mock 混合
- **覆盖率目标**：关键路径 100%

#### 3. 端到端测试（E2E Tests）
- **目标**：测试完整业务流程
- **特点**：慢速、需要完整环境
- **工具**：真实环境
- **覆盖率目标**：核心场景 100%

---

## 测试架构

### 项目结构

```
tests/
├── README.md                           # 测试计划（本文档）
├── README_test_report.md               # 测试报告
├── conftest.py                         # pytest 配置和共享 fixtures
├── __init__.py                         # 测试包
│
├── unit/                               # 单元测试
│   ├── __init__.py
│   ├── test_strategy_rd/
│   │   ├── __init__.py
│   │   ├── test_state.py              # 状态定义测试
│   │   ├── test_subgraph.py           # 子图结构测试
│   │   ├── test_agents/
│   │   │   ├── __init__.py
│   │   │   ├── test_strategy_research_agent.py
│   │   │   ├── test_strategy_develop_agent.py
│   │   │   └── test_strategy_rd_supervisor.py
│   │   └── test_nodes/
│   │       ├── __init__.py
│   │       ├── test_init_node.py
│   │       ├── test_research_node.py
│   │       ├── test_develop_node.py
│   │       ├── test_backtest_node.py
│   │       ├── test_reflection_node.py
│   │       └── test_supervisor_node.py
│   │
│   ├── test_stock_analysis/
│   │   ├── __init__.py
│   │   ├── test_state.py
│   │   ├── test_subgraph.py
│   │   └── test_analysts/
│   │       ├── __init__.py
│   │       ├── test_buffett_analyst.py
│   │       ├── test_munger_analyst.py
│   │       ├── test_petter_analyst.py
│   │       └── test_fisher_analyst.py
│   │
│   ├── test_services/
│   │   ├── __init__.py
│   │   ├── test_llm_service.py
│   │   ├── test_knowledge_service.py
│   │   ├── test_backtest_service.py
│   │   ├── test_stock_service.py
│   │   ├── test_logger_service.py
│   │   └── test_monitoring_service.py
│   │
│   ├── test_tools/
│   │   ├── __init__.py
│   │   ├── test_backtest.py
│   │   ├── test_store.py
│   │   ├── test_get_stock_info.py
│   │   ├── test_kimi_web_search.py
│   │   └── test_md_splitter.py
│   │
│   └── test_callbacks/
│       ├── __init__.py
│       ├── test_monitoring.py
│       └── test_exception.py
│
├── integration/                        # 集成测试
│   ├── __init__.py
│   ├── test_strategy_rd_integration.py  # 策略研究集成测试
│   ├── test_stock_analysis_integration.py  # 股票分析集成测试
│   ├── test_knowledge_integration.py     # 知识库集成测试
│   ├── test_backtest_integration.py      # 回测集成测试
│   └── test_llm_integration.py           # LLM 集成测试
│
├── e2e/                                # 端到端测试
│   ├── __init__.py
│   ├── test_full_pipeline.py           # 完整流程测试
│   ├── test_user_scenarios.py          # 用户场景测试
│   └── test_regression.py              # 回归测试
│
├── performance/                        # 性能测试
│   ├── __init__.py
│   ├── test_benchmark.py               # 基准测试
│   ├── test_load.py                    # 负载测试
│   └── test_stress.py                  # 压力测试
│
└── fixtures/                           # 测试数据
    ├── __init__.py
    ├── sample_strategies.py            # 示例策略代码
    ├── mock_data.py                    # Mock 数据
    └── test_documents/                 # 测试文档
        ├── strategy1.md
        └── strategy2.md
```

---

## 单元测试计划

### 策略研究子图（Strategy RD）

#### 1. State 定义测试
**文件**: `tests/unit/test_strategy_rd/test_state.py`

**测试内容**:
- ✅ State TypedDict 字段完整性
- ✅ 字段类型正确性
- ✅ 默认值验证

**状态**: 已完成（部分）

#### 2. 子图结构测试
**文件**: `tests/unit/test_strategy_rd/test_subgraph.py`

**测试内容**:
- ✅ 子图创建成功
- ✅ 所有节点已注册
- ✅ 边连接正确性
- ✅ 条件边逻辑
- ✅ 循环和分支结构

**状态**: 已完成（部分）

#### 3. Agent 单元测试

##### 3.1 StrategyResearchAgent
**文件**: `tests/unit/test_strategy_rd/test_agents/test_strategy_research_agent.py`

**测试内容**:
- [ ] `research_strategy()` 方法
- [ ] `_get_knowledge_context()` 方法
- [ ] 自适应检索逻辑
- [ ] Mock LLM 响应处理
- [ ] 错误处理

**优先级**: 🔴 高

##### 3.2 StrategyDevelopAgent
**文件**: `tests/unit/test_strategy_rd/test_agents/test_strategy_develop_agent.py`

**测试内容**:
- [ ] `develop_strategy()` 方法
- [ ] `refine_code()` 方法
- [ ] 代码生成逻辑
- [ ] 错误修复逻辑
- [ ] Mock LLM 响应处理

**优先级**: 🔴 高

##### 3.3 StrategyRdSupervisor
**文件**: `tests/unit/test_strategy_rd/test_agents/test_strategy_rd_supervisor.py`

**测试内容**:
- [ ] `evaluate_strategy()` 方法
- [ ] `should_continue()` 方法
- [ ] 评估标准逻辑
- [ ] ToT 多分支反思
- [ ] Mock LLM 响应处理

**优先级**: 🔴 高

#### 4. 节点级别测试

**文件**: `tests/unit/test_strategy_rd/test_nodes/`

**测试内容**:
- [ ] `init_node`: 初始化计数器
- [ ] `research_node`: 策略研究
- [ ] `develop_node`: 代码开发
- [ ] `backtest_node`: 回测执行
- [ ] `reflection_node`: 策略反思
- [ ] `optimize_node`: 策略优化
- [ ] `supervisor_node`: 监督器决策

**优先级**: 🟡 中

### 股票分析子图（Stock Analysis）

#### 1. State 定义测试
**文件**: `tests/unit/test_stock_analysis/test_state.py`

**测试内容**:
- [ ] State TypedDict 字段完整性
- [ ] 字段类型正确性

**优先级**: 🟡 中

#### 2. 子图结构测试
**文件**: `tests/unit/test_stock_analysis/test_subgraph.py`

**测试内容**:
- [ ] 子图创建成功
- [ ] 所有节点已注册（4 个分析师 + 数据获取 + 汇总）
- [ ] 并行结构验证
- [ ] 结果汇聚逻辑

**优先级**: 🟡 中

#### 3. 分析师单元测试

**文件**: `tests/unit/test_stock_analysis/test_analysts/`

**测试内容**:
- [ ] `BuffettAnalyst`: 价值投资分析逻辑
- [ ] `CharlesMungerAnalyst`: 多学科思维分析
- [ ] `PetterAnalyst`: PEG 策略分析
- [ ] `FiskeAnalyst`: 成长股投资分析

**优先级**: 🟡 中

### 服务层测试

#### 1. LLM 服务测试
**文件**: `tests/unit/test_services/test_llm_service.py`

**测试内容**:
- [ ] 服务初始化
- [ ] `invoke()` 方法
- [ ] 懒加载逻辑
- [ ] 错误处理
- [ ] 不同 LLM 提供商切换

**优先级**: 🟡 中

#### 2. 知识服务测试
**文件**: `tests/unit/test_services/test_knowledge_service.py`

**测试内容**:
- [ ] `search()` 方法
- [ ] `save()` 方法
- [ ] 过滤逻辑（类别、源文件、词条）
- [ ] 自适应检索
- [ ] 记忆系统连接管理

**优先级**: 🟡 中

#### 3. 回测服务测试
**文件**: `tests/unit/test_services/test_backtest_service.py`

**测试内容**:
- [ ] `backtest()` 方法
- [ ] 绩效指标计算
- [ ] 错误处理
- [ ] 内嵌回测引擎集成

**优先级**: 🟢 低

#### 4. 股票服务测试
**文件**: `tests/unit/test_services/test_stock_service.py`

**测试内容**:
- [ ] 股票信息获取
- [ ] 财务指标获取
- [ ] 价格历史获取
- [ ] akshare 集成

**优先级**: 🟢 低

#### 5. 日志服务测试
**文件**: `tests/unit/test_services/test_logger_service.py`

**测试内容**:
- [ ] 各日志级别方法（debug/info/warning/error/exception）
- [ ] 日志格式
- [ ] 配置化

**优先级**: 🟢 低

#### 6. 监控服务测试
**文件**: `tests/unit/test_services/test_monitoring_service.py`

**测试内容**:
- [ ] `track()` 上下文管理器
- [ ] `track_tokens()` 方法
- [ ] 性能指标收集
- [ ] 报告生成

**优先级**: 🟢 低

### 工具层测试

#### 1. 回测工具测试
**文件**: `tests/unit/test_tools/test_backtest.py`

**测试内容**:
- [ ] `run_backtest()` 函数
- [ ] 策略代码加载
- [ ] 策略文件加载
- [ ] 绩效指标返回

**优先级**: 🟡 中

#### 2. 知识存储工具测试
**文件**: `tests/unit/test_tools/test_store.py`

**测试内容**:
- [ ] `save_experience()` 函数
- [ ] `search_knowledge()` 函数
- [ ] `init_system()` 函数
- [ ] 记忆系统集成

**优先级**: 🟡 中

#### 3. 股票数据工具测试
**文件**: `tests/unit/test_tools/test_get_stock_info.py`

**测试内容**:
- [ ] 股票信息获取
- [ ] 财务指标获取
- [ ] 价格历史获取
- [ ] 错误重试机制

**优先级**: 🟢 低

#### 4. Web 搜索工具测试
**文件**: `tests/unit/test_tools/test_kimi_web_search.py`

**测试内容**:
- [ ] 搜索功能
- [ ] API 调用
- [ ] 结果处理

**优先级**: 🟢 低

#### 5. Markdown 分割工具测试
**文件**: `tests/unit/test_tools/test_md_splitter.py`

**测试内容**:
- [ ] 按标题分割
- [ ] 层级保持
- [ ] 边界条件

**优先级**: 🟢 低

---

## 集成测试计划

### 策略研究集成测试

**文件**: `tests/integration/test_strategy_rd_integration.py`

**测试内容**:
- ✅ 回测功能集成
- ✅ 真实上下文子图运行
- ✅ 知识检索功能
- ✅ 策略生成和代码开发
- ✅ 完整流程简化版

**状态**: 已完成（部分）

**依赖**:
- 真实 LLM 配置
- 3-Tier 记忆系统
- 内嵌回测引擎

### 股票分析集成测试

**文件**: `tests/integration/test_stock_analysis_integration.py`

**测试内容**:
- [ ] 多视角分析集成
- [ ] 并行分析执行
- [ ] 结果汇总逻辑
- [ ] 真实股票数据获取

**优先级**: 🟡 中

**依赖**:
- 真实 LLM 配置
- akshare 数据源

### 知识库集成测试

**文件**: `tests/integration/test_knowledge_integration.py`

**测试内容**:
- [ ] 知识库初始化
- [ ] 文档加载和分割
- [ ] 向量存储和检索
- [ ] 知识保存和查询

**优先级**: 🟡 中

**依赖**:
- 3-Tier 记忆系统

### 回测集成测试

**文件**: `tests/integration/test_backtest_integration.py`

**测试内容**:
- [ ] 回测引擎初始化
- [ ] YAML DSL 策略解析
- [ ] 绩效指标计算
- [ ] 数据预处理

**优先级**: 🟡 中

**依赖**:
- 内嵌回测引擎
- 股票数据

### LLM 集成测试

**文件**: `tests/integration/test_llm_integration.py`

**测试内容**:
- [ ] 不同 LLM 提供商切换
- [ ] 流式响应
- [ ] Token 统计
- [ ] 错误重试

**优先级**: 🟢 低

**依赖**:
- 多个 LLM API Key

---

## 端到端测试计划

### 完整流程测试

**文件**: `tests/e2e/test_full_pipeline.py`

**测试场景**:
- [ ] 用户查询："创建一个动量策略"
  - 预期：策略研究子图执行，生成策略代码并回测
- [ ] 用户查询："分析贵州茅台"
  - 预期：股票分析子图执行，生成分析报告
- [ ] 用户查询："优化我的策略"
  - 预期：策略优化流程执行

**优先级**: 🔴 高

### 用户场景测试

**文件**: `tests/e2e/test_user_scenarios.py`

**测试场景**:
- [ ] 场景 1：新用户首次使用
- [ ] 场景 2：策略迭代优化
- [ ] 场景 3：多股票对比分析
- [ ] 场景 4：知识库积累和使用

**优先级**: 🟡 中

### 回归测试

**文件**: `tests/e2e/test_regression.py`

**测试内容**:
- [ ] 历史 bug 修复验证
- [ ] 关键功能回归
- [ ] API 兼容性检查

**优先级**: 🔴 高

---

## 性能测试计划

### 基准测试

**文件**: `tests/performance/test_benchmark.py`

**测试内容**:
- [ ] 各节点执行时间基准
- [ ] 子图整体执行时间
- [ ] 内存使用基准
- [ ] Token 使用基准

**指标**:
- 策略研究子图：<30 秒/次迭代
- 股票分析子图：<20 秒/次
- 知识检索：<2 秒/次

**优先级**: 🟡 中

### 负载测试

**文件**: `tests/performance/test_load.py`

**测试内容**:
- [ ] 并发用户测试（10 个并发）
- [ ] 长时间运行测试（1 小时）
- [ ] 资源泄漏检测

**优先级**: 🟢 低

### 压力测试

**文件**: `tests/performance/test_stress.py`

**测试内容**:
- [ ] 极限并发测试（100 个并发）
- [ ] 系统崩溃点测试
- [ ] 恢复能力测试

**优先级**: 🟢 低

---

## 回归测试计划

### 自动化回归

**执行时机**:
- 每次代码提交前
- 每日定时执行
- 版本发布前

**测试范围**:
- 所有单元测试（必须 100% 通过）
- 关键集成测试（必须 100% 通过）
- 核心 E2E 场景（必须 100% 通过）

### 回归测试清单

#### 版本发布前必测
- [ ] 策略研究子图完整流程
- [ ] 股票分析子图完整流程
- [ ] 知识库初始化和检索
- [ ] 回测功能
- [ ] LLM 调用
- [ ] 所有单元测试

#### 日常回归
- [ ] 单元测试套件
- [ ] 关键集成测试

---

## 测试环境配置

### 环境变量

创建 `.env.test` 文件用于测试：

```bash
# LLM 配置（测试环境）
LLM_TYPE=ollama
LLM_MODEL=qwen3.5:cloud
LLM_BASE_URL=http://localhost:11434

# Moonshot API（用于 Web 搜索）
MOONSHOT_API_KEY=your_test_api_key

# 记忆系统配置
MEMORY_PATH=~/.long_earn/memory.npz

# 测试专用配置
TEST_MODE=true
LOG_LEVEL=DEBUG
```

### Mock 配置

使用 `tests/fixtures/mock_data.py` 提供统一的 Mock 数据：

```python
from unittest.mock import MagicMock

def create_mock_llm_response(content: str):
    """创建 Mock LLM 响应"""
    mock_response = MagicMock()
    mock_response.content = content
    return mock_response

def create_mock_context():
    """创建 Mock 运行时上下文"""
    from long_earn.config import RuntimeContext
    context = RuntimeContext()
    # 添加 Mock 服务...
    return context
```

### 测试 Fixtures

使用 `tests/conftest.py` 提供共享 fixtures：

```python
import pytest
from long_earn.config import AppConfig, RuntimeContext

@pytest.fixture
def mock_context():
    """Mock 上下文 fixture"""
    # 创建 Mock context
    pass

@pytest.fixture
def real_context():
    """真实上下文 fixture（需要配置）"""
    config = AppConfig.from_env()
    return create_runtime_context(config)

@pytest.fixture
def sample_strategy():
    """示例策略代码 fixture"""
    return """
class TestStrategy:
    def generate_signals(self, date):
        return {"600519": 1.0}
"""
```

---

## 持续集成

### CI/CD 配置

使用 GitHub Actions 或 GitLab CI：

```yaml
# .github/workflows/test.yml
name: Tests

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11"]

    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: ${{ matrix.python-version }}
    
    - name: Install uv
      run: pip install uv
    
    - name: Install dependencies
      run: uv sync --dev
    
    - name: Run unit tests
      run: uv run pytest tests/unit/ -v --cov=src/long_earn
    
    - name: Run integration tests
      run: uv run pytest tests/integration/ -v
      env:
        LLM_TYPE: ${{ secrets.LLM_TYPE }}
        LLM_MODEL: ${{ secrets.LLM_MODEL }}
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
```

### 测试覆盖率要求

- **单元测试覆盖率**: ≥80%
- **关键路径覆盖率**: 100%
- **分支覆盖率**: ≥70%

### 质量门禁

- 测试通过率必须 100%
- 覆盖率不下降
- 无严重性能退化（>10%）

---

## 测试进度跟踪

### 当前状态

| 测试类型 | 总数 | 已完成 | 进行中 | 待开始 | 完成率 |
|---------|------|--------|--------|--------|--------|
| 单元测试 | 45 | 8 | 0 | 37 | 17.8% |
| 集成测试 | 6 | 1 | 0 | 5 | 16.7% |
| E2E 测试 | 3 | 0 | 0 | 3 | 0% |
| 性能测试 | 3 | 0 | 0 | 3 | 0% |
| **总计** | **57** | **9** | **0** | **48** | **15.8%** |

### 详细进度

#### ✅ 已完成（9 个）

**单元测试（8 个）**:
1. ✅ `test_state_definition` - State 定义验证
2. ✅ `test_subgraph_creation` - 子图创建验证
3. ✅ `test_subgraph_structure` - 子图结构验证
4. ✅ `test_init_node` - init 节点逻辑
5. ✅ `test_state_transitions` - 状态流转验证
6. ✅ `test_conditional_edges` - 条件边逻辑
7. ✅ `test_counter_management` - 计数器管理
8. ✅ `test_mock_llm_responses` - Mock LLM 响应

**集成测试（1 个）**:
1. ✅ `test_backtest_integration` - 回测功能集成（部分完成）

#### 🔄 进行中（0 个）

无

#### ⏳ 待开始（48 个）

**高优先级（15 个）**:
1. ⏳ StrategyResearchAgent 单元测试
2. ⏳ StrategyDevelopAgent 单元测试
3. ⏳ StrategyRdSupervisor 单元测试
4. ⏳ 完整流程 E2E 测试
5. ⏳ 回归测试套件
6. ⏳ LLM 服务单元测试
7. ⏳ 知识服务单元测试
8. ⏳ 回测工具单元测试
9. ⏳ 知识存储工具单元测试
10. ⏳ 股票分析子图 State 测试
11. ⏳ 股票分析子图结构测试
12. ⏳ BuffettAnalyst 单元测试
13. ⏳ CharlesMungerAnalyst 单元测试
14. ⏳ PetterAnalyst 单元测试
15. ⏳ FiskeAnalyst 单元测试

**中优先级（20 个）**:
- 节点级别测试（7 个）
- 股票分析集成测试
- 知识库集成测试
- 回测集成测试
- 用户场景 E2E 测试
- 基准性能测试
- 其他服务测试

**低优先级（13 个）**:
- 其他工具测试
- 回调测试
- 负载测试
- 压力测试
- LLM 集成测试

### 里程碑

#### M1: 核心测试完成（v0.9）
- [x] 策略研究子图单元测试框架
- [ ] 所有 Agent 单元测试（预计：2026-03-30）
- [ ] 关键集成测试（预计：2026-04-05）

#### M2: 完整测试覆盖（v0.10）
- [ ] 所有单元测试（预计：2026-04-15）
- [ ] 所有集成测试（预计：2026-04-20）
- [ ] E2E 测试套件（预计：2026-04-25）

#### M3: 性能优化（v0.11）
- [ ] 基准测试（预计：2026-05-01）
- [ ] 性能优化（预计：2026-05-10）
- [ ] 负载和压力测试（预计：2026-05-15）

---

## 运行测试

### 运行所有测试

```bash
# 使用 pytest（推荐）
uv run pytest tests/ -v

# 使用 unittest
uv run python -m unittest discover tests/
```

### 运行特定测试

```bash
# 运行单元测试
uv run pytest tests/unit/ -v

# 运行集成测试
uv run pytest tests/integration/ -v

# 运行特定测试文件
uv run pytest tests/unit/test_strategy_rd/test_agents/test_strategy_research_agent.py -v

# 运行特定测试用例
uv run pytest tests/unit/test_strategy_rd/test_subgraph.py::test_subgraph_creation -v
```

### 生成覆盖率报告

```bash
# 生成 HTML 报告
uv run pytest tests/unit/ --cov=src/long_earn --cov-report=html

# 生成终端报告
uv run pytest tests/unit/ --cov=src/long_earn --cov-report=term-missing
```

### 运行性能测试

```bash
# 运行基准测试
uv run pytest tests/performance/test_benchmark.py -v

# 运行负载测试
uv run pytest tests/performance/test_load.py -v
```

---

## 测试最佳实践

### 1. 测试命名规范

```python
def test_<method>_<scenario>_<expected_result>():
    """测试方法名_场景_预期结果"""
    pass

# 示例
def test_research_strategy_with_empty_query_returns_error():
    """测试研究策略时空查询应返回错误"""
    pass
```

### 2. 测试组织原则

- **AAA 模式**: Arrange（准备）→ Act（执行）→ Assert（断言）
- **单一职责**: 每个测试只验证一个行为
- **独立性**: 测试之间不依赖
- **可重复性**: 测试结果稳定可靠

### 3. Mock 使用指南

```python
from unittest.mock import MagicMock, Mock, patch

# Mock 服务
mock_llm = MagicMock(spec=LLMService)
mock_llm.invoke.return_value = MagicMock(content="响应")

# Mock 上下文
context = RuntimeContext()
context.set("llm_service", mock_llm)

# 使用装饰器 Mock
@patch('long_earn.services.llm_service.LLMServiceImpl')
def test_something(mock_llm_service):
    pass
```

### 4. 测试数据管理

- 使用 `tests/fixtures/` 存储测试数据
- 使用 `conftest.py` 提供共享 fixtures
- Mock 外部依赖（API、数据库）
- 避免硬编码敏感信息

### 5. 错误处理测试

```python
def test_error_handling():
    """测试错误处理"""
    import pytest
    
    with pytest.raises(ValueError) as exc_info:
        # 触发错误的代码
        pass
    
    assert "expected error message" in str(exc_info.value)
```

---

## 附录

### A. 测试工具

- **测试框架**: pytest, unittest
- **Mock 库**: unittest.mock
- **覆盖率**: pytest-cov
- **性能测试**: pytest-benchmark
- **Linting**: ruff, flake8
- **类型检查**: mypy, pyright

### B. 相关文档

- [LangGraph 测试指南](https://langchain-ai.github.io/langgraph/how-tos/testing/)
- [pytest 最佳实践](https://docs.pytest.org/)
- [Python 测试最佳实践](https://docs.python-guide.org/writing/tests/)

### C. 联系方式

如有测试相关问题，请联系开发团队或提交 Issue。

---

**版本**: v0.8.1  
**更新日期**: 2026-03-23  
**维护者**: Long-Earn Team  
**状态**: 📝 进行中

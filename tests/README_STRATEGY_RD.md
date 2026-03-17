# Strategy Rd 子图测试

## 概述

本测试模块用于验证 `strategy_rd` 子图的正常运行。该子图基于 Reflexion 框架实现量化策略的自动研究和优化。

## 子图架构

### 工作流程

```
START → init → research → develop → backtest → reflection → supervisor
                                                    ↓
                                              (条件判断)
                                              ↙        ↘
                                          optimize    end
                                              ↓
                                      develop_optimized
                                              ↓
                                      backtest_optimized
                                              ↓
                                            reflection (循环)
```

### 节点说明

| 节点 | 功能 | 状态键 |
|------|------|--------|
| init | 初始化迭代计数器 | iteration |
| research | 生成初始策略 | strategy |
| develop | 将策略转化为代码 | strategy_code |
| backtest | 执行回测 | backtest_result |
| reflection | 分析回测结果并生成改进建议 | reflection, improvement_suggestions |
| supervisor | 决定是否继续迭代 | should_continue, iteration |
| optimize | 根据改进建议优化策略 | optimized_strategy |
| develop_optimized | 开发优化后的策略代码 | optimized_strategy_code |
| backtest_optimized | 回测优化后的策略 | backtest_result |

## 测试用例

### 1. 集成测试 (TestStrategyRdSubgraph)

- `test_subgraph_creation`: 测试子图创建
- `test_subgraph_initialization`: 测试子图初始化
- `test_subgraph_full_flow_single_iteration`: 测试单次迭代完整流程
- `test_subgraph_with_multiple_iterations`: 测试多次迭代

### 2. 研究节点测试 (TestStrategyResearchNode)

- `test_research_node_success`: 测试研究节点成功

### 3. 开发节点测试 (TestStrategyDevelopNode)

- `test_develop_node_success`: 测试开发节点成功

### 4. 回测节点测试 (TestBacktestNode)

- `test_backtest_node_success`: 测试回测节点成功

### 5. 反思节点测试 (TestReflectionNode)

- `test_reflection_node_success`: 测试反思节点成功

### 6. 监督器节点测试 (TestSupervisorNode)

- `test_supervisor_continue_iteration`: 测试监督器继续迭代
- `test_supervisor_stop_iteration`: 测试监督器停止迭代
- `test_supervisor_max_iterations_reached`: 测试达到最大迭代次数

### 7. 优化流程测试 (TestOptimizeFlow)

- `test_optimize_and_redevelop`: 测试优化和重新开发流程

### 8. 错误处理测试 (TestErrorHandling)

- `test_llm_error_propagation`: 测试 LLM 错误正确传播
- `test_backtest_error_propagation`: 测试回测错误正确传播

### 9. Reflexion 模式测试 (TestReflexionPattern)

- `test_reflexion_single_loop`: 测试单次 Reflexion 循环
- `test_reflexion_multiple_loops`: 测试多次 Reflexion 循环

## 运行测试

### 运行所有测试

```bash
pytest tests/test_strategy_rd.py -v
```

### 运行特定测试类

```bash
pytest tests/test_strategy_rd.py::TestStrategyRdSubgraph -v
```

### 运行特定测试用例

```bash
pytest tests/test_strategy_rd.py::TestStrategyRdSubgraph::test_subgraph_creation -v
```

## 测试覆盖

- 子图创建和初始化
- 完整工作流程 (单次/多次迭代)
- 各节点独立功能
- Reflexion 循环机制
- 错误传播机制

## 重要说明

1. **无 Fallback 机制**: 系统不再有 fallback 机制。当 LLM 调用失败时，错误会正确传播。
2. **Mock 测试**: 所有测试使用 Mock LLM，以确保测试可以在 CI 环境中运行。
3. **错误处理**: 回测节点会捕获异常并返回错误结果，其他节点的错误会传播到调用方。

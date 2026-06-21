# ADR-002: functools.partial 替代闭包进行节点注入

日期: 2024-05
状态: 已采纳

## 背景

策略研发子图 (`strategy_rd/subgraph.py`) 原本使用闭包模式定义 LangGraph 节点：

```python
def create_strategy_rd_subgraph(context):
    agent = StrategyResearchAgent(context=context)
    def research_node(state):
        return agent.research(state["query"])
    workflow.add_node("research", research_node)
```

问题：
- 节点函数定义在闭包内部，无法单独测试
- 闭包捕获的变量隐式、不可见
- 代码可读性差

## 决策

使用 `functools.partial` 显式注入依赖到模块级节点函数。

```python
def _research_node(state, research_agent, logger):
    ...

workflow.add_node("research", partial(_research_node, research_agent=agent, logger=logger))
```

## 理由

1. **可测试性**: 节点函数可独立导入和测试
2. **显式依赖**: 每个节点函数签名清晰声明其依赖
3. **可复用性**: 模块级函数可被多个图复用

## 后果

- 节点函数签名变长（State + 各依赖参数）
- 需要在 `create_strategy_rd_subgraph` 中显式创建 partial
- 所有新子图应遵循此模式

---
id: 2
title: functools.partial 节点依赖注入
status: Accepted
date: 2024-05
summary: 以 functools.partial 显式注入替代闭包，便于 LangGraph 节点独立导入与单测。
---

# ADR-002: 以 functools.partial 进行节点依赖注入


## 背景

策略研发子图曾以闭包定义 LangGraph 节点：节点函数嵌套在工厂函数内，捕获外部 `context` / agent。由此导致：节点无法独立导入与测试；依赖隐式不可见；可读性差。

## 决策

我们将模块级节点函数与 `functools.partial` 结合，显式注入依赖：

```python
def _research_node(state, research_agent, logger):
    ...

workflow.add_node(
    "research",
    partial(_research_node, research_agent=agent, logger=logger),
)
```

## 后果

- **正面**：节点可独立测试；签名声明全部依赖；模块级函数可被多个图复用。
- **负面**：节点签名变长；构图处须显式构造 partial。
- **中性**：新建子图应遵循同一模式。

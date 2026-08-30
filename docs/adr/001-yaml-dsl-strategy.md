---
id: 1
title: YAML DSL 策略描述
status: Accepted
date: 2024-05
summary: 以 YAML DSL 替代 LLM 生成 Python/qlib 策略代码，并将回测引擎内嵌于主项目。
---

# ADR-001: YAML DSL 策略描述


## 背景

早期策略由 LLM 生成 Python 代码（依赖 pyqlib），经独立 HTTP 回测服务执行。主要问题：

- LLM 生成代码语法错误率高，输出不稳定；
- pyqlib 依赖引发版本冲突，需独立子项目；
- HTTP 往返引入额外延迟；
- 经 `eval()` 执行的代码存在注入风险。

## 决策

我们将策略描述迁移为 **YAML DSL**，并将回测引擎内嵌于主项目：

```
旧路径: LLM → Python → HTTP → 外部回测服务 (pyqlib)
新路径: LLM → YAML DSL → 本地事件驱动引擎 → 结果
```

## 后果

- **正面**：声明式结构使 LLM 输出更可控；本地执行无网络开销；移除独立回测子项目，降低部署复杂度。
- **负面**：复杂控制流（任意循环、递归）的表达力受限；须维护 DSL 规范与解析期校验。
- **中性**：表达式求值路径后由 ADR-009 算子目录取代 AST 白名单（ADR-003 已退役）；缓存后端现为 PostgreSQL（ADR-019）。

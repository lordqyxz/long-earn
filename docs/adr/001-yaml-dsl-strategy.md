# ADR-001: YAML DSL 策略描述替代 Python/qlib

日期: 2024-05
状态: 已采纳

## 背景

v0.8 之前，策略由 LLM 生成 Python 代码（依赖 pyqlib），通过独立的 HTTP 回测服务执行。问题：
- LLM 生成的 Python 代码质量不稳定（语法错误率 ~40%）
- pyqlib 依赖导致版本冲突（需要独立子项目管理）
- HTTP 往返延迟 ~15ms
- `eval()` 执行的代码存在注入风险

## 决策

将策略描述从 Python 代码迁移到 **YAML DSL**，回测引擎内嵌到主项目。

```
旧: LLM → Python 代码 → HTTP → backtest_service (pyqlib) → 结果
新: LLM → YAML DSL → 本地引擎 (pandas/numpy) → 结果
```

## 理由

1. **可控性**: YAML 是声明式结构数据，LLM 输出更稳定（错误率从 ~40% 降至预估 ~10%）
2. **安全性**: 表达式通过 AST 白名单求值，无 `eval()` 风险
3. **性能**: 零网络开销，DuckDB 缓存减少数据获取时间
4. **简洁性**: 移除 `backtest_service/` 子项目，降低部署复杂度

## 后果

- 复杂策略的表达能力受限（无法实现循环/递归等动态逻辑）
- 需要维护 YAML DSL 规范和字段校验
- 旧知识库中的 Python 策略经验需要重新适配

# ADR-003: AST 白名单表达式求值替代 eval()

日期: 2024-05
状态: Superseded by ADR-009（2026-07 退役）

## 背景

回测引擎需要执行用户定义的因子表达式（如 `close / shift(close, 20) - 1`）。原实现使用 `eval()`，虽然有 `{"__builtins__": {}}` 限制，但仍存在风险。

## 决策

实现基于 **AST 白名单** 的安全表达式求值器 (`backtest/engine/evaluator.py`)：
- 递归遍历 AST 节点树
- 仅允许白名单中的操作：算术(BinOp)、比较(Compare)、逻辑(BoolOp)
- 仅允许白名单函数：abs/min/max/sum/mean/std 等 numpy 函数
- 未注册的节点类型直接拒绝

## 理由

1. **安全**: 无代码注入风险，攻击面最小化
2. **可控**: 可精确控制支持的操作和函数
3. **调试友好**: 清晰的错误信息（如 "未定义的变量" vs eval 的隐晦错误）
4. **可扩展**: 新增支持的操作只需添加到白名单

## 后果

- 某些 numpy/pandas 高级用法可能不被支持（如 `df.apply(lambda ...)`）
- 比 `eval()` 稍慢（AST 遍历开销，但回测场景下可忽略）
- 需要维护白名单和 AST 节点处理逻辑

## 退役说明（2026-07）

ADR-009 算子目录已全面落地为策略 DSL 的唯一执行路径，本 ADR 的 `SafeExpressionEvaluator` 正式退役：

- **删除文件**：`backtest/engine/evaluator.py` + 对应测试 `tests/unit/test_backtest/test_evaluator.py` 已删除。
- **DSL 收窄**：`backtest/engine/dsl.py` 移除 `factors` 字段、`filter`/`rank`/`expression` 信号类型、`custom_formula`/`signal` 权重方法，解析期强制拒绝旧式语法。
- **策略执行路径统一**：`DSLStrategy.on_bar` 仅走算子目录执行器（`OperatorStrategyExecutor`），不再有 `_eval` 分支。
- **因果性保证迁移**：旧路径靠 AST 白名单"控制可计算什么"保证安全；新路径靠 `prove_causality`（数学证明未来扰动不变性）+ 算子目录白名单（控制可引用哪些算子）共同保证，安全模型从"表达式审查"升级为"数学证明 + 算子审查"。

详见 [ADR-009](009-operator-catalog-and-operator-dev-subgraph.md)。

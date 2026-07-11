# 回测引擎测试组织规范

> 遵循 ADR-013 七维分类框架，每个测试按准确性主题归类。

## 测试目录结构

```
tests/unit/test_backtest/
├── __init__.py
│
├── test_data_correctness/          # D 类：数据正确性
│   ├── __init__.py
│   ├── test_ffill_sorted.py        # D2 ffill 排序（防止未来函数泄漏）
│   ├── test_pit_regression.py      # D3 PIT 对齐（披露日可见性）
│   └── test_universe_pit.py        # D1 幸存者偏差（TODO）
│
├── test_temporal_bias/             # T 类：时序偏差
│   ├── __init__.py
│   ├── test_causality.py           # T1/T2 因果性（扰动后半段，断言前半段不变）
│   ├── test_t1_execution.py        # T1 T+1 信号执行
│   └── test_walk_forward.py        # T3/T4 Walk-Forward 正确性（TODO）
│
├── test_execution/                 # E 类：交易执行
│   ├── __init__.py
│   ├── test_broker.py              # E1-E8 撮合/成本/滑点
│   ├── test_impact_model.py        # E2 冲击模型验证
│   └── test_advanced_orders.py     # 限价/止损/OCO 订单
│
├── test_compliance/                # M 类：A 股合规
│   ├── __init__.py
│   └── test_compliance.py          # M1-M6 T+1/涨跌停/成交量/过户费/停牌
│
├── test_portfolio/                 # P 类：投资组合与风控
│   ├── __init__.py
│   ├── test_portfolio.py           # P1 现金约束/P2 行业集中度
│   └── test_risk.py                # P3 止盈/P4 换手率/P5 杠杆
│
├── test_metrics/                   # C 类：指标计算
│   ├── __init__.py
│   └── test_numerics.py            # C1-C5 Sharpe/Alpha/Beta 公式对齐
│
├── test_audit/                     # A 类：审计
│   ├── __init__.py
│   └── test_audit_flow.py          # A1-A4 审计完整性
│
├── test_engine.py                  # 引擎集成测试（主流程 + Walk-Forward）
├── test_dsl.py                     # DSL 解析测试
├── test_evaluator.py               # 表达式求值测试
├── test_visibility.py              # 可见性守卫测试
├── test_data_provider.py           # 数据 Provider 契约测试
├── test_provider_pit_contract.py   # Provider PIT 契约测试
└── test_parallel.py                # 并行回测测试
```

## 测试标记

| 标记 | 用途 |
|---|---|
| `@pytest.mark.regression` | 回归测试（防回退） |
| `@pytest.mark.causality` | 因果性证明（无未来函数） |
| `@pytest.mark.compliance` | A 股合规约束 |
| `@pytest.mark.slow` | 慢测试（>1s） |

## 每类测试必须断言的内容

- **数据正确性**：assert 公告日前为 NaN，assert ffill 不泄漏未来
- **时序偏差**：assert 前半段权益不变（因果性），assert T+1 执行
- **交易执行**：assert 税费/滑点/过户费数值正确
- **A 股合规**：assert ORDER_SKIPPED 审计事件
- **指标计算**：assert value == pytest.approx(numpy_direct, rel=1e-5)
- **风控**：assert RISK_TRIGGER 审计事件，assert 持仓清空
- **审计**：assert 全部 12 种事件类型，assert 因果链完整

# 统计验证门控调研与落地（2026-08-31）

> 范围：对照现代量化实践，评估并加固 Long Earn「正确性 / 质量」双层门控。  
> 方法：OpenCodeReview 专项结论 + 文献/开源闸门对照 + 源码修复。  
> 决策锚点：ADR-022；实现：`overfit_gates.py` / `ResearchAgent.run_oos_gates`。

## 1. 问题陈述

业界把两类失败分开：

1. **正确性（Correctness）**：回测是否在 PIT / 可交易约束下计算（前视、成本、制度）。
2. **质量（Quality / Edge）**：在可信回测之上，选出的策略是否仍可能是多重检验噪音。

Quantopian 等实证强调：样本内夏普对样本外几乎无预测力；试得越多，IS–OOS 落差越大。
因此「能跑通回测」≠「可合并 / 可写 success」。

## 2. 对照来源

| 来源 | 要点 |
|------|------|
| Bailey & LdP DSR / PSR / MinTRL | 多重检验与非正态下的夏普可信度；轨迹长度下限 |
| Bailey et al. PBO + CSCV | T×N 收益矩阵组合对称交叉验证 |
| Harvey–Liu / HLZ RFS | 因子发现的多重检验折减 |
| AFML Ch.7/12 | purge / embargo；CPCV 补 WF、不替代 WF |
| [factor-qc](https://github.com/foolproof-labs/factor-qc) | fail-closed：DSR/PBO/haircut/MinTRL 作 P0；缺 `n_trials` 拒评判 |
| ADR-013 / 引擎 | VisibilityGuard、因果证明、制度撮合 |

论文索引与映射：[papers/README.md](papers/README.md)、[papers/statistical-gates-mapping.md](papers/statistical-gates-mapping.md)。

## 3. 修复前缺口 → 修复后状态

| 缺口 | 修复 |
|------|------|
| success 写回可仅凭训练集 | **P0**：`oos_passed=True` 才可 success；train-only → `candidate` |
| PBO 仅 pair 置换，非 CSCV | **P1**：`evaluate_returns_matrix`（T×N）；≥2 列走矩阵，否则 pair_legacy / skipped |
| DSR 用 worst_fold + 矩来源不清 | **P1**：观测夏普优先 `oos_mean`；标注 `moments_source` / `observed_sharpe_source` |
| 无 MinTRL / haircut | **P1**：诊断挂入 `dsr.mintrl` / `dsr.haircut`（不硬拒） |
| WF train/test 紧贴可能泄漏 | **P2**：`run_oos` 默认 `gap=5` 贯穿 ParallelRunner / core |
| 文档仅 ToG 论文 | **docs**：扩展 `papers/` 索引 + 本笔记 + gates 映射 |

## 4. 仍开放项

- DSR/PBO **升硬门**需单独 ADR（契约：持久 registry、相关 \(N_{\mathrm{eff}}\)、默认矩阵 CSCV）
- 跨 invoke 持久化 current best / 候选矩阵
- 完整 AFML purge（按标签视界）与 CPCV
- 纸面/模拟对账闸（ADR-022 L2）后再谈进化改参
- 幸存者偏差数据面仍弱于制度撮合面

## 5. 验收

```text
uv run pytest tests/unit/test_strategy_rd/test_research_agent.py \
  tests/unit/test_strategy_optimization/test_overfit_gates.py \
  tests/unit/test_backtest/test_timeseries_split.py -v
```

期望：写回契约、CSCV/MinTRL/haircut、gap 隔离相关用例全绿。

# ADR-015: 统计过拟合门与反馈闭环修复

日期: 2026-07-27
状态: Accepted, Implemented
关联: 增强 ADR-010 合并门；补齐 ADR-005/013 在统计显著性维度的空白（013 是方法论清单，本 ADR 是可执行代码层）

## 背景

实证：`ProfitGrowthStrategy` Q1 2026 收益 -5.48% / Q2 +20.09%，窗口极度不一致，却通过了旧 OOS 合并门（`oos_sharpe=0.51`）--拟合特定市场风格而非稳健 alpha。代码审查发现五个根本断点：

1. OOS 只读跨折平均 sharpe，完全忽略 `fold_results` 稳定性（`[0.3, -0.5, 1.4]` 与 `[0.4, 0.4, 0.4]` 等价）；
2. 合并门无多重检验校正（N 轮尝试 family-wise error 线性累积，Bailey & López de Prado 所述经典场景）；
3. 失败信号不上行（rejected 节点不写 backtest_result，LLM 反思时看不到失败原因只能瞎猜）；
4. select 多样性逻辑失效（`max_select >= K` 候选数时全选同方向）；
5. frontier 语义错误（executor 置 VALIDATED 后 frontier 永远空，「前沿控制」从未生效）。

**核心思想：统计显著性是过拟合的唯一标准答案**；补齐统计门之前不改进探索算法--否则只是更高效地找到过拟合策略。

## 决策

### Tier S：三道统计门（在 `_evaluate_oos_and_merge` 内串行追加，缺一不可）

**S1. Walk-Forward 稳定性门 `WalkForwardStabilityGate`**--防单策略窗口不稳定。三道硬条件任一不满足即拒：最差 fold sharpe > -0.1（容忍微小负噪声）；fold 间 sharpe 标准差 < 0.8；正 sharpe 折数占比 >= 2/3。直接拒绝 Q1/Q2 不一致型策略。

**S2. Deflated Sharpe Ratio 门 `DeflatedSharpeGate`**--防单策略不显著（multiple testing selection bias）。Bailey & López de Prado (2014) 简化版（不含 skew/kurt 校正，因 fold_results 无日收益序列）：`sr_se = 1/sqrt(n_observations)`，`E[max_N] ≈ sqrt(2 ln N)`（N = 累积尝试数 tree.node_count），`t_stat = (observed_sharpe - E[max_N]·sr_se) / sr_se > 1.96` 通过。

**S3. PBO 概率门 `BacktestOverfitGate`**--防多策略集合 selection bias（「取 N 个中最优」的过拟合）。Bailey et al. (2017) CSCV：N 个候选 (dev_sharpe, oos_sharpe) 配对对称重排采样，计算「IS 最优在 OOS 劣于中位数」频率，PBO > 0.5 拒绝。需维护历史全部候选配对列表。

### Tier A：失败反馈闭环

rejected 节点也写 `backtest_result` + `rejection_reason`；`_collect_tried_directions` 含 dev_score / 失败原因 / step_failures 并递归收集；backpropagate prompt 显式提取失败原因供 LLM 针对性反思（而非瞎猜）。

### Tier B：探索机制修复

`is_frontier()` 扩展含 VALIDATED（已验证未充分展开的叶节点仍是探索候选）；select 改 strict set 语义（强制不同 direction，候选不足时重新生成或降级全选并记警告）。

## 后果

- 合并门变严，HTR/ResearchAgent 产出率短期下降（期望行为：拒绝「看似 OOS 通过实则过拟合」的策略，不浪费 held-out 测试集）；PBO CSCV 采样 ~100ms 可接受；三道门阈值为经验值，需实际运行调优；DSR 简化版对非正态收益检验力下降（完整版需引擎保留日收益序列，留待后续）。
- 调研结论明确不走 UCB/MCTS 数值搜索（量化 sharpe 噪声大收敛不可靠）、暂不做 Arbor expand/prune 三动作与 QD behavioral_descriptor--均为后续可选 ADR。
- 参考来源：[Deflated Sharpe Ratio (2014)](https://doi.org/10.3905/jpm.2014.40.5.094) / [PBO (2017)](https://doi.org/10.3905/jpm.2017.43.4.041) / [Arbor](https://arxiv.org/abs/2606.11926)。

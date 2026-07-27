# ADR-015: 统计过拟合门与反馈闭环修复

## 状态

Accepted（2026-07-27）

## 背景

ADR-010 落地了 HTR 六步循环，提供了 `AcceptanceGate`（训练集门）+ OOS 合并门（测试集门）双层防护。但 2026-07-26 ~ 27 的三次 HTR 运行与双季度前瞻验证暴露了严重缺陷：

**核心事实**：`ProfitGrowthStrategy` 在 Q1 2026 收益 -5.48%、Q2 2026 收益 +20.09%，窗口极度不一致，但通过了 OOS 合并门（`oos_sharpe=0.51`）。这是过拟合的典型表现——策略拟合于特定市场风格，而非稳健 alpha。

**代码审查发现的五个根本性断点**：

1. **OOS 不读 fold 稳定性**：`_evaluate_oos_and_merge`（[htr_subgraph.py:873-884](../../src/long_earn/strategy_rd/htr_subgraph.py)）只取 `oos_sharpe`（跨折平均），完全忽略 `fold_results`。Walk-Forward 3 折 sharpe `[0.3, -0.5, 1.4]` 与 `[0.4, 0.4, 0.4]` 在合并门眼里完全等价。Q1/Q2 不一致正是此 bug 的直接体现。
2. **合并门无多重检验校正**：阈值 `0.05` 是绝对值，不随 HTR 尝试的策略数 N 调整。N 轮跑下来 family-wise error rate 线性累积——这是 Bailey & López de Prado (2014) 所述"multiple testing 导致回测过拟合"的经典场景。
3. **失败信号不上行**：`_backpropagate_node`（[htr_subgraph.py:797-807](../../src/long_earn/strategy_rd/htr_subgraph.py)）在 rejected=True 时只标 status=FAILED，**不写 backtest_result**；`_collect_tried_directions`（[htr_subgraph.py:352-392](../../src/long_earn/strategy_rd/htr_subgraph.py)）只传 hypothesis 文本，不含失败原因。LLM 反思时看不到"为什么失败"，只能瞎猜。
4. **select 多样性逻辑失效**：[strategy_research_agent.py:1023-1048](../../src/long_earn/strategy_rd/agents/strategy_research_agent.py) 的 `direction not in seen OR len < max_select` 条件——当 `max_select ≥ K`（候选总数）时全选，`seen_directions` 完全不起作用，导致"8 个子节点全部多因子复合+行业中性化"。
5. **frontier 语义错误**：[hypothesis_tree.py:65-67](../../src/long_earn/strategy_rd/hypothesis_tree.py) 的 `is_frontier()` 要求 status ∈ {PENDING, RUNNING}，但 `_executor_node` 跑完默认置 VALIDATED → frontier 永远空，Arbor 的"前沿控制"机制实际从未生效。

**学术调研依据**：
- Bailey & López de Prado (2014) "The Deflated Sharpe Ratio" 提出 DSR 校正 multiple testing 的 selection bias
- Bailey et al. (2017) "The Probability of Backtest Overfitting (PBO)" 用 CSCV 量化过拟合概率
- Arbor 论文 [arXiv:2606.11926] 消融实验证明"insight propagation"是 HTR 的核心 driver，去掉它比去掉整棵树掉得更多
- LATS [arXiv:2310.04406] 与 AFlow 都用 MCTS+UCB，但量化回测 sharpe 噪声大，UCB 收敛性不可靠——本 ADR 选择"统计门 + 离散多样性约束"路径，不走数值搜索算法

**与其他 ADR 的关系**：
- 增强 ADR-010 的合并门（不推翻 HTR 架构）
- 补齐 ADR-005 回测引擎准确性原则在"统计显著性"维度的空白
- 与 ADR-013 回测准确性原则清单互补（ADR-013 是方法论清单，本 ADR 是可执行代码层实现）

## 决策

我们将引入**三道统计过拟合门** + **失败反馈闭环修复** + **探索机制缺陷修复**，分三个阶段落地。核心思想：**统计显著性是过拟合的唯一标准答案**，在补齐统计门之前不改进探索算法——否则只是更高效地找到过拟合策略。

### 阶段 1：Tier S — 三道统计过拟合门（根本性防线）

#### S1. Walk-Forward 稳定性门（`WalkForwardStabilityGate`）

**问题**：`run_oos` 已返回 `fold_results: [{test: {sharpe_ratio}}, ...]`，但 HTR 只读 `oos_sharpe`（平均）。

**设计**：新增 `WalkForwardStabilityGate`，在 `_evaluate_oos_and_merge` 内 OOS 平均 sharpe 过门后追加检查：

```python
@dataclass
class StabilityResult:
    passed: bool
    reason: str
    worst_fold_sharpe: float
    fold_sharpe_std: float
    consistency_ratio: float  # 正 sharpe 折数 / 总折数

class WalkForwardStabilityGate:
    def evaluate(self, fold_results: list[dict]) -> StabilityResult:
        sharpes = [f["test"]["sharpe_ratio"] for f in fold_results if ...]
        # 三道硬性条件（任一不满足即拒绝）：
        # 1. 最差 fold sharpe > -0.1（允许微小负值，容忍噪声）
        # 2. fold 间 sharpe 标准差 < 0.8（防方差过大）
        # 3. 正 sharpe 折数占比 ≥ 2/3（一致性）
```

**接入点**：`_evaluate_oos_and_merge`（[htr_subgraph.py:880-894](../../src/long_earn/strategy_rd/htr_subgraph.py)），OOS 平均 sharpe 通过后追加调用，失败则返回 `"continue"` 并在日志中记录稳定性指标。

**对目标的贡献度**：Q1 -5.48% / Q2 +20.09% 对应到 Walk-Forward 3 折大概率是 sharpe 分布极不稳定（`[正, 负, 正]`）。加稳定性门后会因 worst_fold < 0 或 std 过大被拒，**直接解决报告核心问题**。

#### S2. Deflated Sharpe Ratio 门（`DeflatedSharpeGate`）

**问题**：HTR 跑 N 轮尝试 M 个策略，取最优——这是典型 multiple testing，但当前合并门无 N 校正。

**设计**：实现 Bailey & López de Prado (2014) 的 DSR 简化版（不含 skew/kurt 校正，因 OOS fold_results 不含日收益序列）：

```python
class DeflatedSharpeGate:
    def evaluate(
        self,
        observed_sharpe: float,
        n_trials: int,                # HTR 累积尝试的策略数
        n_observations: int,         # 回测天数
        threshold: float = 1.96,     # 95% 置信
    ) -> tuple[bool, str]:
        # SR 标准误（假设 i.i.d. 正态收益）
        sr_se = 1.0 / sqrt(n_observations)
        # 多重检验校正：N 个独立标准正态噪声的最大期望
        # E[max_N] ≈ sqrt(2 * ln(N))（Bailey 2014）
        expected_max_noise = sqrt(2 * ln(max(n_trials, 1))) if n_trials > 1 else 0
        # Deflated t-statistic
        t_stat = (observed_sharpe - expected_max_noise * sr_se) / sr_se
        return t_stat > threshold, f"DSR t-stat={t_stat:.2f}"
```

**接入点**：`_evaluate_oos_and_merge`，在 S1 稳定性门通过后追加。`n_trials = tree.node_count`（HTR 累积尝试数），`n_observations` 从 `backtest_result.trading_days` 取。

**为什么用简化版**：完整 DSR 需要策略日收益序列计算偏度峰度。OOS `fold_results` 只含每折聚合指标，不含日收益。完整版需要回测引擎改造（每折保留日收益序列），ROI 低。简化版只做 multiple testing 校正（`E[max_N]` 项），已能覆盖 selection bias 的主要风险。

#### S3. PBO 概率门（`BacktestOverfitGate`，完整版）

**问题**：DSR 是单策略检验，无法回答"这 N 个候选策略中是否存在真 alpha，还是全部是噪声"。

**设计**：实现 Bailey et al. (2017) 的 Combinatorial Symmetric Cross-Validation (CSCV)：

```python
class BacktestOverfitGate:
    def evaluate(
        self,
        is_sharpes: list[float],     # N 个策略在训练集的 sharpe
        oos_sharpes: list[float],   # N 个策略在测试集的 sharpe
        n_samples: int = 1000,      # CSCV 组合采样数
    ) -> tuple[bool, str]:
        # 1. 把 IS/OOS sharpe 配对，对称重排 C(2N, N) 个组合（采样 n_samples 个）
        # 2. 对每个组合，计算"IS 最优策略在 OOS 表现劣于中位数"的次数
        # 3. PBO = 次数 / n_samples
        # PBO > 0.5 拒绝（过拟合概率超过 50%）
```

**接入点**：`_evaluate_oos_and_merge`，在 S2 DSR 通过后追加。需要 HTR 维护历史所有候选的 `(dev_sharpe, oos_sharpe)` 配对列表。

**为什么需要 S1+S2+S3 三道门**：
- S1 防单策略窗口不稳定（Q1/Q2 不一致）
- S2 防单策略 sharpe 不显著（multiple testing）
- S3 防多策略集合的 selection bias（"取 N 个中最优"的过拟合）
- 三者互补，缺一不可

### 阶段 2：Tier A — 失败反馈闭环修复

#### A1. 失败信号上行

**问题**：rejected 节点 backtest_result 丢失，`_collect_tried_directions` 不含失败原因。

**设计**：
1. `_backpropagate_node`（[htr_subgraph.py:797-807](../../src/long_earn/strategy_rd/htr_subgraph.py)）：rejected=True 时也写 `backtest_result` + `rejection_reason`（从 executor_results 取）
2. `_collect_tried_directions`：扩展输出格式，包含 `dev_score` / `rejection_reason` / `step_failures`，递归收集 parent 的孙子节点
3. `backpropagate_prompt.md`：显式提取失败原因字段供 LLM 反思

**对目标的贡献度**：间接但关键。当前 LLM 反思是瞎猜（只看到"该方向失败"），修好后能针对"信号过严""lhs 类型错误"具体修正，提升 HTR 收敛效率，间接降低产出过拟合策略的概率。

### 阶段 3：Tier B — 探索机制缺陷修复

#### B1. frontier 语义修复

**问题**：`is_frontier()` 要求 status ∈ {PENDING, RUNNING}，但 executor 跑完置 VALIDATED → frontier 永远空。

**设计**：扩展 `is_frontier()` 包含 VALIDATED 状态（已验证但未被充分展开的叶节点仍可作探索候选）。

#### B2. select 多样性逻辑修复

**问题**：`direction not in seen OR len < max_select` 当 `max_select ≥ K` 时全选。

**设计**：改为 strict set 语义——强制要求每个被选候属不同 direction，候选不足时触发重新生成（或降级为全选并记录警告）。

## 后果

### 正面

1. **直接解决 Q1/Q2 不一致**：S1 稳定性门会拒绝 sharpe 分布不稳定的策略
2. **统计显著性可量化**：S2 DSR 提供 t-statistic，S3 PBO 提供过拟合概率，决策有据可依
3. **LLM 反馈闭环修复**：A1 让 LLM 看到失败原因，HTR 收敛效率提升
4. **探索多样性恢复**：B2 修复后 select 真正起作用，避免同质化假设
5. **架构增量演进**：不重写 HTR，仅增强合并门和反馈链路，风险可控

### 负面

1. **合并门变严，HTR 产出策略变少**：三道统计门叠加后，ProfitGrowthStrategy 这类"看似 OOS 通过实则过拟合"的策略会被拒。这是期望行为，但短期看 HTR"产出率"下降
2. **算力消耗增加**：S3 PBO 的 CSCV 采样 1000 次计算有开销（约 100ms 级，可接受）
3. **DSR 简化版损失精度**：不含 skew/kurt 校正，对非正态收益分布的策略检验力下降。完整版需改造回测引擎保留日收益序列，留待后续 ADR
4. **三道门阈值需调参**：稳定性阈值 0.8、DSR 阈值 1.96、PBO 阈值 0.5 都是经验值，需要实际运行调优
5. **frontier 语义变更影响兼容性**：B1 修改 `is_frontier()` 后，observe 节点展示给 LLM 的 frontier 列表会变化，可能影响 LLM 决策行为

### 中性

1. 本 ADR 不引入 UCB/MCTS 数值搜索算法——调研结论是量化 sharpe 噪声大，UCB 收敛性不可靠。若后续证明有必要，另起 ADR
2. 本 ADR 不实现 Arbor 三动作（expand/prune）——这是中期工作，需要 Coordinator LLM 设计，留待后续 ADR
3. 本 ADR 不引入 QD behavioral_descriptor——离散多样性约束（B2）已能缓解同质化，QD 是更复杂的方案，留待后续

## 参考来源

- [Bailey & López de Prado (2014) "The Deflated Sharpe Ratio"](https://doi.org/10.3905/jpm.2014.40.5.094)
- [Bailey et al. (2017) "The Probability of Backtest Overfitting"](https://doi.org/10.3905/jpm.2017.43.4.041)
- [Arbor: Toward Generalist Autonomous Research via Hypothesis-Tree Refinement](https://arxiv.org/abs/2606.11926)
- [LATS: Language Agent Tree Search](https://arxiv.org/abs/2310.04406)
- [Tree of Thoughts](https://arxiv.org/abs/2305.10601)
- ADR-010 假设树精炼 HTR
- ADR-005 事件驱动回测
- ADR-013 回测准确性原则与陷阱清单

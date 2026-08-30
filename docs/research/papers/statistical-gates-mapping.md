# 统计验证门控：论文机制 → Long Earn 映射

> 依据：Bailey/LdP DSR·PBO·MinTRL；Harvey-Liu 多重检验；AFML purge/embargo  
> 决策：[ADR-022](../../adr/022-statistical-validation-gates-and-evolution-staging.md)  
> 实现：`src/long_earn/strategy_optimization/overfit_gates.py` + `ResearchAgent.run_oos_gates`

## 1. 角色分层（与 factor-qc 对照）

| 检查 | 论文问题 | factor-qc | Long Earn（现行） |
|------|----------|-----------|-------------------|
| Walk-Forward 稳定性 | 跨折是否稳定 | （工程侧 WF） | **硬门** `WalkForwardStabilityGate` |
| held-out vs current best | 测试集是否真提升 | — | **硬门** `evaluate_merge_gate` |
| DSR | 多重检验后夏普是否仍显著 | P0 | **诊断** `DeflatedSharpeGate`（可 `skipped`） |
| PBO / CSCV | 选 IS 最优是否过拟合 | P0（需 trials 矩阵） | **诊断** `evaluate_returns_matrix`；缺列 → pair_legacy / skipped |
| MinTRL | 轨迹是否够长 | P0 | **诊断** `evaluate_mintrl` |
| Haircut Sharpe | 多重检验折减后夏普 | P0 | **诊断** `evaluate_haircut_sharpe` |
| 诚实 n_trials | 试了多少配置 | 必填 | session `_trial_fingerprints` → `N_eff`（非相关校正） |
| 纸面/模拟 | 冻结后对账 | 部署外 | ADR-022 L2（未落地） |

ADR-022 明确：DSR/PBO **在契约齐备前不作硬拒**；缺料必须 `skipped`，不得静默当通过。
与 factor-qc「缺 trials 拒绝评判」同哲学，但晋级硬门目前只绑 WF+合并。

## 2. 算法映射

### 2.1 DSR

- 输入：观测 OOS 平均夏普、`N_eff`、观测数 T；可选 skew / kurtosis（Pearson）
- 实现：`DeflatedSharpeGate`；有矩 → `simplified=False`
- 接入：`ResearchAgent._evaluate_dsr_diagnostic`（`observed_sharpe_source=oos_mean` 优先）

### 2.2 PBO / CSCV

- **标准路径**：T×N 日收益矩阵 → `BacktestOverfitGate.evaluate_returns_matrix`（`method=cscv_matrix`）
- **退化路径**：仅有 (IS,OOS) sharpe 对 → `evaluate`（`method=pair_legacy`）
- 会话内列来源：每次 `run_oos_gates` 从训练集 `daily_returns` 登记一列

### 2.3 MinTRL / Haircut

- 随 DSR 诊断一并产出，写入 `dsr.mintrl` / `dsr.haircut`
- **不**翻转硬门 `passed`

### 2.4 Purge / Embargo

- AFML 完整 purge 依赖标签视界；本仓库以 `TimeSeriesSplit.gap` 做 **embargo 简化**
- `BacktestService.run_oos` 默认 `gap=5`（交易日）

## 3. 写回契约（飞轮）

| outcome | 条件 |
|---------|------|
| `success` | `oos_passed=True`（稳定性 ∧ 合并）+ 指标可信 |
| `candidate` | 仅可靠训练集回测证据 |
| `failure` | 任意（记录失败模式） |

禁止：train-only 标 success 污染经验库。

## 4. 明确不覆盖

统计门 **不能**替代：

- VisibilityGuard / `prove_causality` / T+1 / 涨跌停（正确性）
- 幸存者偏差消除、动态冲击成本
- 纸面交易与实盘 kill switch（L2+）

## 5. 升硬门前置（ADR-022 契约）

DSR/PBO 升为硬门前须：

1. 日收益矩与观测夏普来源一致（OOS）
2. 持久 trial registry + 相关试验 \(N_{\mathrm{eff}}\)
3. 全候选收益矩阵 CSCV 为默认路径（pair_legacy 仅过渡）
4. 单独 ADR 变更阈值与拒侧回归

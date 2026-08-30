---
id: 22
title: 统计验证门控与自我进化分期解锁
status: Accepted
date: 2026-08-30
summary: 规定 Walk-Forward 硬性门控、DSR/PBO 诊断角色与自我进化 L0–L3 分期解锁。
related: ["ADR-015", "ADR-017"]
---


# ADR-022: 统计验证门控（statistical validation gates）用法与自我进化（self-evolution）分期解锁（staged unlock）


## 背景

ADR-015 将 Walk-Forward 稳定性（walk-forward stability，S1）、Deflated Sharpe Ratio（DSR，S2）、Probability of Backtest Overfitting（PBO，S3）写成合并路径上「串行追加、缺一不可」的硬性门控（hard gate），并标 Implemented。对照运行时与业界实践后出现张力：

1. **现行控制面不完整**：ToG `ResearchAgent.run_oos_gates` 仅接 S1 + 简化 DSR；PBO 仍挂在已 Deprecated 的 HTR（Hypothesis Tree Refinement）路径。称「三道硬性门控已落地」与实现不符。
2. **DSR 试验计数不完整**：业界要求日收益序列的偏度/峰度（skew / kurtosis）与有效试验数（effective number of trials，\(N_{\mathrm{eff}}\)；相关网格不得当独立试验）；现行用 raw trial count + 无高阶矩的简化式，易过度惩罚或虚假的置信感。
3. **工具角色被用错**：Bailey & López de Prado 将 DSR 与 PBO 定义为**互补的统计验证诊断（statistical validation diagnostics）**（一个问「冠军夏普在多重检验 / multiple testing 后还算不算数」，一个问「挑样本内最优 / in-sample best 这套选法是否过拟合」），需全候选收益矩阵才能算 PBO；它们**挡不住**前视偏差（look-ahead bias）、成本失真、幸存者偏差（survivorship bias），也**不能替代**纸面交易（paper trading）/ 模拟盘。Quantopian 888 策略实证：回测夏普对样本外（out-of-sample, OOS）几乎无预测力（R² < 0.025），试得越多 IS–OOS 落差越大。
4. **ADR-017 前置过粗**：要求「门会拒 + 至少一个过全部门合并 + 验证集稳健基线」齐备才 Deferred→Proposed，且四项技能一次性全自主实现并启用。这把「统计验证」「测试集合并」「历史验证集」「过程自我改写」耦合为同一启用条件；与三段式硬性约束（验证集研发期禁止碰）冲突，也与业界「先冻结策略 → paper → 再谈改研究过程」的顺序相反。LLM 进化环本身即多重检验（MadEvolve 等明确警告：无严格 OOS fitness 就是多重检验偏误的高效放大器）。

张力：需要统计门防控过拟合（overfitting），但不能用未就绪的诊断误充作完备硬性门控；需要自我进化路线图，但不能在无冻结基线时让系统改 prompt。

## 决策

我们将统计过拟合工具的**用法**与自我进化的**解锁节奏**从 ADR-015 / ADR-017 中拆出，按业界**统计验证（statistical validation）**规范与实盘分期重新规定如下。

### A. 统计验证门控（statistical validation gates）（非「三道全硬」）

| 层级 | 工具 | 用法 |
|------|------|------|
| **硬性门控（hard gate；合并不可跳过）** | Walk-Forward 稳定性（walk-forward stability，原 S1）+ held-out OOS 相对 current best 的合并阈值 | 看**跨折稳定性（cross-fold stability）**，不看最优；任一折级硬条件失败即拒合并 |
| **诊断门控（diagnostic gate；先报告，契约齐备后可升为硬性门控）** | DSR、PBO | 合并决策中**必须产出显式结果**（含 `passed` / `skipped` + 原因）；输入不齐时 **`skipped`，不得静默视为通过** |

**DSR 契约（升为硬性门控前必须满足）**：日收益序列（计算 skew / kurtosis）；完整试验登记（trial registry；网格/会话累计）；相关试验用 \(N_{\mathrm{eff}}\)，禁止把高度相关参数扰动当独立 N。现行简化版仅可作诊断，标注 `simplified`。

**PBO 契约（升为硬性门控前必须满足）**：全体候选的样本内/样本外（in-sample / out-of-sample, IS/OOS）收益或配对矩阵；挂在 ToG `run_oos_gates`（或其后继），**不得**只活在 HTR 遗留线。缺矩阵时 `skipped`。经验阈值：PBO > 0.5 视为选噪强信号；实务上 > 0.2–0.5 已应警惕。

**明确不覆盖**：前视偏差 / 成本 / 幸存者偏差 / 样本外结构性断裂——仍靠引擎硬性约束、PIT（point-in-time）、纸面交易与模拟盘。组合对称交叉验证（combinatorially symmetric cross-validation, CSCV）打乱块序，**补** Walk-Forward，**不替代** Walk-Forward。

HTR 退役时：PBO 实现必须迁入 ResearchAgent 证据路径，或显式宣布降级为可选诊断并改本 ADR；禁止随 HTR 删除后无声消失。

### B. 自我进化（self-evolution）：按危险度分期解锁（staged unlock by risk）（取代 017 全有或全无前置）

ADR-017 四项技能的**实现内容**仍见该 ADR；**何时允许启用**以本表为准（后档包含前档已满足的假设）：

| 档 | 解锁条件 | 允许启用 |
|----|----------|----------|
| **L0** | S1 硬性门控已在生产路径上至少拦截过一次过拟合候选（拒侧有效） | 只读元指标 / 失败模式报告（017 技能 2 的只读面） |
| **L1** | 在现行 ToG 门（至少 S1 + 已启用的诊断）下至少一次 **测试集（test set）** 合并为 current best | 任务经验回写与热启动（experience write-back / hot-start；017 技能 1）；仍禁止改 prompt / 自动调参 |
| **L2** | 冻结该策略配置后，**纸面交易或模拟盘（paper / simulation）**与回测预期可对账（漂移在约定带内） | 失败信号驱动的**参数**调整提案可自动生效（017 技能 3 的调参面）；改 prompt 仍禁止 |
| **L3** | 验证集（validation set）**最后触碰一次**达标，或模拟盘达到书面准入标准 | Prompt 自审计与修订（017 技能 4）+ 技能 3 的改 prompt 面；必须版本追溯与失败率上升自动回滚 |

**禁止**：在连续无 OOS 改善、尚无 L1 冠军时，因 `detect_stagnation` 自动改 prompt 或放宽统计门阈值。

**与三段式关系**：L1 只用测试集合并门；L3 的验证集触碰遵守 AGENTS 硬性约束（研发过程不得反复使用验证集）。「前瞻验证」优先认纸面/模拟（L2），不把历史验证集当作进化启用条件的唯一门槛。

### C. 对旧 ADR 的裁剪

- ADR-015：删除 Tier S 正文；保留 Tier A/B 与「先补统计显著性再扩探索」的背景动机；门用法以本 ADR §A 为准。
- ADR-017：删除「前置条件」整节与全量一次性解锁叙述；技能清单保留为 Deferred 实现规格；解锁节奏以本 ADR §B 为准。

## 后果

- **正面**：门角色与业界一致（Walk-Forward 为硬性门控、DSR/PBO 为可升级诊断）；ToG 路径缺口（PBO、完整 DSR）变为显式契约而非名实不符的 Implemented 标注；进化能力按腐化风险分期，避免无基线改 prompt；与 HTR 清退、纸面交易准入表述对齐。
- **负面**：短期内合并路径可能更常出现 DSR/PBO `skipped`（如实标注）；补齐日收益、试验登记、全候选矩阵有工程量；L2 依赖纸面/模拟基建，未就绪前进化停在 L1。
- **中性**：阈值（S1 折条件、PBO 0.5、DSR 0.95 等）仍为经验值，调参须另开变更并回归，不得由进化环自行放宽；UCB/MCTS / Arbor 三动作 / QD behavioral descriptor 仍为后续可选 ADR（继承 015 调研结论）。

## 参考

- Bailey & López de Prado (2014), *The Deflated Sharpe Ratio* — [JPM](https://doi.org/10.3905/jpm.2014.40.5.094)
- Bailey et al. (2016/2017), *The Probability of Backtest Overfitting* — CSCV / PBO
- Wiecki et al. (Quantopian), *All That Glitters Is Not Gold* — 回测夏普对 OOS 预测力极弱
- 实盘分层：research gates ≠ paper / live ops（冻结配置、对账、kill switch）

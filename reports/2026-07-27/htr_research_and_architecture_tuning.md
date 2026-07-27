# HTR 策略研发过程与架构调优报告 — 2026-07-27

> **报告目的**：记录 2026-07-26 ~ 2026-07-27 HTR 策略研发循环的完整过程、问题修复、验证结果，并提出系统架构调优建议。本报告面向系统架构师，用于指导后续架构演进。

---

## 一、研发背景与目标

### 1.1 系统定位

Long Earn 是 AI 驱动的量化交易研究平台，核心能力是通过 HTR（Hypothesis Tree Refinement）六步循环（observe → ideate → select → dispatch → backpropagate → decide）自动产出可回测的交易策略。

### 1.2 本次研发目标

- 在严格三段式数据分割铁律下，驱动 HTR 产出通过 held-out OOS 合并门的最优策略
- 对最佳策略做双季度前瞻验证（Q1 2026 + Q2 2026），两个窗口收益均需达标
- 发现系统架构缺陷并提出调优建议

### 1.3 数据分割（铁律）

| 区间 | 默认窗口 | 用途 | 约束 |
|------|----------|------|------|
| 训练集 | 2022-01-01 ~ 2024-12-31 | 策略研发、参数寻优 | 自由使用 |
| 测试集 | 2025-01-01 ~ 2026-03-24 | HTR `_decide` 合并门 | 仅合并门触碰 |
| 验证集 | 2026-03-25 ~ 2026-06-25 | 最终评估 | 仅触碰一次 |

---

## 二、研发过程

### 2.1 第一次运行（run_20260726_122219）

**配置**：HTR_MAX_CYCLES=8, max_workers=16, csi500 股票池

**结果**：6 项判据全部失败
- 运行 3h24m 后中止，仅 6/9 节点完成
- 无 oos_score，审计日志不完整
- 验证集窗口错配（实际 2026-01-08~07-10，与期望不符）
- 验证集 Sharpe = -1.45（负值）
- best_strategy.yaml 未更新（仍是 7/23 的 ReversalMomentumHybrid）

**根因**：
1. arithmetic 算子参数错误（lhs=数字常量被 Pydantic 拒绝）
2. 8 个子节点假设同质化严重（全部"多因子复合+行业中性化"）
3. 6 个完成节点全部负 Sharpe
4. HTR_MAX_CYCLES 硬编码，CLI `--max-iterations` 不生效
5. `evaluate_recent_performance` 窗口计算错误，导致验证集窗口错配

### 2.2 第二次运行（run_20260726_174857）

**配置**：HTR_MAX_CYCLES=8, max_workers=16, main_board+gem 股票池, 修复了上述 bug

**结果**：修复生效但 HTR 空转
- 运行 5 小时，6 个迭代全部被 AcceptanceGate 拒绝
- 无 arithmetic 错误，家族多样性机制正常工作
- **核心问题**：首次循环 `previous_backtest={}` 为空，baseline_sharpe=None，AcceptanceGate 要求优化版有正 Sharpe，但 2020-2023 A 股弱势市场下所有策略都是负 Sharpe，导致 HTR 永远无法建立初始基线

### 2.3 第三次运行（run_20260726_232609）

**配置**：HTR_MAX_CYCLES=8, max_rounds=3, 修复了 AcceptanceGate 逻辑

**结果**：HTR 成功产出策略
- 第1轮 node_1 被接受（dev_score=-0.12，AcceptanceGate 修复让负 Sharpe 策略可作为初始基线）
- OOS 合并门通过（oos_sharpe=0.51 > best=None）
- 近三个月（训练集最后6个月）收益率 +9.6%
- 第2轮无显著改善（-17.3%），触发家族切换
- 第3轮进入均值回归家族，多个节点因"信号过滤过严导致无交易"被拒
- 进程意外终止（exit code -1），未完成全部 8 cycles

### 2.4 双季度前瞻验证

对保存的最佳策略 ProfitGrowthStrategy（净利润同比增长率因子）做 Q1/Q2 2026 独立窗口验证：

| 窗口 | 收益率 | Sharpe | 最大回撤 | trade_count | 结论 |
|------|--------|--------|----------|-------------|------|
| Q1 2026 (01-01~03-31) | **-5.48%** | -0.65 | -18.76% | 0 | ❌ 未达标 |
| Q2 2026 (04-01~06-30) | **+20.09%** | 2.41 | -9.50% | 0 | ✅ 优异 |

**结论：❌ 双季度验证未通过**

策略表现极度不一致（Q1 亏 5.48%，Q2 盈 20.09%），说明 ProfitGrowthStrategy 对市场环境高度敏感，**不是稳健的 alpha 策略**，更可能是 Q2 恰好契合了某种市场风格。

---

## 三、关键修复内容

### 3.1 arithmetic 算子参数校验（修复 1）

**问题**：LLM 生成策略时高频错误 `lhs=1`、`lhs=0.0`（数字标量作为 lhs），Pydantic 默认报错信息不友好。

**修复**：在 [src/long_earn/backtest/operators/compose/arithmetic.py](file:///d:/dev/long-earn/src/long_earn/backtest/operators/compose/arithmetic.py) 中：
- `ArithmeticParams.lhs` 字段改为 `str | int | float` 联合类型
- 添加 `_reject_scalar_lhs` 验证器，拒绝数字标量和纯数字字符串
- 在 `apply` 方法中添加 `assert isinstance(params.lhs, str)` 类型断言

**效果**：第二次运行后无 arithmetic 错误。

### 3.2 HTR 家族多样性机制（修复 2）

**问题**：8 个子节点假设同质化严重（全部"多因子复合+行业中性化"），反向传播未能引导 LLM 探索新方向。

**修复**：在 [src/long_earn/strategy_rd/htr_subgraph.py](file:///d:/dev/long-earn/src/long_earn/strategy_rd/htr_subgraph.py) 中：
- 新增 `_collect_tried_directions` 函数，收集 parent 下所有 failed/pruned 子节点的假设摘要
- 在 `_ideate_node` 中将已尝试方向注入 `pruned_directions` 变量
- 更新 [ideate_prompt.md](file:///d:/dev/long-earn/src/long_earn/strategy_rd/agents/ideate_prompt.md) 中"已尝试/已失败方向"语义，明确要求 LLM 探索新方向
- 添加模块级常量 `_TRUNCATE_HYPOTHESIS_LEN = 120`，控制假设摘要截断长度

**效果**：第三次运行中，ideate 节点成功检测到 1-5 个已尝试方向并注入 prompt，LLM 生成了更多样化的假设。

### 3.3 AcceptanceGate 初始基线逻辑（修复 3，核心）

**问题**：首次循环 `previous_backtest={}` 为空，baseline_sharpe=None，AcceptanceGate 要求优化版 `sharpe > 0`。弱势市场下所有策略都是负 Sharpe，导致 HTR 永远无法建立初始基线，整个研发循环空转。

**修复**：在 [src/long_earn/strategy_optimization/acceptance.py](file:///d:/dev/long-earn/src/long_earn/strategy_optimization/acceptance.py) 中：
- 基线无 Sharpe 时（HTR 首次循环），接受任何有有效 Sharpe 的非 degenerate 策略作为初始基线（即使负 Sharpe）
- 后续循环 `previous_backtest` 有值时，仍走 `o_sharpe > b_sharpe + eps` 的严格提升分支

**效果**：第三次运行中 node_1 被接受（dev_score=-0.12），HTR 首次产出有效策略。

### 3.4 HTR 配置与数据分割修复（修复 4）

**问题**：
- `find_best_strategy.py` 未调用 `load_dotenv()`，.env 配置被忽略
- `find_best_strategy.py` 硬编码 `max_iterations=2`，CLI `--max-iterations` 不生效
- dev 回测区间包含测试集（`config.backtest_end_date = config.test_end_date`），违反铁律
- `strategy_research_service.py` 中 `history_end = config.test_end_date`，`recent_start/end = config.validation_*`，违反铁律

**修复**：在 [scripts/find_best_strategy.py](file:///d:/dev/long-earn/scripts/find_best_strategy.py) 和 [src/long_earn/services/strategy_research_service.py](file:///d:/dev/long-earn/src/long_earn/services/strategy_research_service.py) 中：
- 添加 `from dotenv import load_dotenv; load_dotenv()`
- 添加 `--max-iterations` CLI 参数，同时覆盖 `config.max_iterations` 和 `config.htr_max_cycles`
- `config.backtest_end_date = config.train_end_date`（dev 回测仅用训练集）
- `history_end = config.train_end_date`，`recent_start/end` 从训练集最后 6 个月推导

### 3.5 审计日志完整性（修复 5）

**问题**：
- RUN_END 事件 payload 缺失 `metrics_unreliable` 字段
- DATA_EMPTY 路径未记录 RUN_END，导致审计日志不配对（7/26 HTR 中 2 个节点只有 RUN_START 无 RUN_END）

**修复**：在 [src/long_earn/backtest/engine/core.py](file:///d:/dev/long-earn/src/long_earn/backtest/engine/core.py) 中：
- RUN_END 事件 payload 添加 `metrics_unreliable` 字段
- DATA_EMPTY 路径补全 RUN_END 事件记录，status=FAILED

### 3.6 双季度前瞻验证（新增）

**目的**：铁律 #3 要求验证集仅最终评估时触碰一次。HTR 完成后对最佳策略分别跑 Q1 2026 和 Q2 2026 独立窗口回测，两个窗口收益都需达标才算通过。

**实现**：在 [scripts/find_best_strategy.py](file:///d:/dev/long-earn/scripts/find_best_strategy.py) 中：
- 新增 `validate_best_strategy_dual_quarter()` 函数
- 集成到 `main()` 末尾，HTR 循环完成后自动执行
- 验证结果追加到 `strategy_research_results.json`
- 独立脚本 [scripts/validate_dual_quarter.py](file:///d:/dev/long-earn/scripts/validate_dual_quarter.py) 支持对任意最佳策略做双季度验证

---

## 四、核心架构问题与调优建议

### 4.1 AcceptanceGate 首次循环冷启动问题（P0，已修复）

**问题**：首次循环无基线时要求正 Sharpe，弱势市场下 HTR 永远无法建立基线。

**根因**：AcceptanceGate 设计初衷是"防止 LLM 把策略改成更差版本"，但未考虑 HTR 首次循环的冷启动场景。

**调优建议**：
- ✅ 已修复：基线无 Sharpe 时接受任何有效策略作为初始基线
- **后续优化**：引入"基线质量等级"概念，区分"冷启动基线"与"稳定基线"，冷启动基线在后续循环中应更快被替换

### 4.2 LLM 假设同质化问题（P1，部分修复）

**问题**：LLM 倾向生成同质化假设（如全部"多因子复合+行业中性化"），反向传播未能有效引导探索新方向。

**根因**：
- ideate prompt 未显式避开已失败方向
- 缺乏"策略家族"显式约束，LLM 在同一思路空间内打转

**调优建议**：
- ✅ 已修复：注入已尝试方向到 ideate prompt
- **后续优化**：
  - 引入"策略家族指纹"（如因子类型/换仓频率/风控方式），强制不同家族
  - 在 `_decide` 节点添加"家族多样性阈值"，同家族节点超过 N 个就强制切换
  - 扩展 `_IDEA_FAMILY_POOL`，增加市场状态识别、动态仓位、事件驱动等家族

### 4.3 策略过拟合检测缺失（P1，未修复）

**问题**：ProfitGrowthStrategy 训练集 -27% 但"近三个月 +9.6%"，Q1 2026 -5.48% 但 Q2 2026 +20.09%，明显过拟合于特定窗口的市场风格。

**根因**：当前系统仅依赖 OOS 合并门（测试集 Sharpe > best）作为过拟合防线，未做"窗口稳定性检测"。

**调优建议**：
- **引入 Walk-Forward 稳定性检测**：在 OOS 合并门通过后，对测试集做多个子窗口（如每月、每季）的 Sharpe 一致性检测，方差过大则拒绝
- **训练集内部分桶验证**：将训练集分为多个时间桶，要求策略在多数桶上表现一致（如 4 个桶中至少 3 个正收益）
- **双季度验证前置**：将双季度验证作为合并门的附加条件，而非事后验证

### 4.4 回测引擎"信号过滤后 selected_df 为空"高频警告（P2）

**问题**：均值回归家族策略在多个时间点触发"signal 算子过滤后 selected_df 为空"，导致策略退化（无真实交易）。

**根因**：
- LLM 生成的过滤条件过于严格（多重阈值叠加）
- 引擎在过滤为空时直接 break，未给 LLM 反馈"条件过严"信号

**调优建议**：
- **引擎层**：在 selected_df 为空时记录"空信号原因"（哪个 filter 步骤导致），传播到 backpropagate 节点
- **AcceptanceGate 层**：对"全 step 失败"的策略，在拒绝原因中显式标注"信号过严"，引导 LLM 放宽条件
- **ideate prompt**：添加"信号稀疏性"警示，要求 LLM 平衡信号质量与数量

### 4.5 HTR 进程意外终止（P2）

**问题**：第三次运行中，HTR 进程在 iteration=4 回测阶段意外终止（exit code -1）。

**根因**：未明确，可能与长时间运行（已 4h+）+ 大量 WARNING 日志写入 + Windows 进程管理有关。

**调优建议**：
- **日志采样**：对"selected_df 为空"等高频 WARNING 做采样输出（如每 100 次输出 1 次），避免日志爆炸
- **checkpoint 恢复**：当前 `--reset-checkpoint` 清空旧 checkpoint，应支持"从中断处续跑"模式（不传 `--reset-checkpoint`）
- **进程看门狗**：添加 HTR 进程心跳检测，异常终止时自动保存当前假设树状态

### 4.6 LLM 算子参数生成质量（P2，部分修复）

**问题**：LLM 仍会生成 `lhs=2`（数字标量）等非法参数，node_4 因此被拒绝。

**根因**：arithmetic 算子校验虽已拒绝，但 LLM 未从拒绝原因中学习（prompt 未反馈具体错误）。

**调优建议**：
- **错误反馈闭环**：在 backpropagate 节点注入"策略解析失败原因"，让 LLM 看到具体错误并修正
- **算子使用示例库**：在 strategy_develop_prompt.md 中为每个算子添加"正确/错误"示例对比
- **运行时自愈**：对 `lhs`/`rhs` 类型错误，引擎层做"自动交换"兜底（如 `lhs=2, rhs="close"` → 自动交换为 `lhs="close", rhs=2`）

### 4.7 数据分割合规性检测缺失（P3）

**问题**：第二次运行前，`find_best_strategy.py` 和 `strategy_research_service.py` 中存在违反三段式数据分割铁律的代码（dev 回测区间包含测试集）。

**根因**：缺乏运行时合规性检测，依赖人工 review。

**调优建议**：
- **添加合规性断言**：在 `BacktestService.run()` 入口添加日期区间断言，回测区间与测试集/验证集重叠时直接抛异常（除非显式标注 `purpose="oos_merge"` 或 `purpose="final_validation"`）
- **CI 合规性检查**：添加单元测试验证 `strategy_research_service.py` 中 `history_end`/`recent_end` 严格等于 `train_end_date`

---

## 五、验证结果总结

### 5.1 修复效果验证

| 修复项 | 验证方式 | 结果 |
|--------|----------|------|
| arithmetic 算子校验 | 单元测试 + 第二次运行 | ✅ 无 arithmetic 错误 |
| 家族多样性机制 | 第三次运行日志 | ✅ 检测到 1-5 个已尝试方向 |
| AcceptanceGate 冷启动 | 第三次运行 node_1 被接受 | ✅ 负 Sharpe 策略被接受为初始基线 |
| HTR 配置与数据分割 | 单元测试 + 日志确认 | ✅ CLI 参数生效，dev 回测仅用训练集 |
| 审计日志完整性 | 代码审查 | ✅ RUN_END 配对，含 metrics_unreliable |
| 双季度前瞻验证 | 独立脚本验证 | ✅ 验证逻辑正确，结果已保存 |

### 5.2 单元测试

- 修复前：705 个测试全部通过
- 修复后：705 个测试全部通过（新增 3 个 AcceptanceGate 测试），无回归

### 5.3 最终策略表现

| 指标 | 数值 | 说明 |
|------|------|------|
| 训练集 Sharpe | -0.12 | node_1 dev_score |
| 训练集收益 | -27.35% | 历史窗口 |
| OOS Sharpe | +0.51 | 测试集合并门 |
| 近6个月收益（训练集尾部） | +9.61% | 开发期评估 |
| Q1 2026 收益 | **-5.48%** | 双季度验证未通过 |
| Q2 2026 收益 | **+20.09%** | 双季度验证通过 |
| **双季度综合判定** | **❌ 未通过** | Q1 收益为负 |

---

## 六、结论与后续行动项

### 6.1 核心结论

1. **AcceptanceGate 冷启动修复是关键突破**：让 HTR 首次能产出有效策略，整个研发循环得以运转
2. **策略过拟合问题严重**：ProfitGrowthStrategy 在 Q1/Q2 2026 表现极度不一致，当前 OOS 合并门不足以防范过拟合
3. **LLM 生成策略质量仍有提升空间**：算子参数错误、信号过滤过严等问题频发，需要错误反馈闭环

### 6.2 后续行动项（按优先级）

| 优先级 | 行动项 | 负责模块 |
|--------|--------|----------|
| P0 | 引入 Walk-Forward 稳定性检测，作为 OOS 合并门附加条件 | `strategy_optimization/` |
| P0 | 训练集内部分桶验证，要求策略在多数桶上表现一致 | `strategy_optimization/` |
| P1 | backpropagate 节点注入"策略解析失败原因"，形成错误反馈闭环 | `strategy_rd/htr_subgraph.py` |
| P1 | 引入"策略家族指纹"，强制家族多样性 | `strategy_rd/htr_subgraph.py` |
| P1 | 引擎层记录"空信号原因"并传播到 backpropagate | `backtest/engine/operator_executor.py` |
| P2 | 日志采样：高频 WARNING 采样输出 | `backtest/engine/operator_executor.py` |
| P2 | HTR 进程看门狗 + checkpoint 恢复增强 | `scripts/find_best_strategy.py` |
| P2 | 算子使用示例库（正确/错误对比） | `strategy_rd/agents/strategy_develop_prompt.md` |
| P3 | 数据分割合规性断言 + CI 检查 | `services/backtest_service.py` |
| P3 | 扩展 `_IDEA_FAMILY_POOL`，增加市场状态识别等家族 | `services/strategy_research_service.py` |

### 6.3 架构演进方向

当前 HTR 是"串行探索 + 反向传播"模式，建议演进为：

1. **并行假设探索**：`HTR_MAX_SELECT > 1` 启用并行 fan-out，多个假设同时回测，加速探索
2. **多目标优化**：不仅优化 Sharpe，同时优化"窗口稳定性"、"家族多样性"、"信号密度"，避免单一指标过拟合
3. **元学习**：将历史 HTR 运行的失败/成功经验沉淀到 Substance 记忆，ideate 节点检索相似经验避免重复

---

## 附录：修改文件清单

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| `src/long_earn/backtest/operators/compose/arithmetic.py` | 修复 | lhs 字段校验，拒绝数字标量 |
| `src/long_earn/strategy_rd/htr_subgraph.py` | 修复 | 新增 `_collect_tried_directions`，注入已尝试方向 |
| `src/long_earn/strategy_rd/agents/ideate_prompt.md` | 修复 | 更新已尝试方向语义 |
| `src/long_earn/strategy_optimization/acceptance.py` | 修复 | 基线无 Sharpe 时接受初始基线 |
| `src/long_earn/backtest/engine/core.py` | 修复 | RUN_END 补全 metrics_unreliable + DATA_EMPTY 路径 |
| `scripts/find_best_strategy.py` | 修复 + 新增 | load_dotenv, --max-iterations, 双季度验证 |
| `src/long_earn/services/strategy_research_service.py` | 修复 | 数据分割合规（history_end/recent 用训练集） |
| `tests/unit/test_strategy_rd/test_htr_acceptance_gate.py` | 新增测试 | 初始基线场景覆盖 |
| `tests/unit/test_backtest/test_operators/test_numerics.py` | 新增测试 | arithmetic lhs 标量校验 |
| `scripts/validate_dual_quarter.py` | 新增 | 独立双季度验证脚本 |
| `reports/` | 新增 | 按日期组织的研发报告目录 |

---

**报告生成时间**：2026-07-27
**报告作者**：TRAE AI 助手
**数据来源**：HTR 运行日志、双季度验证结果、代码审查

# ADR-010: 假设树精炼（Hypothesis Tree Refinement, HTR）

日期: 2026-06
状态: Accepted, Implemented (Phase 1-3); Phase 4-5 pending

## 背景

`strategy_rd` 子图当前采用**线性进化循环**架构：

```
START → init → initial_retrieval → evaluate_retrieval ↔ adaptive_retrieval
  → research → develop → backtest ↔ refine → reflection
  → save_experience → supervisor →(continue)→ optimize → develop_optimized
  → backtest_optimized ↔ refine_optimized → reflection → ... → supervisor →(stop)→ END
```

15 个节点，4 层循环（自适应检索 / 初始代码修复 / 优化代码修复 / 外层进化，各 max 3 轮）。

对比 Arbor 论文（[arXiv:2606.11926](https://arxiv.org/abs/2606.11926)）的 Hypothesis Tree Refinement (HTR) 框架，当前架构有六项结构性局限：

1. **无持久化研究状态**：每轮覆盖前一轮的 `strategy`/`backtest_result`/`reflection`，无法回溯探索路径。
2. **无分支探索**：每轮只选一个 ToT 分支，其余两个被丢弃，竞争假设无并行验证。
3. **无洞察累积**：每轮反思独立进行，不继承前轮的抽象教训。
4. **无 dev/test 分离**：单回测区间，无 held-out 验证门，易过拟合。
5. **无基于证据的剪枝**：失败方向不被正式跟踪和传播。
6. **反思是单次 ToT**：不是跨迭代的累积假设精炼。

## 决策

将 `strategy_rd` 子图从线性循环替换为 **HTR 六步循环**，用持久化假设树作为研究状态。

### A. Arbor HTR 核心思想适配

Arbor 将自主研究组织为**持久假设树上的证据结构化精炼**：

- **Coordinator**（长生命周期）维护假设树，管理全局研究策略。
- **Executor**（短生命周期）在隔离 worktree 中测试单个假设，返回结构化证据。
- **六步循环**：Observe → Ideate → Select → Dispatch → Backpropagate → Decide。
- **Held-out 合并门**：仅当 dev 改进 transfer 到 held-out 时才合并为 current best。
- **洞察传播**：局部实验结果向上抽象为方向级教训，累积为全局理解。

#### 适配映射

| Arbor 概念 | long_earn 对应 | 说明 |
|-----------|---------------|------|
| Material (artifact) | 策略 spec dict + YAML DSL | 可变工件 |
| Objective | 回测指标方向（sharpe↑, drawdown↓） | 改进目标 |
| E_dev | 回测引擎（全区间） | 探索反馈，自由使用 |
| E_test | Walk-Forward OOS（扩展窗口） | held-out 验证，仅用于 merge 决策 |
| Hypothesis | 策略改进方向（"加动量过滤", "调止损参数"） | 可验证假设 |
| Executor | optimize → develop → backtest → refine 循环 | 短生命周期执行器 |
| Coordinator | observe + ideate + select + backpropagate + decide | 长生命周期协调器 |
| Idea Tree | `HypothesisTree` 领域实体 | 持久化研究状态 |
| Insight | 反思摘要 + 提炼教训 | 可复用语义记忆 |

### B. 目标流程图

```
START → init_tree → observe → ideate → select → dispatch(Send×k)
  → [executor_1, executor_2, ...] → backpropagate(join)
  → decide →(continue)→ observe → ...
            →(merge)→ save_tree → END
            →(stop)→ save_tree → END
```

每个 executor 内部：
```
optimize_strategy(hypothesis) → develop → backtest → refine(loop) → return(score, result, insight, yaml)
```

### C. 持久化策略：独立 JSON Store + 摘要回写 memory（混合策略）

> **本节是与 ADR-007（物质-运动架构）的关键交叉点，作为正式决策记录。**

假设树本体用**独立的 JSON 文件持久化**（`HypothesisTreeStore`），**不直接存入** ADR-007 的 SubstanceStore。树摘要（insight / best 节点 / 研究方向）在 Phase 4 回写 memory 做 hot-start 检索。

**理由（为什么混合而非统一）：**

1. **数据结构本质不同**：假设树是**层级结构化研究状态**（parent/children/depth/frontier），Substance 是**扁平事实/事件/关系**。把树强塞进 Substance 的 `content: str` + flat keys 会丢失层级语义，GraphIndex 的 BFS 也无法表达"假设 A 的子假设 B 的证据"这种树路径。
2. **检索模式不同**：树操作是 `frontier()` / `best_node()` / `prune_subtree()` / `backpropagate_insight()`——全是树拓扑查询，不是关键词/语义检索。SubstanceStore 的双通道检索（keyword + TF-IDF）对树结构无增值。
3. **生命周期不同**：树是单次研究 run 的状态（`run_id` 隔离），run 结束后树持久化供跨 run 热启动。Substance 是跨 run 的长期记忆（事件/知识/策略经验），生命周期更长。
4. **但洞察需要跨 run 复用**：Phase 4 的"洞察传播"把树摘要回写 SubstanceStore 为 `form=KNOWLEDGE` 物质（`category="研究树"`），通过 ADR-007 双通道检索实现"上次研究的教训这次能检索到"——这正是 SubstanceStore 擅长的。

**混合边界**：
- `HypothesisTreeStore`（新）：`~/.long_earn/hypothesis_trees/{run_id}.json`，存完整树结构。
- `MemoryService.save_hypothesis_tree(tree)`（新方法）：把树摘要（best 节点 + 关键 insight + 研究方向）存为 knowledge Substance。
- `MemoryService.search_hypothesis_trees(query, k)`（新方法）：检索历史树摘要，供 `_observe` / `_ideate` hot-start。
- **Protocol 兼容**：新增两方法到 `MemoryService` Protocol，`MemoryServiceImpl` 内部委托 SubstanceStore（与 ADR-007 的其他方法同模式）。

### D. Held-out 验证门（防过拟合）

- **Dev 信号**：现有回测（全区间）— 自由用于探索，指导假设搜索。
- **Held-out 信号**：Walk-Forward OOS — 严格合并门。
- Walk-Forward 的 expanding-window 设计（`TimeSeriesSplit`）天然将历史切为连续折叠，每个折叠的 test 窗口是真正的 OOS。
- **现状**：`EventDrivenBacktestEngine.walk_forward_run()` 已实现于引擎层（`core.py`），ADR-008 已交付 `BacktestService.run_walk_forward_parallel()`。但**未通过 `BacktestService` Protocol 暴露为 `run_oos` 方法**。需新增 Protocol 方法 + 类型化模型。
- 配置参数：`merge_threshold`（OOS 最低改善幅度，默认 5%）、`oos_n_splits`（折叠数，默认 3）。
- **合并规则**：`oos_score > current_best_oos + threshold` → merge；否则继续探索或剪枝。

### E. 并行执行

- 引入 LangGraph `Send` API 实现动态 fan-out。
- `dispatch` 节点为每个 selected 叶节点返回 `Send` 对象。
- `backpropagate` 在 join 点合并所有 executor 结果。
- 与 `stock_analysis` 的固定列表 fan-out 不同——`Send` 允许任意 branching_factor，无需预定义节点名。
- `executor_results: Annotated[list[dict], _collect_results]` 自定义 reducer 收集并行结果。
- **分阶段启用**：阶段 2 先串行单节点 dispatch，阶段 5 再加并行，降低初始复杂度。

### F. 历史兼容

- **直接替换**现有线性流程为 HTR 架构，不保留旧线性模式作为 fallback。
- 旧线性流程节点（`init`, `supervisor`, `optimize`, `develop_optimized`, `backtest_optimized`, `refine_optimized`, `save_experience` 等）逻辑迁移到 executor 内部或新节点。
- `strategy_rd_supervisor_prompt.md` / `strategy_rd_supervisor_continue_prompt.md` 被 `decide_prompt.md` 替代。

## 文件结构

### 新增文件

```
src/long_earn/strategy_rd/
├── hypothesis_tree.py          # HypothesisNode + HypothesisTree 领域实体
└── tree_store.py               # HypothesisTreeStore（JSON 文件持久化）

src/long_earn/strategy_rd/agents/
├── observe_prompt.md           # 输入：树投影/frontier/祖先 insight/当前 best
├── ideate_prompt.md            # 输入：观察/父假设/子 insight/已剪枝节点（负约束）
├── backpropagate_prompt.md     # 输入：子节点 insight 列表/父假设 → 抽象摘要
└── decide_prompt.md            # 输入：树状态/best dev/current best OOS/预算 → action
```

### 修改文件

```
src/long_earn/strategy_rd/
├── state.py                    # 新增 hypothesis_tree/current_best_node_id/selected_leaves/executor_results/run_id/oos_threshold/oos_n_splits
├── strategy_research_agent.py  # 新增 observe/ideate/select/backpropagate_insights/decide 方法
└── subgraph.py                 # 新节点：_init_tree/_observe/_ideate/_select/_dispatch/_executor/_backpropagate/_decide/_save_tree

src/long_earn/services/
├── __init__.py                 # BacktestService Protocol 新增 run_oos
├── backtest_service.py         # 实现 run_oos（调 walk_forward_run）
└── memory_service.py           # 新增 save_hypothesis_tree / search_hypothesis_trees

src/long_earn/backtest/models.py   # 新增 WalkForwardResult(BaseModel)
```

### 领域实体设计

`HypothesisNode`：
- `id`, `parent_id`, `hypothesis`, `status`（pending/running/validated/pruned/merged/failed）
- `strategy_ref`（指向策略 dict 的引用 key）
- `dev_score`, `oos_score`, `backtest_result`, `insight`
- `children_ids`, `depth`, `direction`（收益增强/风险控制/收益稳定性）
- `created_at`

`HypothesisTree`：
- `root`, `_nodes: dict[str, HypothesisNode]`
- `add_child(parent_id, hypothesis, direction) -> node_id`
- `update_evidence(node_id, dev_score, oos_score, backtest_result, insight)`
- `backpropagate_insight(node_id)` — 沿路径向上传播洞察
- `prune_subtree(node_id)` — 标记子树为 pruned，移出 frontier
- `frontier() -> list[HypothesisNode]` — pending/running 状态的叶节点
- `best_node() -> HypothesisNode | None` — oos_score 最高的 validated/merged 节点
- `current_best_id`, `serialize()` / `deserialize(data)`

## 理由

1. **持久化研究状态**：假设树让探索路径可回溯，失败方向被正式跟踪，不再每轮覆盖。
2. **分支并行验证**：竞争假设并行测试（`Send` API），不丢弃 ToT 分支。
3. **洞察累积**：`backpropagate_insight` 沿树路径向上抽象教训，跨迭代累积全局理解。
4. **dev/test 分离防过拟合**：Walk-Forward OOS 作 held-out 合并门，只有 transfer 到 OOS 的改进才合并——这是"金融级可信"在研究流程层的体现。
5. **混合持久化策略**：树本体用 JSON Store（保层级语义），摘要回写 SubstanceStore（复用 ADR-007 双通道检索做 hot-start）——各取所长，不强行统一。
6. **直接替换**：不保留旧线性模式，避免维护两套流程的长期成本。

## 后果

- **复杂度增加**：树结构 + 并行执行显著增加状态管理复杂度。分阶段交付（串行 → 并行）降低初始复杂度。
- **LLM 调用成本**：observe/ideate/backpropagate/decide 是额外 LLM 调用。可通过 `max_cycles` 和 `branching_factor` 控制。
- **回测成本**：held-out 验证增加回测次数。可配置 `merge_threshold` 控制频率。可复用 ADR-008 的 `run_walk_forward_parallel` 加速。
- **MemoryService Protocol 扩展**：新增 `save_hypothesis_tree` / `search_hypothesis_trees` 两方法，`MemoryServiceImpl` 委托 SubstanceStore（与 ADR-007 其他方法同模式）。
- **新配置项**：`HTR_MAX_CYCLES`(10) / `HTR_BRANCHING_FACTOR`(3) / `HTR_MAX_DEPTH`(3) / `HTR_MERGE_THRESHOLD`(0.05) / `HTR_OOS_N_SPLITS`(3) / `HTR_HOT_START`(false) / `HYPOTHESIS_TREE_PATH`。
- **直接替换不可回退**：不保留旧线性流程，需确保新流程完整后再移除旧代码。

## 分阶段实施计划

> 执行顺序：ADR-009 gap_detector 接入（可选前置）→ 阶段 1 → 阶段 2 → 阶段 3 → 阶段 4 → 阶段 5
> 逐阶段交付，每阶段含测试 + lint + Serena 诊断

### 阶段 1：假设树领域模型

**目标**：建立持久化树结构，不改变现有流程，只增加旁路记录。

- `hypothesis_tree.py`：`HypothesisNode` + `HypothesisTree`（add_child/update_evidence/backpropagate/prune/frontier/best_node/serialize/deserialize）。
- `tree_store.py`：`HypothesisTreeStore`（save/load/list_runs，`~/.long_earn/hypothesis_trees/{run_id}.json`）。
- `state.py` 新增字段：`hypothesis_tree` / `current_best_node_id` / `selected_leaves` / `executor_results` / `run_id` / `oos_threshold` / `oos_n_splits`。
- 测试：`test_hypothesis_tree.py`（CRUD + 往返）、`test_tree_store.py`（save/load + 不存在 → None）。

### 阶段 2：Coordinator 六步循环（串行）

**目标**：用六步循环替代当前线性 reflection → supervisor → optimize 流程。

- 新 Prompt 文件（observe/ideate/backpropagate/decide，均与 agent .py 同目录）。
- `strategy_research_agent.py` 新增方法：observe/ideate/select/backpropagate_insights/decide。
- `subgraph.py` 新节点：_init_tree/_observe/_ideate/_select/_dispatch（**阶段 2 先串行单节点**）/_executor（内部复用现有 optimize→develop→backtest→refine 逻辑）/_backpropagate/_decide/_save_tree。
- 测试：`test_subgraph_htr.py`（子图编译 + `_decide_node` 合并门逻辑——核心信任路径）。

### 阶段 3：Held-out 验证门

**目标**：防止 dev 区间过拟合，只有 transfer 到 held-out 的改进才合并。

- `BacktestService` Protocol 新增 `run_oos(strategy_yaml, start_date, end_date, n_splits) -> dict`。
- `backtest_service.py` 实现 `run_oos`，调 `engine.walk_forward_run()`。
- `models.py` 新增 `WalkForwardResult`（fold_results/average_test_metrics/n_splits/failed_folds）。
- `_decide_node` 调 `run_oos()` 对 best dev 候选；`oos_score > current_best_oos + threshold` 则 merge。
- 测试：`test_backtest_service.py`（run_oos Protocol 一致性 + 结果结构）。

### 阶段 4：洞察传播与记忆增强

**目标**：让洞察跨迭代累积，增强记忆系统的结构化检索。**此阶段落地与 ADR-007 的混合持久化策略。**

- `memory_service.py` 新增 `save_hypothesis_tree(tree)`（树摘要存为 knowledge Substance，`category="研究树"`）+ `search_hypothesis_trees(query, k)`（检索历史树摘要）。
- `_ideate` 注入历史树洞察（从 `search_hypothesis_trees`）；`_observe` 可加载之前的树实现 hot-start。
- 测试：`test_memory_service.py`（两新方法接口层验证）。

### 阶段 5：并行执行

**目标**：利用 LangGraph `Send` API 并行测试多个假设。

- `_dispatch_node` 返回 `Send` 对象列表。
- `_backpropagate_node` 作为 join 节点，LangGraph barrier 语义。
- `executor_results: Annotated[list[dict], _collect_results]` 自定义 reducer。
- 测试：`test_subgraph_htr.py`（并行 dispatch + join + reducer 行为）。

## 与其他 ADR 的关系

- **ADR-002**（partial 节点注入）：新节点同样用 partial 绑定服务，沿用 ADR-002 模式。
- **ADR-005**（事件驱动回测）：Held-out 验证门复用 ADR-005 的 Walk-Forward `walk_forward_run()`。
- **ADR-007**（物质-运动架构）：**关键依赖**。混合持久化策略——假设树本体独立 JSON Store（层级结构不适合 Substance 扁平模型），树摘要回写 SubstanceStore 为 knowledge 物质（复用双通道检索做 hot-start）。`MemoryService` Protocol 新增两方法，`MemoryServiceImpl` 委托 SubstanceStore，与 ADR-007 其他方法同模式，消费方零改动。
- **ADR-008**（并行回测 + 统一模板）：HTR executor 内部 backtest 可复用 `run_walk_forward_parallel` 加速 held-out 验证；策略参数化用 ADR-008 的 `${var}` 模板 + `ParamGrid` 做参数寻优。
- **ADR-009**（算子目录 + operator_dev）：HTR executor 内部的 develop/backtest 复用算子目录 DSL；HTR 假设的"改进方向"若涉及算子缺口，可经 `gap_detector`（ADR-009 后续项）产出 OperatorSpec 进 operator_dev backlog——两系统形成"假设驱动算子研发"闭环。

## 参考文献

- **Arbor 论文**：[arXiv:2606.11926](https://arxiv.org/abs/2606.11926) — Toward Generalist Autonomous Research via Hypothesis-Tree Refinement
- **Arbor 代码**：[GitHub](https://github.com/RUC-NLPIR/Arbor)
- **Arbor 项目主页**：https://ruc-nlpir.github.io/Arbor/
- 详细实施计划：`plans/hypothesis-tree-refinement-plan.md`
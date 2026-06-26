# 策略反思架构调整计划：假设树精炼（Hypothesis Tree Refinement）

> 基于 Arbor 论文（[arXiv:2606.11926](https://arxiv.org/abs/2606.11926)）的 Hypothesis Tree Refinement (HTR) 框架，将 long_earn 的 `strategy_rd` 子图从**线性进化循环**升级为**假设树精炼**架构。

## 一、背景与动机

### 当前架构：线性进化循环

```
START → init → initial_retrieval → evaluate_retrieval ↔ adaptive_retrieval
  → research → develop → backtest ↔ refine → reflection
  → save_experience → supervisor →(continue)→ optimize → develop_optimized
  → backtest_optimized ↔ refine_optimized → reflection → ... → supervisor →(stop)→ END
```

**15 个节点，4 层循环：**

1. 自适应检索循环（`evaluate_retrieval ↔ adaptive_retrieval`，max 3 轮）
2. 初始代码修复循环（`backtest ↔ refine`，max 3 次）
3. 优化代码修复循环（`backtest_optimized ↔ refine_optimized`，max 3 次）
4. 外层进化循环（`reflection → save_experience → supervisor → optimize → ...`，max 3 迭代）

### 当前架构的关键设计特征

| 组件 | 位置 | 特征 |
|------|------|------|
| 状态结构 | `strategy_rd/state.py` | 扁平 TypedDict，单策略、单回测结果、单反思。`code_valid` 使用 `_last_wins` reducer |
| 反思节点 | `subgraph.py:280-307` | ToT 三分支并行（收益增强/风险控制/收益稳定性），按规则评分选最优分支。Prompt 内联在 `strategy_research_agent.py` |
| 监督器 | `subgraph.py:462-500` | LLM JSON 决策 + 硬上限（`max_iterations`）+ 性能信号回退（sharpe ≥ 1.5 → stop）。解析失败默认 continue |
| 优化节点 | `subgraph.py:310-350` | 第 N 轮用第 N-1 轮的 `optimized_strategy` 作基底，增量进化。维护 `evolution_lineage` 审计链 |
| 记忆集成 | `memory_service.py` | `save_experience` 持久化到 Core 层（TF-IDF）；`optimize` 检索历史经验 |
| 设计模式 | 全局 | `functools.partial` 节点注入；`KnowledgeContextMixin` 共享检索；显式错误历史重置 |

### 当前架构的局限性（对比 Arbor HTR）

1. **无持久化研究状态**：每轮覆盖前一轮的 `strategy`/`backtest_result`/`reflection`，无法回溯探索路径
2. **无分支探索**：每轮只选一个 ToT 分支，其余两个被丢弃，竞争假设无并行验证
3. **无洞察累积**：每轮反思独立进行，不继承前轮的抽象教训
4. **无 dev/test 分离**：单回测区间，无 held-out 验证门，易过拟合
5. **无基于证据的剪枝**：失败方向不被正式跟踪和传播
6. **反思是单次 ToT**：不是跨迭代的累积假设精炼

### Arbor HTR 核心思想

Arbor 将自主研究组织为**持久假设树上的证据结构化精炼**：

- **Coordinator**（长生命周期）维护假设树，管理全局研究策略
- **Executor**（短生命周期）在隔离 worktree 中测试单个假设，返回结构化证据
- **六步循环**：Observe → Ideate → Select → Dispatch → Backpropagate → Decide
- **Held-out 合并门**：仅当 dev 改进 transfer 到 held-out 时才合并为 current best
- **洞察传播**：局部实验结果向上抽象为方向级教训，累积为全局理解

---

## 二、架构决策

### 2.1 总体架构

将 `strategy_rd` 子图从线性循环替换为 HTR 六步循环，用持久化假设树作为研究状态。

### 2.2 适配映射

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

### 2.3 Held-out 验证门

- **Dev 信号**：现有回测（全区间）— 自由用于探索，指导假设搜索
- **Held-out 信号**：Walk-Forward OOS — 严格合并门
- Walk-Forward 的 expanding-window 设计（`TimeSeriesSplit`）天然将历史切为连续折叠，每个折叠的 test 窗口是真正的 OOS
- 现状：`EventDrivenBacktestEngine.walk_forward_run()` 已实现于引擎层（`core.py:615-740`），但**未通过 `BacktestService` 暴露**。需新增 Protocol 方法 + 实现 + 类型化模型
- 配置参数：`merge_threshold`（OOS 最低改善幅度，默认 5%）、`oos_n_splits`（折叠数，默认 3）

### 2.4 树持久化

- 新增 `HypothesisTreeStore` — JSON 文件持久化（`~/.long_earn/hypothesis_trees/{run_id}.json`），独立于 memory 系统
- 原因：树是结构化研究状态，不是语义事实；现有 memory 的 flat-fact + TF-IDF 检索不适用层级结构
- 现有 memory 保留用于事实检索；tree store 支持跨运行加载历史树，实现"热启动"研究

### 2.5 并行执行

- 引入 LangGraph `Send` API 实现动态 fan-out
- `dispatch` 节点为每个 selected 叶节点返回 `Send` 对象
- `backpropagate` 在 join 点合并所有 executor 结果
- 现有 `stock_analysis` 的固定列表 fan-out 模式不同 — `Send` 允许任意 branching_factor，无需预定义节点名

### 2.6 历史兼容

- 直接替换现有线性流程为 HTR 架构，不保留旧线性模式作为 fallback

---

## 三、新流程图（目标状态）

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

---

## 四、分阶段实施计划

> 执行顺序：ADR-007 → 阶段 1 → 阶段 2 → 阶段 3 → 阶段 4 → 阶段 5
> 逐阶段交付，每阶段含测试 + lint + Serena 诊断

### 阶段 0：ADR-007 文档

**目标：** 记录架构决策，符合项目规范

**新文件：**
- `docs/adr/007-hypothesis-tree-refinement.md` — 记录线性循环 → HTR 的决策、Coordinator/Executor 分离、合并门、与 Arbor 论文的关系

### 阶段 1：假设树领域模型

**目标：** 建立持久化树结构，不改变现有流程，只增加旁路记录

**新文件：**
- `src/long_earn/strategy_rd/hypothesis_tree.py`
  - `HypothesisNode` (dataclass)：
    - `id: str`
    - `parent_id: str | None`
    - `hypothesis: str` — 假设描述
    - `status: Literal["pending", "running", "validated", "pruned", "merged", "failed"]`
    - `strategy_ref: str | None` — 指向策略 dict 的引用 key
    - `dev_score: float | None`
    - `oos_score: float | None`
    - `backtest_result: dict | None`
    - `insight: str` — 可复用语义记忆
    - `children_ids: list[str]`
    - `depth: int`
    - `direction: str` — 收益增强/风险控制/收益稳定性
    - `created_at: str`
  - `HypothesisTree`：
    - `root: HypothesisNode`
    - `_nodes: dict[str, HypothesisNode]`
    - `add_child(parent_id, hypothesis, direction) -> node_id`
    - `update_evidence(node_id, dev_score, oos_score, backtest_result, insight)`
    - `backpropagate_insight(node_id)` — 沿路径向上传播洞察
    - `prune_subtree(node_id)` — 标记子树为 pruned，移出 frontier
    - `frontier() -> list[HypothesisNode]` — pending/running 状态的叶节点
    - `best_node() -> HypothesisNode | None` — oos_score 最高的 validated/merged 节点
    - `current_best_id: str | None`
    - `serialize() -> dict` / `deserialize(data: dict) -> HypothesisTree` — JSON 安全
- `src/long_earn/strategy_rd/tree_store.py`
  - `HypothesisTreeStore`：JSON 文件持久化
  - `save(tree, run_id)`, `load(run_id) -> HypothesisTree | None`, `list_runs() -> list[str]`
  - 路径：`~/.long_earn/hypothesis_trees/{run_id}.json`

**修改文件：**
- `src/long_earn/strategy_rd/state.py` — 新增字段：
  - `hypothesis_tree: dict | None` — 序列化树（LangGraph 状态兼容）
  - `current_best_node_id: str | None`
  - `selected_leaves: list[str] | None` — 当前轮选定的节点
  - `executor_results: list[dict] | None` — 合并 executor 输出
  - `run_id: str | None` — 用于树持久化
  - `oos_threshold: float` — 合并门阈值
  - `oos_n_splits: int`

**测试：**
- `tests/unit/test_strategy_rd/test_hypothesis_tree.py`：
  - `HypothesisNode` 构造与默认值
  - `HypothesisTree` add_child / update_evidence / backpropagate / prune / frontier / best_node
  - serialize / deserialize 往返一致性
- `tests/unit/test_strategy_rd/test_tree_store.py`：
  - save / load 往返
  - list_runs
  - 不存在的 run_id → None

**验证：**
- `uv run pytest tests/unit/test_strategy_rd/test_hypothesis_tree.py tests/unit/test_strategy_rd/test_tree_store.py -v`
- `uv run ruff check src/long_earn/strategy_rd/hypothesis_tree.py src/long_earn/strategy_rd/tree_store.py`
- Serena LSP 诊断目标文件 Error 为空

### 阶段 2：Coordinator 六步循环

**目标：** 用六步循环替代当前的线性 reflection → supervisor → optimize 流程

**新 Prompt 文件（均与 agent .py 同目录）：**
- `strategy_rd/agents/observe_prompt.md` — 输入：树投影、frontier 状态、祖先 insight、当前 best。输出：结构化观察（活跃方向、表现不足区域、约束）
- `strategy_rd/agents/ideate_prompt.md` — 输入：观察结果、父节点假设、子节点 insight、已剪枝节点（负约束）。输出：1-3 个子假设 JSON `[{hypothesis, direction, rationale}]`
- `strategy_rd/agents/backpropagate_prompt.md` — 输入：子节点 insight 列表、父假设。输出：抽象摘要 insight
- `strategy_rd/agents/decide_prompt.md` — 输入：树状态、best dev_score 叶节点、current best OOS、预算。输出：JSON `{action: "continue"|"merge"|"prune"|"stop", node_id, reason}`

**修改文件：**
- `src/long_earn/strategy_rd/strategy_research_agent.py` — 新增方法：
  - `observe(tree) -> str` — LLM 读取树投影
  - `ideate(parent_node, tree, observation) -> list[HypothesisNode]` — LLM 生成子节点
  - `select(tree, pending_leaves) -> list[HypothesisNode]` — frontier 控制（exploit vs explore），返回最多 k 个
  - `backpropagate_insights(node_id, tree) -> None` — LLM 抽象子节点 insight
  - `decide(tree, budget_remaining) -> dict` — merge/prune/continue/stop 决策
- `src/long_earn/strategy_rd/subgraph.py` — 新节点结构：
  - `_init_tree_node` — 创建根节点，加载历史树（若配置 hot-start）
  - `_observe_node` — 调用 `agent.observe(tree)`，写入 `observation_context`
  - `_ideate_node` — 调用 `agent.ideate(parent, tree, observation)`，将子节点加入树
  - `_select_node` — 调用 `agent.select(tree, pending)`，写入 `selected_leaves`
  - `_dispatch_node` — 返回 `[Send("executor", {"node": node, ...}) for node in selected_leaves]`（阶段 5 启用并行，阶段 2 先串行单节点）
  - `_executor_node` — 内部循环：optimize → develop → backtest → refine；返回 `{node_id, dev_score, backtest_result, insight, strategy_yaml}`
  - `_backpropagate_node` — join 所有 executor 结果；回写树证据；调用 `agent.backpropagate_insights`
  - `_decide_node` — 合并门：best 叶节点 OOS vs current best OOS；剪枝；路由
  - `_save_tree_node` — 持久化整个树到 tree store + memory

  复用现有节点逻辑到 executor 内部：
  - `optimize_strategy` 逻辑 → executor 的一部分（hypothesis 成为改进输入）
  - `develop` + `backtest` + `refine` → executor 内部循环（逻辑不变）
  - `save_experience` → `_save_tree_node`（保存整棵树）

**测试：**
- `tests/unit/test_strategy_rd/test_subgraph_htr.py`：
  - 子图编译：`create_strategy_rd_subgraph(context)` 编译成功，路由有效
  - `_decide_node` 合并门逻辑：OOS 阈值执行 — 核心信任路径

**验证：**
- `uv run pytest tests/unit/test_strategy_rd/ -v`
- `uv run ruff check src/long_earn/strategy_rd/`
- `uv run lint-imports`
- Serena LSP 诊断所有修改文件 Error 为空

### 阶段 3：Held-out 验证门

**目标：** 防止 dev 区间过拟合，只有 transfer 到 held-out 的改进才合并

**修改文件：**
- `src/long_earn/services/__init__.py` — `BacktestService` Protocol 新增：
  ```python
  def run_oos(self, strategy_yaml: str, start_date: str = "", end_date: str = "", n_splits: int = 3) -> dict: ...
  ```
- `src/long_earn/services/backtest_service.py` — 实现 `run_oos`，调用 `engine.walk_forward_run()`
- `src/long_earn/backtest/models.py` — 新增 `WalkForwardResult(BaseModel)`：
  - `fold_results: list[FoldResult]`
  - `average_test_metrics: dict[str, float]`
  - `n_splits: int`
  - `failed_folds: list[dict]`
- `src/long_earn/strategy_rd/subgraph.py` — `_decide_node` 调用 `backtest_service.run_oos()` 对 best dev 候选；`oos_score > current_best_oos + threshold` 则 merge

**测试：**
- `tests/unit/test_services/test_backtest_service.py`：
  - `run_oos` Protocol 一致性 — 接口层
  - Walk-Forward 结果结构验证

**验证：**
- `uv run pytest tests/unit/test_services/ tests/unit/test_backtest/ -v`
- `uv run ruff check src/long_earn/services/ src/long_earn/backtest/models.py`
- Serena LSP 诊断

### 阶段 4：洞察传播与记忆增强

**目标：** 让洞察跨迭代累积，增强记忆系统的结构化检索

**修改文件：**
- `src/long_earn/services/memory_service.py` — 新增：
  - `save_hypothesis_tree(tree: HypothesisTree) -> None` — 将树摘要作为 fact 存入 Core 层，`category="研究树"`
  - `search_hypothesis_trees(query: str, k: int = 3) -> list[dict]` — 检索历史树摘要
- `src/long_earn/strategy_rd/strategy_research_agent.py`：
  - `_ideate` 注入历史树洞察（从 `memory.search(category="研究树")`）
  - `_observe` 可加载之前的树实现 hot-start

**测试：**
- `tests/unit/test_services/test_memory_service.py`：
  - `save_hypothesis_tree` / `search_hypothesis_trees` 接口层验证

**验证：**
- `uv run pytest tests/unit/ -v`
- `uv run ruff check src/long_earn/services/memory_service.py`
- `uv run lint-imports`
- Serena LSP 诊断

### 阶段 5：并行执行

**目标：** 利用 LangGraph `Send` API 并行测试多个假设

**修改文件：**
- `src/long_earn/strategy_rd/subgraph.py`：
  - `_dispatch_node` 返回 `Send` 对象：
    ```python
    def _dispatch_node(state, ...):
        return [Send("executor", {"node_id": n.id, "hypothesis": n.hypothesis, ...})
                for n in selected_leaves]
    ```
  - `_backpropagate_node` 作为 join 节点，使用 LangGraph barrier 语义
- `src/long_earn/strategy_rd/state.py`：
  - `executor_results: Annotated[list[dict], _collect_results]` — 自定义 reducer 收集数组：
    ```python
    def _collect_results(left: list, right: list) -> list:
        return (left or []) + (right or [])
    ```

**测试：**
- `tests/unit/test_strategy_rd/test_subgraph_htr.py`：
  - 并行 dispatch + join 语义验证
  - `executor_results` reducer 行为

**验证：**
- `uv run pytest tests/unit/test_strategy_rd/ -v`
- `uv run ruff check src/long_earn/strategy_rd/`
- Serena LSP 诊断

---

## 五、测试策略

根据 CLAUDE.md 测试原则（仅接口层 + 核心引擎路径）：

| 测试目标 | 类型 | 说明 |
|---------|------|------|
| `HypothesisTree` CRUD | 单元 | add/update/backprop/prune/frontier/best/serialize 往返 — 结构正确性 |
| `_decide_node` 合并门 | 单元 | OOS 阈值执行 — 核心信任路径 |
| `BacktestService.run_oos` | 接口层 | Protocol 一致性 |
| 子图编译 | 接口层 | `create_strategy_rd_subgraph(context)` 编译且路由有效 |
| `HypothesisTreeStore` 持久化 | 接口层 | save/load 往返 |
| `MemoryService` 树接口 | 接口层 | `save_hypothesis_tree` / `search_hypothesis_trees` |
| 并行 dispatch + join | 单元 | `Send` API + `executor_results` reducer |

---

## 六、架构兼容性

保留的设计模式与约定：
- `functools.partial` 节点注入（ADR-002）— 新节点同样用 partial 绑定服务
- `RuntimeContext` DI — 树管理器作为新服务注入
- Markdown prompt 共置 — 新 prompt .md 与 agent .py 同目录
- 记忆系统 — 扩展而非替换，新增树持久化能力
- 回测引擎 — 复用 Walk-Forward 做 held-out 验证
- 韧性回退 — 所有新 LLM 节点需有规则回退
- `KnowledgeContextMixin` — 知识检索复用

移除的组件：
- 旧线性流程节点（`init`, `supervisor`, `optimize`, `develop_optimized`, `backtest_optimized`, `refine_optimized`, `save_experience` 等）— 逻辑迁移到 executor 内部或新节点
- `strategy_optimize_prompt`（inline PromptTemplate）— 逻辑融入 executor
- `strategy_rd_supervisor_prompt.md` / `strategy_rd_supervisor_continue_prompt.md` — 被 `decide_prompt.md` 替代

---

## 七、风险与权衡

1. **复杂度增加**：树结构 + 并行执行显著增加状态管理复杂度。阶段 1-2 先做串行 dispatch，阶段 5 再加并行，降低初始复杂度
2. **LLM 调用成本**：observe/ideate/backpropagate/decide 都是额外 LLM 调用。可通过配置 `max_cycles` 和 `branching_factor` 控制
3. **回测成本**：held-out 验证增加回测次数。可配置 `merge_threshold` 控制频率
4. **Walk-Forward 数据需求**：需要足够长的历史数据支撑 OOS 验证
5. **直接替换不可回退**：不保留旧线性流程，需确保新流程完整后再移除旧代码

---

## 八、配置项变更

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `HTR_MAX_CYCLES` | 10 | HTR 六步循环最大轮数 |
| `HTR_BRANCHING_FACTOR` | 3 | 每轮 ideate 生成的子假设数 |
| `HTR_MAX_DEPTH` | 3 | 假设树最大深度 |
| `HTR_MERGE_THRESHOLD` | 0.05 | OOS 合并门最低改善幅度（5%） |
| `HTR_OOS_N_SPLITS` | 3 | Walk-Forward OOS 折叠数 |
| `HTR_HOT_START` | false | 是否加载历史树热启动 |
| `HYPOTHESIS_TREE_PATH` | ~/.long_earn/hypothesis_trees/ | 树持久化目录 |

---

## 九、参考文献

- **Arbor 论文**：[arXiv:2606.11926](https://arxiv.org/abs/2606.11926) — Toward Generalist Autonomous Research via Hypothesis-Tree Refinement
- **Arbor 代码**：[GitHub](https://github.com/RUC-NLPIR/Arbor)
- **Arbor 项目主页**：[https://ruc-nlpir.github.io/Arbor/](https://ruc-nlpir.github.io/Arbor/)
- **ADR-002**：`functools.partial` 节点注入
- **ADR-005**：事件驱动回测框架（Walk-Forward 基础）
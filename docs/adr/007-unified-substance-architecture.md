# ADR-007: 物质-运动统一架构（Substance-Motion）

日期: 2026-06
状态: Accepted, Implemented (Phase 1)

## 背景

ADR-004 采纳的 numpy/pandas 三级记忆系统在 v2.0 增强后暴露出结构性缺陷，维护成本已高于迁移成本：

### 结构性缺陷（非修补可解）

1. **事件与关系二元分裂**：`MemoryStore`（list + TF-IDF）与 `RelationGraph`（邻接矩阵）完全解耦。fact 不知 relation，relation 无 provenance（无来源、无时间戳、无置信度）。违反"普遍联系"。
2. **检索单通道**：只有 TF-IDF 余弦相似度一条路，无 WorldInfo 式关键词触发机制。所有检索被降维为同一种运动形式。
3. **TF-IDF 特征选择反转**：`fit()` 按文档频率**降序**保留词（保留最高频词），与标准 TF-IDF 应保留区分性强的罕见词相反。
4. **全量 refit**：`_ensure_vectors()` 每次 dirty 都 `fit_transform` 全量重建，O(n²) 不可扩展。
5. **线性扫描**：`get_fact_by_id`、`_find_index`、`_get_or_create_index` 全是 O(n) 扫描，无哈希索引。
6. **400MB 预分配**：`RelationGraph` 邻接矩阵 `np.zeros((10000, 10000), float32)` = 400MB，即使空图。
7. **边无元数据**：`RelationGraph` 边只有 `(source, target, weight)`，无类型、无时间、无来源。关系是"二等公民"。
8. **冲突检测硬编码**：16 个中文金融词的情绪判断，不可配置、不可多语言。
9. **pickle 不安全**：`allow_pickle=True` 的 `.npz` + `.pkl`，无版本号、无 schema 校验、有反序列化安全风险。
10. **EmbeddingRetriever 脆弱**：`hybrid_search` 按内容字符串对齐 TF-IDF 与 embedding 结果（O(n²)、碰撞）；缓存按 `fact_count` 失效（内容变更不可感知）。

### 影响范围（已验证，可控）

| 层 | 触点 | 风险 |
|---|---|---|
| `MemoryService` Protocol（8 方法） | 签名保持不变 | 零 |
| DI 注入（`context_init.py`、`config.py`） | 构造不变 | 低 |
| 生产消费方（`strategy_rd` 4 文件 5 调用点） | Protocol 不变 → 零改动 | 零 |
| `stock_analysis` | 不使用记忆 | 零 |
| `tools/store.py`（遗留兼容层） | 2 行 import 变更 | 低 |
| `MemoryServiceImpl`（315 行业务逻辑） | 内部存储替换，业务逻辑保留 | 中 |
| 旧 `memory/`（5 文件 ~1500 行） | 删除 | — |
| 旧测试（4 单元 + 1 集成） | 重写 | 中 |

## 决策

用"物质-运动"统一架构替换旧记忆系统，**立即删除** `memory/` 模块（数据备份到 temp）。

### 设计哲学

马克思主义辩证法映射：

| 哲学范畴 | 架构对应 |
|---|---|
| **物质**（客观实在） | `Substance`——统一存在基类，可持久化、可检索、有来源 |
| **物质形态**（粒子/波） | `SubstanceForm`——event（粒子态）/ relation（波态）/ knowledge / strategy / backtest |
| **运动**（物质的存在方式） | `motion.py` 中的运动函数——activate/decay/conflict/compress。运动不持久化，只产出新物质 |
| **普遍联系** | 关系是一等物质（有完整 provenance）；GraphIndex 邻接表 |
| **对立统一** | `conflict_group` 互斥组 + `detect_conflicts` |
| **量变到质变** | `decay` 量变累积；事件累积到阈值触发策略信号（Phase 3） |
| **波粒二象性** | 同一 Substance 有两种检索视图：RetrievalIndex（内容/关键词）与 GraphIndex（关系遍历） |

借鉴 SillyTavern WorldInfo：
- 关键词触发（含正则）+ 可选过滤键（AND/NOT 逻辑）
- 递归激活（已激活物质的内容再激活其他物质）
- Inclusion Group（同组互斥，取 insertion_order 最高者）
- Timed Effects（visible_from 防未来函数、expires_at 失效、decay 衰减）
- Token 预算控制

### Substance 数据模型（Pydantic）

采用 Pydantic `BaseModel`（与项目 `BacktestResult`、`StrategyDSL` 一致），免费获得 schema 校验、JSON 序列化、版本化：

```python
class SubstanceForm(str, Enum):
    EVENT = "event"
    RELATION = "relation"
    KNOWLEDGE = "knowledge"
    STRATEGY = "strategy"
    BACKTEST = "backtest"

class FilterLogic(str, Enum):
    AND_ANY = "and_any"
    AND_ALL = "and_all"
    NOT_ANY = "not_any"
    NOT_ALL = "not_all"

class Substance(BaseModel):
    sid: str
    form: SubstanceForm
    content: str
    keys: list[str] = []                # WorldInfo 触发词
    filter_keys: list[str] = []
    filter_logic: FilterLogic = FilterLogic.AND_ANY
    created_at: datetime
    visible_from: datetime | None = None
    expires_at: datetime | None = None
    source: str = "manual"
    confidence: float = 1.0
    source_id: str | None = None         # relation 专用
    target_id: str | None = None
    relation_type: str | None = None
    conflict_group: str | None = None
    insertion_order: int = 0
    decay_half_life_days: float = 90.0
    metadata: dict[str, Any] = {}
```

### 双索引（波粒二象性）

**RetrievalIndex**（`indices/retrieval.py`）—— 粒子态检索，双通道融合：
- **关键词通道**：`dict[str, list[str]]` 倒排索引（key → sid 列表），正则 key 编译存储
- **语义通道**：TF-IDF（修复特征选择为 IDF 升序 + jieba 分词）或 embedding（可选）
- **融合**：keyword 命中优先，semantic 补充，alpha 加权
- **增量更新**：新物质只 transform 不 refit；定期全量 refit
- **缓存**：按 content hash 失效（不按 count）

**GraphIndex**（`indices/graph.py`）—— 波态检索：
- **邻接表** `dict[str, list[str]]`（不预分配矩阵，内存高效、无容量上限）
- 边指向 substance sid，权值/类型/provenance 从对应 relation 物质读取
- BFS 返回 `(sid, path, distance, weight)`（不再只返回节点名）

> **时间过滤不设独立索引**：visible_from/expires_at 是施加在任意查询结果上的谓词（post-filter），<100K 物质量级下成本可忽略，独立排序索引是过度优化。

### 运动层（motion.py）

运动是过程，不持久化，只产出新物质或变更状态：

- `activate(text, store, budget=2000, max_recursion=3, visible_at=None)` — WorldInfo 激活引擎：分词 → KeyIndex 查候选 → filter_logic 过滤 → conflict_group 互斥 → 递归扫描 → 时间过滤 → 预算截断
- `decay(store, half_life_map)` — 按 form 配不同半衰期（新闻短、知识长、回测中等）
- `detect_conflicts(store, substance)` — 可配置词库（不再硬编码 16 词）
- `compress(store, min_similarity=0.6)` — 修复聚类算法（re-center 或传递闭包）

### 持久化（安全 + 版本化）

```
~/.long_earn/substances.jsonl      # 每行一个 Substance JSON（Pydantic 序列化）
~/.long_earn/vectors.npy            # 向量矩阵（numpy save，无 pickle）
~/.long_earn/meta.json              # schema_version, substance_count, last_decay_run
```

- 无 `allow_pickle`，无反序列化安全风险
- `schema_version` 支持迁移
- 索引从 JSONL 重建，不持久化

### MemoryService Protocol 兼容

Protocol 8 方法签名不变，`MemoryServiceImpl` 内部委托 `SubstanceStore`：

| 旧方法 | 新实现 |
|---|---|
| `recall(query, k, **filters)` | `store.search(query, k, **filters)` |
| `remember(content, **metadata)` | `store.add(Substance(form=KNOWLEDGE, ...))` |
| `search(query, k, **filters)` | `recall` + 格式化字符串 |
| `reflect(summary)` | 业务逻辑保留（正则提取 + remember + relate） |
| `relate(s, t, rel, w)` | `store.add(Substance(form=RELATION, source_id=s, target_id=t, ...))` |
| `save_experience(...)` | 业务逻辑保留（构造 STRATEGY 物质 + metrics） |
| `search_experience(query, k, min_sharpe)` | 业务逻辑保留（recall + 过滤 + 正则提取） |
| `initialize()` | `store.load(path)` 或 `store.load_directory(init_dir)` |

**结果**：`strategy_rd` 4 文件 5 调用点零改动。

## 文件结构

```
src/long_earn/substance/
├── __init__.py              # 导出 Substance, SubstanceForm, SubstanceStore 等
├── model.py                 # Substance(Pydantic) + SubstanceForm + FilterLogic
├── store.py                 # SubstanceStore（统一存储 + 索引协调 + 时间过滤）
├── motion.py                # 运动层（activate/decay/conflict/compress）
├── persistence.py           # JSONL 读写（Pydantic 序列化，~20 行）
└── indices/
    ├── __init__.py
    ├── retrieval.py         # RetrievalIndex（keyword 通道 + semantic 通道 + 融合）
    └── graph.py             # GraphIndex（dict 邻接表 + BFS 返回路径）
```

6 个源文件（不含 `__init__`），比旧 `memory/` 5 文件仅多 1，但能力全面超越。

## 实施计划

### Phase 1：SubstanceStore 核心 + 旧系统移除

**Step 1**：`model.py`（Substance Pydantic）+ `store.py`（SubstanceStore）+ `indices/retrieval.py`（RetrievalIndex 双通道）+ `indices/graph.py`（GraphIndex 邻接表）+ `persistence.py`（JSONL）

**Step 2**：`motion.py`（activate WorldInfo 引擎 + decay + detect_conflicts + compress）

**Step 3**：重写 `MemoryServiceImpl` 委托 SubstanceStore + 更新 `tools/store.py`（消费方零改动）

**Step 4**：备份旧数据到 temp + 删除 `memory/` + 重写测试 + 更新 `config.py`/`import-linter`/`CLAUDE.md`/`TODO.md`

**验证门槛**：
```sh
uv run ruff check src/long_earn/substance/ src/long_earn/services/memory_service.py
uv run lint-imports                             # substance 独立合约 0 broken
uv run pytest tests/unit/test_substance/ -v
uv run pytest tests/unit/test_strategy_rd/ -v   # Protocol 不变 → 不破
uv run pytest tests/unit/ -v
```
Serena LSP 诊断：每个修改文件 `Error` 级别诊断为空。

### Phase 2：采集器 + 事件推理子图

- Collector registry + Kimi（包装现有 `tools/kimi_web_search.py`）/ Tencent / ciccwm 采集器
- 事件推理子图：collect → extract → propagate（L2 影响传播，LLM 辅助） → conflict → save
- 主图新增"事件推理"路由

### Phase 3：子图集成 + Dashboard

- `stock_analysis` / `strategy_rd` 调 `store.activate()` 注入事件上下文（helper 函数，非独立模块）
- Dashboard 事件流可视化

## 理由

1. **哲学-实现统一**：物质（Substance）与运动（motion 函数）是代码中的一等概念，不是 ADR 注释。减少认知分裂。
2. **关系升为一等公民**：relation 物质有完整 provenance（来源、时间、置信度、衰减），与 event 对等。
3. **双通道检索**：WorldInfo 关键词触发 + 语义相似度融合，比旧系统单 TF-IDF 通道表达力强一个量级。
4. **修复全部已知缺陷**：TF-IDF 特征选择、中文分词、线性扫描、400MB 预分配、pickle 安全、embedding 缓存脆弱——一次性解决。
5. **Pydantic 一致性**：与项目 `BacktestResult`、`StrategyDSL` 技术栈统一，persistence 从 ~100 行缩到 ~20 行。
6. **邻接表简化**：dict 邻接表比 numpy 矩阵更简单、更可测、内存高效，BFS 性能在小规模下等同。

## 后果

- 删除 `src/long_earn/memory/`（5 文件 ~1500 行）+ 旧测试（4 单元 + 1 集成）
- 新增 `jieba>=0.42.1` 依赖
- `AppConfig.memory_path` 默认值 `.data/memory.npz` → `.data/substances.jsonl`
- 旧数据文件备份到 `$TEMP/opencode/`，一次性迁移脚本 `scripts/migrate_memory.py`
- import-linter 新增 `substance_independent` 合约
- ADR-004 状态改为 Superseded by ADR-007
- Phase 2/3 完成后新闻事件推理引擎上线

## 对 CLAUDE.md TODO 的影响

CLAUDE.md "记忆系统" 4 项 TODO（语义增强检索/记忆压缩/记忆衰减/冲突检测）在 TODO.md 标记为"v2.0 完成"，但正是被替换的旧实现。Phase 1 完成后重新标记为"v3.0 物质-运动架构重构"。

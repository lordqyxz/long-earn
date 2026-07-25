# ADR-014: 全局本体论 + 连接器 + 图驱动推理

日期: 2026-07
状态: Accepted

## 背景

长期量化交易系统的核心目标是**策略研究成功率和运行效率**。当前架构在推理准确性、取数效率、多数据源适配上存在系统性瓶颈，根因不是某个模块的实现缺陷，而是**缺乏统一的本体论层**导致数据、记忆、事件、策略经验各自为政、无法关联推理。

### 现状瓶颈

#### 1. 财务数据：扁平宽表 + 字段清单硬编码分裂

- `backtest_cache.duckdb` 的 `financial_quarterly` 单一宽表只覆盖 xtquant 8 张财务表中的 4 张（Income/Balance/CashFlow/Pershareindex），**遗漏 Capital/Holdernum/Top10holder/Top10flowholder 四张表**——股东户数、股本、十大股东数据全部缺失
- 字段清单两处硬编码、手动对齐、无单一事实源：`cache.py:21-49` 的 `_FINANCIAL_QUARTERLY_COLUMNS` 与 `miniqmt_provider.py:55-79` 的 `FINANCIAL_FIELD_MAP`，注释明写"22 列，与 save_financials 的 cache_columns 对齐"。新增字段需改 4 处（schema 版本 / FIELD_MAP / `_extract_*_fields` / `save_financials`），遗漏是结构性必然
- Top10holder/Top10flowholder 每季度 10 行，**塞不进 `(symbol, report_date)` 一行一记录的扁平宽表契约**——宽表模型对多行结构不适用，当前做法是"做不了的干脆跳过"

#### 2. 推理准确性：硬编码阈值 + 关键词递归 + 字段清单不一致

- **`_evaluate_branches`（`strategy_research_agent.py:496`）**：ToT 4 个方向打分全靠 `if sharpe < 0.3: score += 30` 这类硬编码规则，不区分 universe/行业/市场状态。这是"推理不准"的最大单点——决定选哪个分支继续演进
- **`_identify_primary_issue:269`**：只有 4 个粗粒度方向（收益增强/风险控制/收益稳定性/策略家族切换），无子方向
- **财务字段三处硬编码不一致**：`strategy_develop_prompt.md`（16+ 字段带口径）、`strategy_research_prompt.md`（仅 7 字段）、`strategy_research_prompt.py:64`（16 字段字符串）。研究阶段看到的字段比开发阶段少 9 个
- **监督器 `decision_history="无"`**（`strategy_rd_supervisor.py:83`）：每轮决策独立，看不到历史迭代轨迹，无法基于"连续 3 轮收益下降"这类趋势判断收敛

#### 3. 记忆激活：关键词递归漏召回，图谱断裂

- `motion.activate` 的 `_activate_recursive`（`motion.py:79-99`）靠"已激活物质的 content 文本再激活其他物质"——**不走图边**。事件A→影响公司B→公司B相关策略经验C 这种链路无法走图谱，除非 C 的 keys 字面出现在 A 或 B 的 content 里
- `MemoryServiceImpl.activate_events`（`memory_service.py:172-182`）用 O(N) 线性扫描 `store.get_all()` 找 `source_id in event_sids` 的 RELATION 物质，**完全不调 GraphIndex.bfs**
- **图谱断裂点**：`memory_service.py:327` 把 `target_id` 设为标的代码字符串（`600519.SH`）甚至行业名（`白酒`），而 `source_id` 是 EVENT 物质 sid——**target 端不是 substance**，图遍历到 target 后无法继续 hops
- `GraphIndex`（`substance/indices/graph.py`）有 BFS 能力但：不支持按 `relation_type` 过滤、不支持按节点 `form` 过滤、`_reverse` 邻接表已维护但**无 API 暴露**

#### 4. 事件→策略调整链路断点

- `_reflection_node` 构造 `PersonaContext` 时 `event_context=""`（默认）——大师审视策略时看不到事件
- HTR 子图（`htr_subgraph.py`）**完全没有调用 `activate_events`**——HTR 研究循环对市场事件盲视
- 事件 RELATION 的 `target_id` 是股票代码，不是财务字段，**"央行降息→对 debt_to_assets 高的标的影响更大"无法自动推理**——本体论里没有"事件类型→敏感字段"的边
- 事件只在 `_initial_retrieval_node` 注入一次，之后 optimize/reflection/HTR 都不再更新

#### 5. 多数据源：降级链死代码 + 字段命名三层不一致 + 无概念层

- `CompositeDataProvider` 声称 DuckDB→miniqmt→ciccwm→akshare，实际 `get_price_panel`/`get_financial_panel` **根本没有调用 ciccwm/akshare**（降级分支已删除）
- 字段命名三层不一致：miniqmt 内部做映射、akshare 中文→英文映射、ciccwm 情报接口完全保留原始字段名。Protocol 层无强约束
- **无"本体概念"层**：上层必须自己列举 `roe/gross_margin/net_profit_margin` 等字段名，没有"盈利能力"这个概念对应物。`StockServiceImpl.get_financial_metrics` 绕过 Protocol 直接调 `MiniQmtClient`，字段名 `operating_revenue` 与标准 `revenue` 不一致

#### 6. 两套策略研发子图并存

- ADR-010 文档明说"直接替换现有线性流程"，但代码现状是**两套并存**：`subgraph.py` 仍是主入口（15 节点 4 层循环），`htr_subgraph.py` 是独立新入口
- HTR 子图存在死代码：`_decide_node:505` 已 return，507-521 永不执行；`_dispatch_node` 是死代码（`_dispatch_cond` 才是真路由）；`_observe_node:76` 的 `pruned_directions="无"` 硬编码（剪枝未接入）
- Persona 大师只在旧线性 `subgraph.py` 生效，HTR 子图完全不调大师

### 需求

用户明确：
1. **核心目标是提高策略研究成功率和运行效率**，不是维持架构稳定
2. **本体论不只是为财务数据**，记忆模块、策略交易模块也要通过图谱提高推理和理解准确性
3. 通过某个本体概念可以通过连接器准确获取数据，**能够适配各种数据源**
4. 按新架构全新设计，旧的一套在收益率改进上效果不好

## 决策

建立**全局本体论层 + 单一概念查询连接器 + 图驱动记忆激活**，统一财务、记忆、事件、策略、经验的数据访问与推理增强。

### 四项核心决策（已与用户确认）

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 本体论覆盖范围 | **全局统一本体图谱** | 财务/公司/行业/事件/策略/经验/universe 全部纳入，跨域关联推理 |
| 连接器接口形态 | **单一概念查询入口** `get_concept(ConceptQuery)` | 上层完全不碰字段名/数据源/符号格式/PIT |
| motion 与 GraphIndex | **motion.activate 改走图遍历** | 废弃关键词递归，图驱动跨域激活 |
| 两套子图去留 | **废弃旧线性流程，统一到 HTR** | 修死代码，六步循环全部接入图谱+连接器 |

---

### A. 本体论核心模型 — `ontology/model.py`

#### A.1 节点域（OntologyDomain）

```python
class OntologyDomain(StrEnum):
    ENTITY = "entity"           # 公司/行业/板块/Universe
    INDICATOR = "indicator"     # 财务指标/技术指标
    EVENT = "event"             # 市场事件/公告事件
    STRATEGY = "strategy"       # 策略实例
    EXPERIENCE = "experience"   # 策略经验/教训
    CONCEPT = "concept"         # 抽象概念（盈利能力/动量族/大盘蓝筹）
```

#### A.2 节点（OntologyNode）

```python
class OntologyNode(BaseModel):
    sid: str                     # 唯一 ID
    domain: OntologyDomain
    label: str                   # 人类可读名
    aliases: list[str] = []      # 别名（["roe","净资产收益率","ReturnOnEquity"]）
    properties: dict[str, Any] = {}  # 领域专属属性
```

**sid 统一约定**：
- substance 物质的 sid 直接作为本体节点 sid（substance 是本体节点的子集）
- 本体内部概念节点用领域前缀 sid：`indicator:roe`、`concept:profitability`、`entity:600519.SH`、`concept:universe:csi300`

#### A.3 关系类型（RelationType）— 受约束的枚举

```python
class RelationType(StrEnum):
    IMPACTS = "impacts"                          # 事件→标的/行业
    PROPAGATES_TO = "propagates_to"              # 事件→传导目标
    BELONGS_TO = "belongs_to"                    # 公司→行业/板块
    MEMBER_OF = "member_of"                      # 标的→Universe
    REPORTS_INDICATOR = "reports_indicator"      # 公司→指标（某期财报）
    DERIVED_FROM = "derived_from"                # 指标→父指标（杜邦分解）
    SAME_DIMENSION = "same_dimension"            # 同维指标（ROE 同维 ROIC/ROA）
    APPLIES_STRATEGY = "applies_strategy"        # 经验→策略
    DERIVED_FROM_EXPERIENCE = "derived_from_experience"  # 策略→经验
    RELATES_TO_CONCEPT = "relates_to_concept"    # 任意节点→抽象概念
    CORRELATES_WITH = "correlates_with"          # 跨域关联
```

**替代旧自由字符串**：当前 `relation_type` 是自由 str（`impacts`/`propagates_to`/`correlates_with`/`related_to` 语义重叠且无校验）。`store.add_relation` 入口校验 `relation_type` 在 `RelationType` 内。

#### A.4 边（OntologyEdge）

```python
class OntologyEdge(BaseModel):
    source_sid: str
    target_sid: str
    relation_type: RelationType
    weight: float = 1.0
    provenance: str = ""          # 来源（xtquant/wind/llm_inferred/manual）
    visible_from: datetime | None = None  # PIT：边何时可见
    metadata: dict[str, Any] = {}
```

---

### B. 本体图谱 — `ontology/graph.py`

替代现有 `substance/indices/graph.py`，支持跨域遍历：

```python
class OntologyGraph:
    def traverse(
        self, start_sid: str, *,
        max_depth: int = 2, min_weight: float = 0.1,
        relation_types: set[RelationType] | None = None,   # 边类型过滤
        domain_filter: set[OntologyDomain] | None = None,  # 目标节点域过滤
        direction: Literal["forward", "reverse", "both"] = "forward",
        visible_at: datetime | None = None,                # PIT 过滤
    ) -> list[GraphPath]: ...
    
    def resolve_concept(self, concept_sid: str) -> set[str]: ...
    def find_path(self, source_sid: str, target_sid: str, max_depth: int = 4) -> list[str] | None: ...
```

**与旧 GraphIndex 的差异**：
- 边是 `OntologyEdge` 对象（含 relation_type/visible_from），不再是 `(target, rel_sid, weight)` 元组
- 支持按 `relation_types` / `domain_filter` 过滤（跨域查询"公司→所属行业→行业指标→影响该指标的事件"）
- 支持反向遍历（`direction="reverse"`，查"哪些事件影响了我"）
- 支持 PIT 过滤（`visible_at` 参数，边/节点都要过 `is_visible_at`）
- 节点旁路索引支持按域过滤

---

### C. 单一概念查询连接器 — `ontology/connector.py`

#### C.1 查询与结果

```python
@dataclass
class ConceptQuery:
    subject: str            # "600519.SH" / "csi300" / "贵州茅台"
    aspect: str             # "盈利能力" / "成分股" / "相关事件" / "动量族经验"
    time: str = ""          # "2024Q3" / "2024-01-01~2024-12-31" / "latest"
    as_of: str = ""         # PIT 裁剪时刻（回测当日）
    constraints: dict[str, Any] = field(default_factory=dict)

@dataclass
class ConceptResult:
    concept: str
    subject: str
    data: pl.DataFrame | dict | list
    provenance: list[str] = []         # 数据源链
    related_nodes: list[OntologyNode] = []  # 图谱关联（供 LLM 推理）
    paths: list[GraphPath] = []        # 溯源路径
```

#### C.2 单一入口

```python
class Connector:
    def get_concept(self, query: ConceptQuery) -> ConceptResult:
        # 1. 解析 subject（名称→symbol，universe→成分股，symbol→entity_sid）
        # 2. 解析 aspect（概念→字段集/universe/情报方法/图谱查询）
        # 3. 按 resolution 类型分发：indicator_panel / universe / event_graph / experience / intelligence
        # 4. 图谱关联节点（供 LLM 推理增强）
        # 5. 溯源路径
        ...
```

**上层完全不碰字段名、数据源、符号格式、PIT 逻辑**。`related_nodes` 和 `paths` 让 LLM 拿到结构化图谱关联（替代当前平铺文本注入 prompt）。降级链在连接器内部真实生效（修复当前死代码）。

#### C.3 概念解析表 — `ontology/concept_resolver.py`

声明式翻译表，可由本体种子数据驱动：
- 概念→字段集：`"盈利能力" → ["roe","roe_weighted","gross_margin","net_profit_margin","net_profit_yoy","revenue_yoy"]`
- 概念→universe：`"大盘蓝筹" → "csi300"`
- 概念→情报方法：`"市场情绪" → [get_hot_rank, get_topic_news]`
- 实体解析：`"贵州茅台" → "600519.SH" → entity_sid`

新增数据源只需在 `concept_resolver` 加映射 + 实现 provider，上层无感。

---

### D. 本体种子数据 — `ontology/seed/`

#### D.1 财务指标本体（`financial_ontology.py`）

杜邦分解 + 盈利能力族 + 成长性族 + 估值族 + 质量族：

```python
ROE_NODE = OntologyNode(sid="indicator:roe", domain=INDICATOR, label="净资产收益率 ROE",
    aliases=["roe","净资产收益率"],
    properties={"formula": "net_profit / total_equity * 年化系数", "dimension": "盈利能力"})

# 杜邦三分解
DUPONT_EDGES = [
    OntologyEdge("indicator:roe", "indicator:net_profit_margin", DERIVED_FROM),
    OntologyEdge("indicator:roe", "indicator:asset_turnover", DERIVED_FROM),
    OntologyEdge("indicator:roe", "indicator:equity_multiplier", DERIVED_FROM),
]

# 盈利能力概念组
PROFITABILITY_CONCEPT = OntologyNode(sid="concept:profitability", domain=CONCEPT,
    label="盈利能力",
    properties={"resolution": {"aspect": "盈利能力", "fields": [
        "roe","roe_weighted","gross_margin","net_profit_margin","net_profit_yoy","revenue_yoy"]}})
```

#### D.2 实体本体（`entity_ontology.py`）

公司/行业/板块/Universe 节点 + 启动时从 universe 动态注册：

```python
CSI300_CONCEPT = OntologyNode(sid="concept:universe:csi300", domain=CONCEPT,
    label="沪深300", aliases=["csi300","沪深300","大盘蓝筹"],
    properties={"universe_type": "csi300"})

def register_entity(graph, xt_symbol, name, industry, sector):
    entity_sid = f"entity:{xt_symbol}"
    graph.add_node(OntologyNode(sid=entity_sid, domain=ENTITY, label=name, ...))
    graph.add_edge(OntologyEdge(entity_sid, f"concept:industry:{industry}", BELONGS_TO))
```

#### D.3 策略族 + 经验本体（`strategy_ontology.py`）

4 策略族（动量/价值/质量/成长）+ 经验自动建边：

```python
MOMENTUM_FAMILY = OntologyNode(sid="concept:strategy:momentum", domain=CONCEPT,
    label="动量族", properties={"typical_factors": ["returns","shift","sma_ema","macd"]})

def link_experience_to_family(graph, experience_sid, strategy_family):
    graph.add_edge(OntologyEdge(experience_sid, f"concept:strategy:{strategy_family}", RELATES_TO_CONCEPT))
```

#### D.4 事件类型本体（`event_ontology.py`）

事件类型→敏感指标映射，让"央行降息"自动关联到"对 debt_to_assets 高的标的影响更大"：

```python
EVENT_TYPES = [
    OntologyNode(sid="event_type:macro_policy", domain=CONCEPT, label="宏观政策",
        properties={"sensitive_indicators": ["debt_to_assets","capex"]}),
    OntologyNode(sid="event_type:industry_event", domain=CONCEPT, label="行业事件", ...),
    OntologyNode(sid="event_type:company_announcement", domain=CONCEPT, label="公司公告", ...),
]
```

---

### E. motion.activate 重写 — 图遍历优先

```python
def activate(text, store, graph, budget=2000, max_depth=3, visible_at=None) -> list[Substance]:
    when = visible_at or datetime.now()
    store._ensure_index()
    # 1. 关键词首轮（入图种子，保留 _activate_first_round）
    seed = _activate_first_round(store, text, when)
    if not seed: return []
    # 2. 图遍历扩展（替代 _activate_recursive 的关键词递归）
    activated = dict(seed)
    for sid in list(seed.keys()):
        paths = graph.traverse(sid, max_depth=max_depth, min_weight=0.1,
            direction="both", visible_at=when)  # 正反向都走
        for p in paths:
            if p.sid not in activated:
                sub = store.get_by_sid(p.sid)
                if sub and sub.is_visible_at(when):
                    activated[p.sid] = sub
    # 3. conflict_group 互斥（保留）
    activated = _resolve_conflict_groups(activated)
    # 4. 排序：图遍历路径权重 × 衰减因子，降序截断
    return sorted(activated.values(),
        key=lambda s: s.insertion_order * s.decay_factor(when), reverse=True)[:budget]
```

**废弃** `_activate_recursive`（关键词递归，O(N²) 且漏召回）。事件→标的→经验→策略的跨域激活链路打通。

---

### F. HTR 六步循环全部接入图谱+连接器

废弃 `strategy_rd/subgraph.py`（线性流程），HTR 成为唯一入口。六步循环改造：

| 步骤 | 旧实现 | 新实现 |
|------|--------|--------|
| **observe** | `pruned_directions="无"` 硬编码 | `connector.get_concept(aspect="研究上下文")` 注入 related_nodes + paths；pruned_directions 从 HypothesisTreeStore 查 blocked 方向 |
| **ideate** | `memory.search_hypothesis_trees(query, k=2)` 文本 TF-IDF | `connector.get_concept(aspect="相关假设树经验", constraints={strategy_family})` 图谱遍历 |
| **executor** | `backtest_service.run(start_date="", end_date="")` 空日期 + 每假设独立取数 | 连接器取数 + 同 universe 多假设共享面板缓存 |
| **backpropagate** | `tree.backpropagate_insight(node_id)` + LLM | 洞察保存时自动建 `RELATES_TO_CONCEPT` + `DERIVED_FROM_EXPERIENCE` 边 |
| **decide** | 505-521 死代码 + 无图谱视角 | 删除死代码 + 注入 `connector.get_concept(aspect="相似失败案例")` |
| **Persona** | HTR 完全不调大师 | observe/ideate 后追加 `PersonaRegistry.create_all().analyze()`，大师 prompt 拿结构化图谱关联 |

**财务字段清单从本体图谱渲染**：消除 `develop_prompt.md`(16字段)/`research_prompt.md`(7字段)/`research_prompt.py:64`(16字段) 三处硬编码不一致。

---

### G. 财务数据层重构（本体论的第一批应用）

#### G.1 8 张细表 schema — `backtest/data/financial/schemas.py`

- 6 张标量表（Income/Balance/CashFlow/Pershareindex/Capital/Holdernum）→ 宽表，主键 `(symbol, report_date)`
- 2 张长表（Top10holder/Top10flowholder）→ 长表，主键 `(symbol, report_date, rank)`
- 字段映射单一事实源（xtquant 原始字段 → 标准字段名）
- 衍生指标声明（`roe = net_profit/total_equity 年化`），注册为 `DERIVED_FROM` 边

#### G.2 cache.py 改造

- 废弃 `financial_quarterly` 单一宽表
- 从 `financial/schemas.py` 反射建 8 张细表（DDL 从 schema 生成，不再手写）
- 新增 `get_visible_financials(table, symbols, as_of, fields)` — PIT 点查询
- 保留 `price_daily` 和 `universe_constituents`

#### G.3 miniqmt_provider.py 改造

- `_fetch_financials` 改为按表分别取数，返回 `dict[table_name, DataFrame]`
- 废弃 `_extract_income_fields`/`_extract_balance_fields`/`_extract_table_fields`，改为通用 `_extract_by_schema(schema, raw_df)`（从 schema 的 `xt_fields` 候选顺序提取）
- 废弃 `get_financial_panel`（职责移交给 Connector）
- `_quarterly_to_daily` PIT 逻辑搬到 `Connector._fetch_indicator_panel`
- `_compute_derived_financials` 搬到 Connector（衍生指标基于已 join 数据计算）

#### G.4 迁移脚本 — `ontology/migrations.py`

```python
def migrate_financial_quarterly(cache, graph) -> dict:
    # 1. 读 financial_quarterly 22 列
    # 2. 按字段归属拆到 income_stmt/balance_sheet/cashflow_stmt/pershareindex
    # 3. 写 4 张新表
    # 4. 注册财务指标本体节点 + 杜邦分解边
    # 5. 旧表重命名 financial_quarterly_v1_deprecated 保留（不删，遵守缓存保护约定）
```

Capital/Holdernum/Top10 无旧数据，需重下（`download_data.py` 改造后自动覆盖）。

---

### H. 记忆服务 + 事件推理改造

- `MemoryServiceImpl.activate_events` 改用 `graph.traverse(entity_sid, direction="reverse", relation_types={IMPACTS, PROPAGATES_TO})`，替代 O(N) `store.get_all()` 扫描
- `save_experience` 保存时自动建 `APPLIES_STRATEGY` + `RELATES_TO_CONCEPT` 边
- `search_experience` 改用图谱遍历（按因子族 + universe + 图路径），替代文本 TF-IDF
- `save_events` 的 `target_id` 改为 entity sid（先 upsert entity 物质），修复图谱断裂
- `event_inference/subgraph.py` 的 propagate 步骤：扩展 prompt schema 允许多跳 `propagates_to` 链；事件类型自动关联到敏感指标

### I. stock_service.py 收编

废弃 `get_financial_metrics` 的 Balance 单表直连 + 字段名错误（`operating_revenue`）：

```python
def get_financial_metrics(self, stock_code, start_year):
    result = self.connector.get_concept(ConceptQuery(
        subject=stock_code, aspect="盈利能力指标", time=f"{start_year}~latest"))
    return {"code": stock_code, "financial_metrics": result.data.to_dict(), ...}
```

---

## 与 ADR-007 substance 的关系

Substance 物质是本体节点的**子集**：
- substance sid 直接作为 OntologyNode sid
- 新增 `SubstanceForm.ENTITY`（公司/行业也建模为 substance，修复 `target_id` 字符串断裂）
- `GraphIndex` 升级为 `OntologyGraph`（旧 `substance/indices/graph.py` 降级为薄包装或删除）
- substance 的 `visible_from` PIT 机制直接复用到 OntologyEdge
- substance 的 DuckDB 持久化 + 原子追加 + WAL 崩溃安全模式不变

**本体论是 substance 的上层抽象**：substance 统一 event/relation/knowledge/strategy/backtest 五形态；本体论在此之上增加 domain 分类、关系类型约束、概念节点、跨域遍历能力。

## 实施顺序（5 阶段，每阶段可独立验证）

### 阶段 A：本体论基础（无副作用，纯模型）
1. `ontology/model.py` — OntologyNode/Edge/Domain/RelationType
2. `ontology/registry.py` — 注册表 + 校验
3. `ontology/graph.py` — OntologyGraph（类型过滤/反向/PIT）
4. `ontology/seed/` — 4 个本体种子数据
5. 测试：`test_ontology_graph.py`

### 阶段 B：财务数据层（本体论的第一批应用）
6. `backtest/data/financial/schemas.py` — 8 表 schema，注册到 OntologyRegistry
7. `ontology/migrations.py` — 旧宽表迁移 + 指标本体节点注册
8. `backtest/data/cache.py` 改造 — 8 张细表 CRUD + 自动迁移触发
9. `backtest/data/miniqmt_provider.py` 改造 — 按表分别取数
10. 测试：`test_financial_migration.py` + `test_financial_pit.py`

### 阶段 C：连接器（核心枢纽）
11. `ontology/concept_resolver.py` — 概念→字段/数据源/universe 翻译表
12. `ontology/connector.py` — get_concept 单一入口 + 各 _fetch_* 实现
13. `financial/connector_methods.py` — 财务概念实现
14. `services/stock_service.py` 收编
15. 测试：`test_connector.py`

### 阶段 D：记忆激活图驱动
16. `substance/store.py` 改造 — add 时按本体校验 + 自动建边 + entity sid
17. `substance/motion.py` 重写 — 图遍历优先
18. `services/memory_service.py` 改造 — activate_events/search_experience 用图谱
19. `event_inference/subgraph.py` 改造 — propagate 多跳 + entity sid
20. 测试：`test_motion_graph_activation.py` + `test_event_graph.py`

### 阶段 E：HTR 统一 + 六步循环接入
21. `strategy_rd/subgraph.py` 废弃
22. `strategy_rd/htr_subgraph.py` 改造 — 六步循环全部接入 connector + graph + Persona
23. 修复 HTR 死代码（`_decide_node:505-521`、`_dispatch_node`、`pruned_directions="无"`）
24. 财务字段清单从本体图谱渲染（消除 3 处硬编码不一致）
25. 测试：`test_htr_graph_integration.py`

### 阶段 F：质量门槛
26. Serena LSP 单文件零错（所有改动文件）
27. `uv run ruff check src/` 全局零错
28. `uv run lint-imports` 0 broken
29. `uv run pytest tests/unit/` 全绿

## 预期收益

### 策略研究成功率
- 财务字段从本体图谱渲染，消除 3 处硬编码不一致 → LLM 不再因字段缺失或口径混淆出错
- `_evaluate_branches` 硬编码阈值打分 → 图谱按"当前市场状态节点→合理 sharpe 区间"动态判断
- 事件→标的→敏感指标→策略调整链路打通 → 市场事件能真正影响策略研发
- 记忆/经验按因子族 + universe 图谱检索 → 跨策略经验复用
- HTR 六步循环全部接入图谱 → observe/ideate/decide 拿到结构化、可溯源的上下文

### 运行效率
- `activate_events` O(N) 扫描 → O(图遍历深度) 查询
- 同 universe 多假设共享面板缓存 → HTR executor 重复回测减少
- 连接器统一取数 → 消除 `stock_service` 绕路直连 + 字段名分裂
- 降级链真实生效 → miniqmt 不可用时自动 fallback

### 多数据源适配
- 上层只调 `connector.get_concept(ConceptQuery)` → 新增数据源只需在 `concept_resolver` 加映射 + 实现 provider
- 概念翻译表声明式 → 可由本体种子数据驱动，无需改业务代码

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 本体图谱种子数据工作量大 | 阶段 A 先建核心（财务指标杜邦分解 + 4 策略族 + 主要 universe），其余增量 |
| motion 图遍历可能引入噪声（边太多） | min_weight 阈值 + 域过滤 + 预算截断，保留关键词首轮作入图种子 |
| HTR 改造影响面大 | 阶段 E 在前 4 阶段验证后进行，每节点独立改造可回退 |
| 迁移脚本拆错列 | 字段归属从 schema 反查，迁移测试覆盖 22 列不丢不重 |
| 降级链真实生效后性能 | 连接器内部缓存（universe×date_range → panel 物化） |
| 删除旧 subgraph.py 破坏现有能力 | 阶段 E 先验证 HTR 全功能对齐旧流程后再删 |
| 多数据源字段命名差异 | 连接器内部统一映射到本体标准字段名，上层无感 |
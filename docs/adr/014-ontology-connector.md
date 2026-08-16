# ADR-014: 全局本体论 + 连接器 + 图驱动推理

日期: 2026-07
状态: Accepted（原文 HTR 六步循环接入部分由 ADR-018 调整为 ResearchAgent 消费连接器与图谱工具）

## 背景

缺乏统一本体论层导致数据、记忆、事件、策略经验各自为政，系统性瓶颈：财务字段清单多处硬编码不一致（研究 prompt 比开发 prompt 少 9 个字段）；记忆激活靠关键词递归漏召回（不走图边，事件->标的->经验链路断裂）、RELATION `target_id` 是股票代码字符串而非 substance sid（图遍历到 target 无法续跳）；事件->策略调整链路断点（HTR 对市场事件盲视）；数据层降级链死代码、字段命名三层不一致、无概念层（上层须自列 `roe`/`gross_margin` 等字段名，没有「盈利能力」概念的对应物）。核心目标：**提高策略研究成功率和运行效率**。

## 决策（四项核心）

| 决策点 | 选择 |
|--------|------|
| 本体覆盖范围 | 全局统一本体图谱（财务/公司/行业/事件/策略/经验/universe 跨域关联推理） |
| 连接器形态 | 单一概念查询入口 `Connector.get_concept(ConceptQuery)`，上层不碰字段名/数据源/符号格式/PIT |
| 记忆激活 | `motion.activate` 改图遍历优先，废弃关键词递归 |
| 子图统一 | 废弃旧线性 subgraph（ADR-018 后由 ResearchAgent 消费连接器工具） |

### A. 本体模型（`ontology/model.py`）

- `OntologyDomain`：entity / indicator / event / strategy / experience / concept。
- `OntologyNode`：`sid`（substance 物质 sid 直接复用；本体概念用领域前缀 `indicator:roe` / `entity:600519.SH` / `concept:profitability` / `concept:universe:csi300`）+ `aliases` + `properties`。
- `RelationType` **受约束枚举**（替代自由字符串，入边时校验）：impacts / propagates_to / belongs_to / member_of / reports_indicator / derived_from / same_dimension / applies_strategy / derived_from_experience / relates_to_concept / correlates_with。
- `OntologyEdge`：relation_type + weight + provenance（xtquant/wind/llm_inferred/manual）+ **visible_from（PIT：边何时可见）**。

### B. OntologyGraph（`ontology/graph.py`）

替代旧 GraphIndex：边是含类型与 PIT 的对象（非元组）；`traverse` 支持 `relation_types` / `domain_filter` 过滤（跨域查询「公司->行业->指标->影响该指标的事件」）、`direction="reverse"` 反向遍历（查「哪些事件影响了我」）、`visible_at` PIT 裁剪；另有 `resolve_concept` / `find_path`。

### C. 单一概念查询连接器（`ontology/connector.py`）

- `ConceptQuery`：`subject`（`600519.SH` / `csi300` / `贵州茅台`）+ `aspect`（概念字符串：`盈利能力` / `成分股` / `相关事件` / `动量族经验`）+ `time` / `as_of`（PIT 裁剪时刻）+ `constraints`。
- `ConceptResolver`（`ontology/concept_resolver.py`）声明式翻译表：概念 -> 字段集 / universe / 情报方法；实体名 -> symbol -> entity_sid。**新增数据源只需加映射 + 实现 provider，上层无感**。
- `ConceptResult`：data + provenance 数据源链 + `related_nodes`（结构化图谱关联注入 prompt，替代平铺文本）+ `paths` 溯源路径。

### D. 本体种子（`ontology/seed/`）

- 财务指标本体：杜邦三分解（`derived_from` 边）+ 盈利/成长/估值/质量族，衍生指标注册为边。
- 实体本体：公司/行业/板块/universe 节点，启动时从 universe 动态注册。
- 策略族 + 经验本体：动量/价值/质量/成长四族，经验保存自动建边。
- 事件类型本体：事件类型 -> 敏感指标映射（如宏观政策 -> debt_to_assets/capex），让「央行降息对高杠杆标的影响更大」可自动推理。

### E. 记忆/事件改造

`activate_events` 改 `graph.traverse(direction="reverse", relation_types={IMPACTS, PROPAGATES_TO})`（替代 O(N) `get_all()` 扫描）；`save_experience` 自动建 APPLIES_STRATEGY / RELATES_TO_CONCEPT 边；`save_events` 的 target_id 改 entity sid（先 upsert entity 物质）修复图谱断裂；事件 propagate 支持多跳 propagates_to 链。

### F. 财务数据层（本体论第一批应用）

- 8 张细表：Income/Balance/CashFlow/Pershareindex/Capital/Holdernum 六张标量宽表（主键 `(symbol, report_date)`）+ Top10holder/Top10flowholder 两张长表（主键含 rank）--多行结构塞不进扁平宽表的旧问题以此解决。字段映射单一事实源在 `financial/schemas.py`，DDL 从 schema 反射生成。
- `miniqmt_provider` 按表分别取数，通用 `_extract_by_schema`（按 schema 的 xt_fields 候选顺序提取）；`get_financial_panel` 与 `_quarterly_to_daily` PIT 逻辑移交 Connector；衍生指标计算随迁。旧 `financial_quarterly` 宽表迁移后保留不删（缓存保护约定）。

### 与 ADR-007 的关系

Substance 是本体节点的子集：sid 直接复用，PIT 机制（visible_from）复用到边，持久化模式（PG 事务式/幂等 UPSERT）不变；本体论在其上增加 domain 分类、关系类型约束、概念节点、跨域遍历。新增 `SubstanceForm.ENTITY` 修复 target 断裂。

## 后果

- 上层（ResearchAgent 工具 / stock_service / 残留 HTR 节点）统一经 `get_concept` 取数；财务字段清单从本体渲染，消除三处硬编码不一致。
- 图遍历可能引入噪声：min_weight 阈值 + 域过滤 + 预算截断，关键词首轮保留作入图种子。
- `stock_service.get_financial_metrics` 收编（消除绕过 Protocol 直连 client 与字段名分裂）。

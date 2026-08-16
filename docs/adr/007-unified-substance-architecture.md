# ADR-007: 物质-运动统一架构（Substance-Motion）

日期: 2026-06
状态: Accepted, Implemented

## 背景

ADR-004 的 numpy/pandas 三级记忆系统存在结构性缺陷（事件/关系二元分裂且关系无 provenance、单通道 TF-IDF 检索且特征选择写反、O(n²) 全量 refit、线性扫描、400MB 邻接矩阵预分配、边无元数据、冲突词库硬编码、pickle 不安全），维护成本高于迁移成本。决定整体替换并立即删除旧 `memory/` 模块。

## 决策

用「物质-运动」统一架构替换：事件 / 关系 / 知识 / 策略经验统一为 `Substance`（Pydantic，与 `BacktestResult`/`StrategyDSL` 技术栈一致）。哲学映射：物质=Substance（可持久化/可检索/有来源）；运动=motion 函数（activate/decay/conflict/compress，过程不持久化只产出新物质）；普遍联系=关系是一等物质（完整 provenance）；波粒二象性=同一物质有内容检索与图遍历两种视图。

### 核心模型

- **Substance**：`sid` / `form`（event / relation / knowledge / strategy / backtest）/ `content` / `keys`（WorldInfo 触发词）/ `filter_keys` + `filter_logic`（AND/NOT）/ `visible_from` / `expires_at`（PIT 时间窗）/ `source` / `confidence` / `conflict_group`（互斥组）/ `insertion_order` / `decay_half_life_days` / relation 专用 `source_id`/`target_id`/`relation_type`。
- **运动层**（`motion.py`）：`activate`（WorldInfo 激活：分词 -> 倒排索引查候选 -> filter 过滤 -> conflict 互斥 -> 递归 -> 时间过滤 -> 预算截断）、`decay`（按 form 配不同半衰期）、`detect_conflicts`（可配置词库）、`compress`。ADR-014 后 `activate` 改为图遍历优先。
- **双索引**：`RetrievalIndex`（关键词倒排 + TF-IDF/语义双通道融合，keyword 命中优先；增量 transform 不 refit；缓存按 content hash 失效）+ `GraphIndex`（dict 邻接表 + BFS 返回路径；ADR-014 升级为 `OntologyGraph`）。
- **时间过滤不设独立索引**：visible_from/expires_at 是查询后置谓词，<100K 物质下成本可忽略。

### MemoryService Protocol（破坏性收窄为 4 方法）

`search` / `save_experience` / `search_experience` / `initialize`。旧 8 方法中 `reflect`/`relate`/`remember`/`recall` 零外部调用或与 `search` 重复，`tier` 死参（MemGPT 三级模型残留）删除。`StrategyExperience` 值对象统一 save/search 数据契约，消灭 markdown 往返 regex。否决「拆 KnowledgeService + ExperienceService」：同一后端同一双索引，区别仅是过滤参数，不是领域边界。

### 持久化

Phase 1-3 的 JSONL 截断式全量重写（不原子、O(n²) 写放大）暴露工程缺陷后，先升级 DuckDB（`INSERT OR REPLACE` 原子追加 + WAL + 主键幂等 + meta 从 `COUNT(*)` 派生），ADR-019 统一迁移至 **PostgreSQL `substances` 表**（JSONB keys/metadata，`save_many` 幂等 UPSERT）。TF-IDF / Graph 索引不持久化，启动时全量加载到内存热存储。

写入路径收敛：所有生成数据落盘位置由 `core/storage.py` 唯一裁决，唯一控制变量 `LONG_EARN_DATA_DIR`（默认 `D:/dev/long-earn-data`），各业务模块不得 `Path.home()` 或硬编码。

### 事件采集与推理（Phase 2-3）

Collector registry（Kimi / ciccwm 热榜 / 专题资讯）+ 事件推理子图（collect -> extract -> propagate -> conflict -> save）；`save_events` 落 EVENT/RELATION 物质 + 冲突组检测。ADR-018 将入口基础设施化为 `RuntimeContext.prepare_context(query)`。

## 附录：PIT 数据修复（原独立 ADR-007 分支并入）

1. **announce_date 必填、无回退**：`_quarterly_to_daily` 唯一逻辑 `visible_from = announce_date`。原 `report_date + 60 天固定延迟` 对年报（法定披露截止 120 天）造成约 40 个交易日未来函数泄漏。缓存表 DROP + CREATE 重建。
2. **财务接口统一到 miniqmt**：akshare / ciccwm 的财务方法全部删除；ciccwm 保留情报接口（`MarketIntelligenceProvider`）。
3. **四表合并全量字段**：`FINANCIAL_FIELD_MAP` 7 -> 18 字段（Income / Balance / CashFlow / Pershareindex 四表按 `(symbol, report_date)` 对齐），衍生指标优先用 Pershareindex 预计算值、手算兜底。

## 后果

- 删除旧 `memory/`（5 文件 ~1500 行）与旧测试；新增 jieba 依赖；import-linter 新增 substance 独立合约。
- MemoryService 8 -> 4 方法，5 个消费点机械迁移；ADR-004 Superseded。

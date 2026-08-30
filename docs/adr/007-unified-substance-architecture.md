---
id: 7
title: 物质-运动统一架构
status: Accepted
date: 2026-06
summary: Substance 统一建模事件、关系、知识与策略经验；双索引检索与 PostgreSQL 持久化。
---

# ADR-007: 物质-运动统一架构


## 背景

ADR-004 所采用的三级记忆系统（基于 numpy/pandas）存在结构性缺陷：事件与关系二元分裂且关系缺乏溯源信息；检索依赖单通道 TF-IDF，特征选择逻辑存在错误；全量 refit 导致 O(n²) 复杂度；采用线性扫描与约 400MB 邻接矩阵预分配；边元数据缺失；冲突词库硬编码；pickle 序列化存在安全风险。维护成本已高于迁移成本。本决策以整体替换为目标，并删除旧 `memory/` 模块。

## 决策

我们将采用「物质-运动」统一架构，将事件、关系、知识与策略经验统一建模为 `Substance`（Pydantic 模型，与 `BacktestResult`、`StrategyDSL` 技术栈一致）。

**哲学映射**：物质对应 `Substance`（可持久化、可检索、有来源）；运动对应 motion 函数（activate、decay、conflict、compress，过程不持久化，仅产出新物质）；普遍联系对应关系作为一等实体（具备完整 provenance）；波粒二象性对应同一物质同时支持内容检索与图遍历两种视图。

### 核心模型

- **Substance**：`sid`、`form`（event / relation / knowledge / strategy / backtest）、`content`、`keys`（WorldInfo 触发词）、`filter_keys` 与 `filter_logic`（AND/NOT）、`visible_from` 与 `expires_at`（PIT 时间窗）、`source`、`confidence`、`conflict_group`（互斥组）、`insertion_order`、`decay_half_life_days`；关系类型另含 `source_id`、`target_id`、`relation_type`。
- **运动层**（`motion.py`）：`activate`（WorldInfo 激活：分词 → 倒排索引查候选 → filter 过滤 → conflict 互斥 → 递归 → 时间过滤 → 预算截断）、`decay`（按 form 配置不同半衰期）、`detect_conflicts`（可配置词库）、`compress`。ADR-014 之后 `activate` 改为图遍历优先。
- **双索引**：`RetrievalIndex`（关键词倒排与 TF-IDF/语义双通道融合，keyword 命中优先；增量 transform 不 refit；缓存按 content hash 失效）与 `GraphIndex`（dict 邻接表与 BFS 路径返回；ADR-014 升级为 `OntologyGraph`）。
- **时间过滤不设独立索引**：`visible_from`/`expires_at` 作为查询后置谓词；在物质规模低于 10 万条时，成本可忽略。

### MemoryService Protocol（收窄为 4 方法）

接口保留 `search`、`save_experience`、`search_experience`、`initialize`。旧 8 方法中 `reflect`、`relate`、`remember`、`recall` 无外部调用或与 `search` 重复；`tier` 参数（MemGPT 三级模型残留）删除。`StrategyExperience` 值对象统一 save/search 数据契约，消除 markdown 往返正则解析。否决「拆分为 KnowledgeService + ExperienceService」：同一后端、同一双索引，区别仅为过滤参数，不构成独立领域边界。

### 持久化

JSONL 截断式全量重写（非原子、O(n²) 写放大）暴露工程缺陷后，先升级至 DuckDB（`INSERT OR REPLACE` 原子追加、WAL、主键幂等、meta 从 `COUNT(*)` 派生）；ADR-019 统一迁移至 PostgreSQL `substances` 表（JSONB keys/metadata，`save_many` 幂等 UPSERT）。TF-IDF 与 Graph 索引不持久化，启动时全量加载至内存。

写入路径收敛：所有生成数据落盘位置由 `core/storage.py` 唯一裁决，唯一控制变量为 `LONG_EARN_DATA_DIR`（默认 `D:/dev/long-earn-data`）；各业务模块不得使用 `Path.home()` 或硬编码路径。

### 事件采集与推理

Collector registry（Kimi / ciccwm 热榜 / 专题资讯）与事件推理子图（collect → extract → propagate → conflict → save）；`save_events` 写入 EVENT/RELATION 物质并执行冲突组检测。ADR-018 将入口实现为 `RuntimeContext.prepare_context(query)`。

## 后果

**正面**

- 统一事件、关系、知识与策略经验的数据模型，消除 ADR-004 二元分裂与检索缺陷。
- MemoryService 接口收窄至 4 方法，消费方迁移路径明确；`StrategyExperience` 值对象消除 markdown 往返解析。
- PostgreSQL 持久化提供原子 UPSERT 与事务语义；写入路径由 `core/storage.py` 统一裁决。

**负面**

- 删除旧 `memory/` 模块（约 5 文件、1500 行）及对应测试；新增 jieba 依赖。
- MemoryService 8 方法收窄为 4 方法，5 个消费点须机械迁移。
- import-linter 新增 substance 独立合约，架构约束增加。

**中性**

- ADR-004 标记为 Superseded。
- TF-IDF 与 Graph 索引每次启动全量加载，大规模物质集下内存占用须监控。
- 具体实现细节以源码为准。

## 关联

- Supersedes: ADR-004
- 修订: ADR-014（`activate` 图遍历优先）、ADR-018（`prepare_context` 入口）、ADR-019（PostgreSQL 迁移）

## 附录：PIT 数据修复

1. **announce_date 必填、无回退**：`_quarterly_to_daily` 唯一逻辑为 `visible_from = announce_date`。原 `report_date + 60 天固定延迟` 对年报（法定披露截止 120 天）造成约 40 个交易日未来函数泄漏；缓存表经 DROP + CREATE 重建。
2. **财务接口统一到 miniqmt**：akshare / ciccwm 的财务方法全部删除；ciccwm 保留情报接口（`MarketIntelligenceProvider`）。
3. **四表合并全量字段**：`FINANCIAL_FIELD_MAP` 由 7 扩展至 18 字段（Income / Balance / CashFlow / Pershareindex 四表按 `(symbol, report_date)` 时点对齐）；衍生指标优先使用 Pershareindex 预计算值，手工计算作为备选。

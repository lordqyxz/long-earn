# 关键实现约束（Gotchas）

> 从根目录 [AGENTS.md](../AGENTS.md) 拆出。记录易踩坑、反直觉或必须牢记的实现事实；字段级细节以源码为准。  
> 项目规范与质量门槛见 AGENTS.md；架构决策见 [adr/AGENTS.md](adr/AGENTS.md)。

---

## 1. 回测引擎

- **回测引擎内嵌**：回测引擎已整合到主项目（`src/long_earn/backtest/`），无需启动外部 HTTP 服务。策略通过 YAML DSL 描述，引擎直接调用。
- **仅支持多头、不支持做空**：`Portfolio` 仅维护 `cash` + 多头 `positions`，无 `short_positions`；DSL `weights` 仅支持 `equal`。弱市下唯一可用风控是「空仓 + 止损 + 最大回撤清仓」，无法对冲/做空/动态降仓。
- **表达式安全（已退役）**：ADR-003 的 AST 白名单求值器已删除（Superseded by ADR-009）。所有策略走算子目录路径（[ADR-009](adr/009-operator-catalog-and-operator-dev-subgraph.md)），以 `prove_causality` 与算子目录白名单共同保证无未来函数。DSL 解析期强制拒绝旧式 `factors` 字段与 `filter`/`rank`/`expression` 信号类型。
- **算子更名须连带迁移 YAML（硬性约束）**：算子注册名（`Operator.name`）是策略 YAML `op` 字段的契约 ID。发现名实不符时，**必须改名并同步全部策略 YAML / 模板 / 测试引用**，不得「只改正文保留误导 ID」。旧名登记于 `OPERATOR_RENAMES`，`get_operator(旧名)` 抛明确迁移错误（不静默别名）。例：`roe_quality` → `return_quality`，`gross_margin_stability` → `price_stability`。
- **回测记录标签机制（硬性约束）**：审计记录（`backtest_audit.logs`）支持 run 级 `tags`（RUN_START payload.tags，常量 `RUN_TAG_TEST = "test"`）。**测试/冒烟回测写共享 PG 时必须携带标签 `test`**：引擎传 `tags=["test"]`，或 `AuditLogger.log_run_start({"tags": ["test"]})`。清理接口以「带 test 标签」识别测试污染，并保留结构性无效口径（无 FILL / RUN_ERROR / 无 RUN_END / 成交笔数 < 5）。生产回测不传 tags。

## 2. 数据层

- **数据库引擎层**：PG 连接与事务统一走 `core/db.py`（SQLAlchemy 2.0 Core，`postgresql+psycopg`；连接参数由 `core/pg.py` 裁决）。读路径 `read_connection()`，写路径 `write_transaction()`；COPY 经 `raw_psycopg_connection()`。**DataCache 已迁移**；审计/记忆库/分析器仍走 psycopg 直连（第二阶段，见 TODO）。简单表用 Core `Table`；批量分析型负载保持驱动级原生 SQL，不用 ORM。DDL「构造即建表」，不引入 alembic。
- **数据缓存**：PostgreSQL `long_earn` 库；全量下载经 `scripts/download_data.py`（miniqmt）。regime 基准四指数纳入 `DataIngestionService.INDEX_QUOTES`。面板路径：PostgreSQL Cache + 显式主源 miniqmt，失败即失败（ADR-018）。ciccwm 为情报独占；akshare 仅显式点名。不得手动 DELETE/DROP 缓存权威表。
- **三组接口**：`DataConnector` / `MarketIntelligenceProvider` / `RealtimeDataProvider` 分离，不混用。签名以 `services/__init__.py` 与 `backtest/data/connector.py` 为准。
- **并发下载**：`DataIngestionService --max-workers`（1–8，默认 4）；子进程隔离防 xtquant SIGABRT；主进程串行写 PG。
- **增量同步判定（硬性约束）**：须区分**数据状态**（日更域）、**检查水位**（稀疏事件域）、**存在性**（一次性内容）。**禁止**仅用 `today - max(announce_date) > 阈值` 判财务 stale。判定需检查的 symbol 在批次成功后**必须推进水位**（含合法 0 行）。同款逻辑须共享同一水位（启动同步与 `ensure_financial_cache`）。

## 3. 记忆系统

- 物质-运动统一架构（ADR-007）；`substances` 表 PG 持久化；旧 `memory/` 已删。
- `prepare_context` 为纯确定性激活（ADR-021），返回 `ContextActivation`；miss 时由 agent 层显式触发事件推理。`Connector` 注入 `memory_provider`。

## 4. 集成测试

- 运行 `tests/integration/` 前需配置环境变量（见根 AGENTS.md「环境变量」与 `AppConfig` docstring）。

## 5. Agent 分层（ADR-021）

- LLM 推理只存在于 agent 节点层；脚手架层只产确定性结构化中间态。
- 确定性先行：规则可判定的须代码先行；LLM 仅作未命中时的回退，且回退点须在 agent 层。
- 静态合规检查：`scripts/check_llm_call_sites.py`（已入 CI）；白名单扩容须登记架构理由。

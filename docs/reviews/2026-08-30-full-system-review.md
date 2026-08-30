# 全系统代码评审记录（2026-08-30）

> **评审对象**：main `4aa2034`（评审窗口内另落入 `27a690a` / `46382d8`，见文末附记）+ 工作区未提交改动。
> **评审方式**：OpenCodeReview delegate 模式 v1.11.0——OCR 负责 `delegate preview`（range 根提交→HEAD，枚举 394 个可评审文件）与 `delegate rule`（规则解析），实际评审由宿主智能体编排 12 个并行只读评审单元逐文件通读（后端 10 组：183 个 src 文件 + 21 个脚本 + 109 个测试 + 工程配置；前端 2 组：web/src 全部手写代码，生成代码只查漂移）。
> **复核口径**：全部 Critical 与 3 项关键 High 经主评审人源码级二次确认；两项发现（Py3.13 工作线程 `get_event_loop`、openapi 契约漂移）经项目 venv 实证复现。
> **修复处置**：见 [2026-08-30-remediation.md](2026-08-30-remediation.md)。

**统计**：后端 Critical 6 | High 21 | Medium 56 | Low 63（约 146 项）；前端 Critical 0 | High 3 | Medium 8 | Low 14。

**总体结论**：核心链路质量高——引擎未来函数防御体系化（VisibilityGuard 时间轴截断 + T+1 撮合 + 算子因果证明 + DSL 白名单）、OOS 合并门在数据分割意义上真正 held-out、SQL 全面参数化、loguru/类型注解纪律良好。Critical 集中在旧脚本违反数据分割铁律、operator_dev 沙箱逃逸、算子参数缺口未来函数、合并门证据污染。

---

## 后端

### Critical（6 项，均已亲验）

**C1｜数据分割铁律：旧代脚本硬编码 2026 窗口，横跨 TEST/VALIDATION 段**

- `scripts/backtest_recent.py:53-56`——`RECENT 2026-01-06~2026-07-08` 同时覆盖测试段与验证段（开发阶段绝对禁止）；`TRAIN_END=2026-01-05` 使"训练集对照"实际含一年测试段数据。
- `scripts/find_best_strategy.py:121-128, 403-408`——"双季度前瞻验证"硬编码 2026 Q1/Q2（Q2 横跨验证段），且每轮研发循环自动执行；注释自称"铁律 #3"但行为相反，验证结果写入 `strategy_research_results.json` 构成调优反馈通道。
- `scripts/prove_backtest.py:35-36, 57-61`——对账脚本在验证段区间真实重跑回测。
- `scripts/validate_dual_quarter.py:44-47`——同款硬编码双季度窗口，与 config 三段边界不对齐。

**C2｜`scripts/backtest_recent.py:137-138` 无条件用退役 DSL 覆写合并门基准**

写入发生在任何成败分支之外；内嵌 `STRATEGY_YAML` 使用旧式 `factors:`/`type: filter` 语法（`engine/dsl.py:250-255` 解析期强制拒绝）。脚本一跑即把 `best_strategy_path()`（`compare_strategies`/`oos_validate*` 的合并门基准）覆写为解析必然失败的 YAML，破坏整条合并门链路。

**C3｜`src/long_earn/operator_dev/sandbox.py:184-189` 沙箱可一行逃逸**

exec 未显式提供受限 `__builtins__`，CPython 注入完整 builtins；AST 审计只拦名称/属性节点，下标访问不可见：`__builtins__["__import__"]("os").system(...)` 可通过 `audit_source` 后在主进程任意执行。源码来自 LLM 输出（prompt 混入策略 YAML/假设/反思文本），非可信常量；`polars.DataFrame.write_parquet` 亦为白名单内任意文件写向量。

**C4｜`src/long_earn/backtest/operators/factor/log_return.py:13,29` 负 period 即未来函数（实测复现）**

`LogReturnParams.period` 无 `gt=0` 约束；`period=-1` 通过 pydantic 校验后 `shift(-1)` 使 t 行使用 t+1 数据。注册因果证明边界参数仅 `(1, 29)`，引擎执行路径只跑 pydantic 校验——全链路无拦截。对照 `returns.py:34`/`shift.py:35` 均有显式拦截。

**C5｜`scripts/oos_validate.py:272-275` + `scripts/compare_strategies.py:281-284` OOS 合并门对照被基准风控参数污染**

两脚本从 `yamls[0]`（基准策略）提取 `stop_loss/max_position_per_stock` 注入全部回测任务；引擎风控完全来自任务参数（`parallel.py:158-162`），候选自带 `risk_control` 被静默覆盖。`compare_strategies` 的 A/B 风控对照失效；`oos_validate` 的 MERGE/CONTINUE 决策基于错误风控下的候选 OOS 夏普。

**C6｜`src/long_earn/backtest/data/financial/sync.py` 回测读路径财务增量判定违反水位铁律（AGENTS 6.2 点名）**

评审时形态：`is_financial_stale` 用 `today - max(announce_date) > 120 天` 判 stale——沉默股票「判 stale → 下载 0 行 → 状态不推进 → 再判 stale」死循环（实测 4620 只 × ~20 分钟）；`len(latest_map) < len(symbols) → True` 使 ETF/指数永久判 stale；读路径未共享 `financial_sync_watermark`。
**评审期间已被并行修复**（`46382d8` + 工作区延续改动，后作为 `4aa2034` 提交）：读路径改为双水位判定，与启动同步共享水位与 `FINANCIAL_RECHECK_DAYS=7` 常量。遗留观察点：① 启动同步未运行时读路径仍会重复拉取沉默股票（docstring 已声明为设计权衡）；② `ensure_financial_cache` docstring 仍写「DuckDB」。

### High（21 项）

**回测引擎**

| # | 位置 | 问题 |
|---|------|------|
| H1 | `engine/parallel.py:497` | 并行 walk-forward 用含 warmup 的全量时间轴切 fold，fold 边界整体前移 warmup 期，与单进程版（`core.py:1815-1817` 有 start/end 过滤）系统性不可比（edff513 同族） |
| H2 | `engine/portfolio.py:436-454` | SELL 成交无持仓上限校验，卖出超持仓凭空增资（隐性做空），违反仅多头约束；可达路径：direct_orders 无持仓校验 + rebalancing 与挂单触发竞态 |
| H3 | `engine/core.py:1714-1721` | benchmark 对齐用含 warmup 的全量时间戳索引交易期 equity_curve，warmup_days>0 时 alpha/beta/IR/tracking_error 全部失真 |

**数据层**

| # | 位置 | 问题 |
|---|------|------|
| H4 | `data/miniqmt_provider.py:130-138` | `_run_with_timeout` 超时保护失效：TimeoutError 抛出前 `with ThreadPoolExecutor` 的 `shutdown(wait=True)` 仍阻塞等挂死的 xtdata 调用（:429 记录的 QMT 半连接永久阻塞正是该机制要防的场景） |
| H5 | `data/akshare_provider.py:187` | 当前成分股按请求的历史 PIT 日期落盘，重新引入 miniqmt 侧 P1-01 已修复的虚假 PIT 历史（幸存者偏差污染共享 `universe_constituents`） |

**API 应用层**

| # | 位置 | 问题 |
|---|------|------|
| H6 | `app/app.py:283-305` | 导出端点 `finally: shutil.rmtree(tmp_dir)` 在 `FileResponse` 实际发送前执行（starlette 发送时才打开文件，已核 1.0.0 源码），端点必然 500；`mkdtemp` 抛错时 finally 引用未绑定变量 |
| H7 | `app/app.py:710-714` | 同步 LLM 推理管线（事件子图 invoke + prepare_context + ea.load）直接在事件循环内执行，分钟级冻结全部 REST/WS；同文件 research 路径已正确用线程池 |
| H8 | `app/app.py:816,886` | 工作线程内 `asyncio.get_event_loop()` 在 Py3.13 必抛 RuntimeError 且被 `except: pass` 吞（venv 3.13.14 实证）——research 进度广播 WS/REST 双路径均为死功能 |
| H9 | `app/app.py:136-156` + `app/analyzer.py:236-240` | `DELETE /api/runs/clean` 孤儿口径无时间护栏，正在运行的回测审计行被当场删除（破坏 ADR-005 可追溯链）；`delete_run` 可删生产 run 无确认；clean 响应按 len 虚报成功数 |

**股票分析 / 记忆事件**

| # | 位置 | 问题 |
|---|------|------|
| H10 | `stock_analysis/state.py:4-19` + `subgraph.py:290,312` | `StockAnalysisState` 未声明 `fund_flow_analysis`，LangGraph 对未声明 channel 仅告警后丢弃（核对安装版 `_algo.py:286`）——第五视角 LLM 成本照付、结果全量丢弃 |
| H11 | `stock_analysis/subgraph.py:195-242` | 五视角并行节点无异常隔离，任一 LLM 瞬时失败终止整个子图，其余已成功视角全部丢弃 |
| H12 | `event_inference/collectors/kimi_collector.py:57` | Kimi 外部 API 无超时（openai SDK 默认 600s × 重试 × 2 次串行），同步阻塞 collect 节点可挂数十分钟 |
| H13 | `substance/motion.py:409` | compress 合并只改内存不落盘，而 remove 立即物理删除 PG 行——持久化绑定下被合并内容永久丢失（半持久化不对称） |

**算子 / 引擎契约**

| # | 位置 | 问题 |
|---|------|------|
| H14 | `engine/dsl.py:302` | warmup 键扫描漏掉 compose 算子的 `low_vol_lookback/momentum_lookback/momentum_window/quality_window/min_obs`——使用这三个算子的策略 warmup=0，前 20-60 bar 因子 null/0 参与交易；ADR-013 T6 同族复发；`causality.py:78-95` 清单已含这些名字，两处不一致 |

**脚本 / 测试**

| # | 位置 | 问题 |
|---|------|------|
| H15 | `scripts/validate_dual_quarter.py:121`、`scripts/prove_from_audit.py:349`、`scripts/prove_backtest.py:34` | 输出/读取路径硬编码绝对路径或 CWD 相对路径，违反 `core/storage.py` 统一裁决；`best_strategy.yaml` 存在 repo 根遗留副本与数据目录权威副本双源漂移 |
| H16 | `tests/unit/test_backtest/test_engine.py:893-913`、`test_audit_flow.py:587-628` | 断言弱于测试名声称的风控不变量（止损保守成交价测试从不检查 FILL 价格；max_position_pct 测试只断言 success）——假信心 |
| H17 | `tests/unit/test_strategy_rd/test_research_agent.py:374-386` | 恒真断言：测试自赋空值后立即断言为空，生产清空逻辑从未被调用 |

### Medium（56 项，按主题归组）

- **引擎/撮合真实性（8）**：止盈用日内 high 判定并以 high 成交（与止损保守口径自相矛盾，`core.py:798,860`）；风控单绕过 `_pre_trade_check`（`core.py:860,982,1106`）；挂单 STOP 成交绕过成交量参与率限制（`broker.py:269`）；部分成交无整手取整产生碎股（`broker.py:302-305`）；现金不足静默跳过买入但仍记 FILL SUCCESS 审计（`core.py:1348-1369`）；direct_orders 不填 timestamp 致成交 ID 格式化 None 抛 TypeError（`strategy.py:75` + `core.py:1373+`）；`len(timestamps) <= n_splits` 时 `train_ts[0]` IndexError（`core.py:1830`/`parallel.py:507`）；telemetry/parallel 的 `# type: ignore`。
- **数据层（2）**：realtime 订阅丢弃真实句柄、unsubscribe 传列表必然失败且异常被吞（`realtime.py:128-146`）；ciccwm 全局关闭 TLS 证书校验且 apiKey 走该连接（`ciccwm_client.py:179-181`）。
- **服务/核心（7）**：`core/pg.py:81` conninfo 拼接不转义（密码含空格/`=` 解析错误）；`data_ingestion_service.py:632` run() docstring 仍描述已废弃的 120 天判 stale 规则；`backtest_service.py:156,164` 兜底路径每次新建 provider/DataCache 且不关（双轨实现）；`config.py:227` 字段默认值 import 时求值带 mkdir 副作用；`stock_service.py:209` legacy 路径 `operating_revenue` 取自 Balance 表恒 0.0；`master_agent.py:72` ReAct 循环使用 `get_llm()` 长寿单例（自述 GIL Fatal 风险）；`backtest_service.py:344-404` run_grid 无训练集边界校验（防御纵深缺口）。
- **策略研发（5）**：`acceptance.py:47-94` `primary_metric` 参数从未使用、硬编码 sharpe；`research_service.py:132` 原地改写共享 AppConfig 传参；`htr_subgraph.py:1879` `"volume" in "成交量"` 恒 False，成交量算子 spec 丢 volume 输入；`htr_subgraph.py:1114-1168` 确定性失败原因 insight 被 LLM insight 覆盖（ADR-015 A1 反馈环退化）+ backpropagate 循环内重复全量调用；`research_agent.py:870` 无意义重试死代码 + type ignore。
- **记忆/本体/事件（7）**：`store.py:305` remove 内存先删、PG 失败不同步且无日志；`store.py:183` search 衰减条件颠倒 + 浮点 `!=`（物质自身半衰期形同虚设）；`motion.py:276` 按 content 全表反查 O(N×k) 且内容重复错配；`subgraph.py:118` conflict_group 含运行内计数器，跨运行互斥失效；collectors `# type: ignore`；跨对象私有成员访问（`retrieval._semantic._doc_matrix` 等）；`connector.py:584` `_parse_quarter` 违背自身契约抛 ValueError。
- **分析/工具/监控（6）**：`tools/backtest_analyzer.py` 整模块死代码（293 行与 app/analyzer.py 同名双轨）；`tools/store.py:15` 模块级实例化 LoggerServiceImpl，import 即重置全局日志；`utils/llm_factory.py:62-66` openai 分支死守卫 + 默认 URL 串扰；`llm_factory.py:33,54` type ignore；`subgraph.py:121-144` price_history 失败静默（吞异常返回 []，重试包装永不生效）；`realtime_alert.py:108` start() 后新增告警永不生效 + `_alerts` 跨线程无锁。
- **API 应用层（6）**：analyzer 三处 DB 故障静默返回空列表无日志；delete 失败误报 404；remote 模式 Origin 中间件可被无 Origin 客户端绕过且无鉴权强制；本地模式 WS 不校验 Origin（CSWSH 面，可触发昂贵 LLM 管线）；app 层绕过 services 直连 DataCache 私有成员跑裸 SQL（六处）；`web/openapi.json` 落后后端 schema 12 个组件。
- **算子目录（6）**：quality_momentum 把"无数据"当"最优质量"（null→0→满分）；causality 注册证明不覆盖基本面分支且声明强于实际；实现指纹只哈希 `apply.__code__`；roe_quality/gross_margin_stability 名实不符（价格因子冒名基本面因子）；热注册 file-exists 静默跳过造成盘/进程实现漂移；DataFrame 多列输出忽略 alias，两个 macd 步骤静默互相覆盖。
- **脚本/工程配置（5）**：`find_best_strategy.py:281` --auto-window 把 test 段设为 train 区间（合并门退化为样本内自评）；`prove_from_audit.py:98` run 选择与硬编码报告值张冠李戴；`check_deprecated_syntax.py:99` grep 卡口只覆盖单目录且未接入 CI；`langgraph.json` 指向不存在的 `src/long_earn/agent.py`；validate_dual_quarter 窗口硬编码。
- **测试套件（3）**：7 个文件集成性质却放 unit 目录，PG 不可达时整组静默跳过；单测直连共享生产 PG 且清理不可靠（`_schema_meta` 全局表 UPDATE 不恢复、substances 清理不在 finally、断言先于 try/finally）；`test_audit_flow.py:47` 依赖共享库全局最新写入。

### Low（63 项，摘要）

- **DuckDB 文档漂移（约 15 处，ADR-019 迁移后残留）**：services/core/data 层 docstring、scripts 5 处、`provider.py` 还错误描述为"静默降级"（ADR-018 废除的行为）。
- **`# type: ignore` 违反仓库铁律（7 处）**：telemetry/parallel/llm_factory/research_agent/operator_dev subgraph/collectors。
- **死代码**：`miniqmt_provider.py:917-995`（4 个 `_extract_*` 约 80 行）、`operators/_util.py:50` cross_section、`e2e_volatility` 与 realized_vol 公式重复、`overfit_gates.py:151` 死常量、`broker.py:220` expired_ids 恒空、`app/event_analyzer.py:89` 死条件、`context_init.py:117` 死赋值、pyproject duckdb 死依赖 + dev 依赖双轨等。
- **其余**：吞异常无日志、私有成员跨层访问、`evaluate_recent` 术语"验证窗口"与铁律术语冲突、tree_store 非原子落盘、`decay_half_life_days` 无 gt=0、`chunk_overlap >= chunk_size` 死循环、闰日 ValueError、personas/analyst EXAMPLES 双轨维护、md_splitter h1 丢段、CI grep 卡口可绕过等。

### 已验证无问题的要点（正面清单）

- **未来函数主链路**：T+1 pending 队列、VisibilityGuard 截断、算子因果证明（19 算子 × 4 扰动 + 负向必检 + DSL 级权益曲线不变性 + 预计算等价测试）、`run()` 重置瞬态状态防跨 fold 泄漏——均验证在位。
- **OOS held-out**：`run_oos` 缺省锁定 config 测试段 + `_validate_oos_window` 硬校验；训练/寻优/逃生口全部钉死训练集；验证集在 src 全量中零触碰（违规仅限 scripts）。
- **SQL**：全仓参数化 + `sql.Identifier`，无注入面；`run_custom_query` 经只读连接兜底。
- **import-linter 方向 / loguru / 缓存保护（无越权 DELETE/DROP）/ test 标签契约**：全量合规。

---

## 前端（web/，React 18 + Vite + TS）

### High（3 项，2 个独立根因）

| # | 位置 | 问题 |
|---|------|------|
| FH1 | `web/src/hooks/useWebSocket.ts:60-64` | 卸载后无限重连泄漏：onclose 无条件排定重连、timer 未存 ref 无法取消、cleanup 的 close 触发 onclose 再排新定时器；StrictMode 双挂载放大 |
| FH2 | `web/src/hooks/useResearchWebSocket.ts:49-52,59-65` | 同款根因：disconnect 先 clearTimeout 再 close，close 触发的 onclose 其后异步执行又重排 3s 重连，wsRef 已 null 时 OPEN 守卫失效新建幽灵连接（确定性触发） |
| FH3 | `web/src/pages/EventFlowPage.tsx:29-32` | 固定 4 秒 setTimeout 冒充管线完成信号：管线超 4s 按钮提前解禁且 reload 拉到中间态，失败白等 4s；定时器卸载不清理 |

### Medium（8 项）

`useWebSocket.ts:31` JSON.parse 无 try/catch（与 research hook 不一致）；`useResearchWebSocket.ts:4` WS URL 硬编码 ws://（https 托管被阻断）；`useRuns.ts:55-92` + `SymbolDetailDialog.tsx:84-108` 切换 run/symbol 无 abort/stale 守卫（慢响应覆盖新数据）；`BacktestDetail.tsx:836-837` map 返回无 key 匿名 Fragment；`ResearchRounds.tsx:93-114` document.write 渲染新窗口（OCR 规则明令禁止）；`SymbolDetailDialog.tsx:95,104` 松散响应双重断言为全必填接口（字段缺失 hover 崩溃）；`useWebSocket.ts:76-82` 未查 readyState 即 send；`useRuns.ts:107-109` useEventData 四请求错误全吞不暴露 error 态。

### Low（14 项，摘要）

logEndRef 死意图 + index key；交易窗口魔法数字 ±90/30 天；图表硬编码亮色主题 hex 与 dark: token 双轨；`-999` 哨兵两处硬编码；可点击 div 无键盘可达性、手搓遮罩无 Esc；阻塞式 alert() 反馈；done 态连接线灰色不一致；固定间隔重连无退避；lib/utils 死导出与 formatDate 无 Invalid Date 守卫等。

### 已验证无问题的要点

生成代码（16 个 api 文件 + shadcn）零手改；baseUrl 空串相对路径无 localhost 进构建产物；手写类型与生成类型无双定义漂移；package.json 无 `latest`/`*` 与依赖重复；全范围无 innerHTML/eval。

---

## 附记：评审期间仓库的并行变更

评审执行窗口（约 02:44-03:02）内仓库落入两个提交，均为用户并行开发所致：

| 时间 | 提交 | 与评审的关系 |
|------|------|--------------|
| 02:49 | `27a690a` feat(adr021): 确定性脚手架与 LLM 推理分层 | 会话开始时待提交的 5 个文件入库；评审读的即该内容，发现全部有效 |
| 02:56 | `46382d8` fix(data): 财务同步水位表治理沉默股票重查死循环 | 即 C6 的修复（后续 `4aa2034` 补齐读路径）；C6 状态已相应更新 |

C6 之外其余发现不涉及这两个提交的文件内容，结论按评审时点成立。

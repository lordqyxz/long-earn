# 全系统评审修复记录（2026-08-30）

> **对应评审**：[2026-08-30-full-system-review.md](2026-08-30-full-system-review.md)（评审对象 main `4aa2034`）。
> **修复载体**：分支 `fix/review-critical-high`（worktree `D:/dev/long-earn-fix`，基于 `4aa2034`），43 个文件 +1406/−661，未直接在主工作区改动（主区另有 cache.py SQLAlchemy 迁移 WIP 并行进行）。
> **执行方式**：6 个并行修复单元（脚本合规 / 数据层 / 应用层 / 分析与记忆 / 测试 / 前端）+ 主评审人自修安全与引擎关键项；全部改动经交叉审查后统一验证。

**验证结果**（worktree 实测）：`uv run ruff check .` 全绿 · `uv run lint-imports` 9 contracts kept · `uv run pyright src/` 0 errors · `uv run pytest tests/unit/` **1132 passed / 0 failed** · 前端 `npx tsc --noEmit` 零错误 · `check_deprecated_syntax` / `check_llm_call_sites` 卡口通过。

---

## 一、逐条处置映射

### 后端 Critical（6/6 关闭）

| ID | 处置 | 说明 |
|----|------|------|
| C1 | ✅ 已修 | 4 个脚本窗口全部改 `config.train/test/validation_*` 派生；`find_best_strategy` 双季度前瞻验证移出研发循环，仅显式 `--final-validation` 旗标触发（默认关，执行前打印「消耗验证集唯一一次触碰」警告），Q1/Q2 从 `config.validation_*` 对半拆分派生；`backtest_recent`/`prove_backtest` 的 "recent" 改为训练集尾部窗口（train_end−183 天） |
| C2 | ✅ 已修 | `backtest_recent.py` 删除内嵌退役 DSL 与无条件覆写逻辑，改为只读 `best_strategy_path()`（缺失报错退出） |
| C3 | ✅ 已修 | sandbox exec 显式注入受限 `_SANDBOX_BUILTINS`（基础类型/常用函数/受控异常集 + 白名单守卫版 `__import__` 复用 `_assert_module_allowed` + `__build_class__`）；审计黑名单补 `__builtins__`/`getattr`/`setattr`/`delattr`/`vars`。冒烟验证 5 种逃逸路径（builtins 下标/getattr/vars/eval/运行期下标）全部被拦，合法算子加载不受影响。docstring 如实声明残留风险（polars 文件写出向量）与根治方向（子进程隔离） |
| C4 | ✅ 已修 | `LogReturnParams.period: int = Field(default=1, gt=0)`，负值解析期拦截 |
| C5 | ✅ 已修 | `oos_validate.py`/`compare_strategies.py` 删除自管 `run_parallel_backtest`（含 `_prepare_data` 私有调用与 `yamls[0]` 风控污染），改用 `ParallelRunner.run_candidates`——候选各自 YAML 的 `risk_control` 真正生效，A/B 对照语义恢复 |
| C6 | ✅ 上游已修 | 用户并行修复（`46382d8` + `4aa2034`）：读路径双水位 + 与启动同步共享 `FINANCIAL_RECHECK_DAYS=7`。遗留观察点登记 TODO（见第三节） |

### 后端 High（15/15 关闭，H16/H17 含 1 处有意偏离）

| ID | 处置 | 说明 |
|----|------|------|
| H1 | ✅ 已修 | `parallel.run_walk_forward_parallel` 时间轴加 `start_date/end_date` 过滤，与 `core.walk_forward_run` 对齐 |
| H2 | ✅ 已修 | `portfolio.update_from_fill` SELL 分支按持仓截断/拒绝（拒绝时回滚 trade_count 并告警）；返回值语义见下「行为变化」 |
| H3 | ✅ 已修 | `_calculate_benchmark_metrics` 接收交易期时间戳（`_build_result` 新增 `trading_timestamps` 参数自 run() 传入）；无时间轴可传时回退尾部对齐 |
| H4 | ✅ 已修 | `_run_with_timeout` 手动管理线程池生命周期：超时路径 `shutdown(wait=False, cancel_futures=True)` 后再抛，不再被 join 阻塞 |
| H5 | ✅ 已修 | akshare `save_universe` 对齐 P1-01：空 date 保存当前快照，历史日期请求打幸存者偏差 warning |
| H6 | ✅ 已修 | 导出端点改 `BackgroundTask(shutil.rmtree, ...)` 延迟清理；`tmp_dir` 提到 try 前初始化 |
| H7 | ✅ 已修 | 事件推理管线（invoke/prepare_context/ea.load）移入 `_PIPELINE_EXECUTOR`（单 worker，与 research 路径同策略），async 侧只 broadcast |
| H8 | ✅ 已修 | async handler 内 `get_running_loop()` 闭包传入线程，`run_coroutine_threadsafe(..., loop)`；去掉裸 pass，失败 `logger.warning` |
| H9 | ✅ 已修 | 孤儿口径加 30 分钟新鲜度护栏（Python 侧算 cutoff 同钟比较，避免 PG 时区错位）；`delete_run` 对 prod 标签 run 要求 `force=true` 否则 409（复用 `RUN_TAG_PROD` 常量）；clean 按实际删除数统计 |
| H10 | ✅ 已修 | `StockAnalysisState` 补 `fund_flow_analysis: str \| None` 字段 |
| H11 | ✅ 已修 | 5 个分析节点各自 try/except，失败视角降级为占位文案，summarize 聚合继续 |
| H12 | ✅ 已修 | Kimi OpenAI 客户端注入 `timeout=60s` + `max_retries=2`（模块级常量） |
| H13 | ✅ 已修 | `store.update()` 新增公开方法（先存后删语义），compress 合并结果持久化与 remove 即时性对称 |
| H14 | ✅ 已修 | 时序参数键收敛单一事实源：`causality.TEMPORAL_PARAMETER_NAMES`（转公开常量），`dsl.lookback_profile` 改用之 + 非数值参数防御；冒烟验证 compose 算子 warmup 正确计入且旧键回归无损 |
| H15 | ✅ 已修 | 输出/读取路径统一走 `core.storage`；`prove_backtest` 改读权威基准副本 |
| H16 | ✅ 已修 | 止损测试重建场景（`_BuyOnceStrategy` + 专属面板）断言 FILL 成交价 ∈ [stop_line×0.995, stop_line]；`test_max_position_pct_limit` 补「FILL 数量×价格/组合净值 ≤ 5%+容差」实质断言（顺带修复场景缺 open 列导致 FILL 从未产生的缺陷） |
| H17 | ✅ 已修（偏离说明） | 改为真实 invoke 生产代码断言缓存清空。**有意偏离评审字面**：生产 `invoke()` 不重置 `_strategy_trial_count`——其为 DSR 多重检验校正的跨 invoke 累计计数（每 invoke 重置会放水统计门），原测试 docstring 的期望本身错误；新测试按生产声明语义 pin（trial_count 跨 invoke 保留），未改 src |

### 前端 High（3/3 关闭）

| ID | 处置 | 说明 |
|----|------|------|
| FH1 | ✅ 已修 | `manualCloseRef` + 连接身份短路 + timer ref cleanup + 指数退避（5s 起步上限 30s，onopen 重置） |
| FH2 | ✅ 已修 | 同款修复（disconnect 先置空 wsRef 再 close，双重短路）；WS URL 按 location.protocol 探测 wss/ws |
| FH3 | ✅ 已修 | 删 4 秒 setTimeout，`useEffect` 监听 `pipelineStage` 进入 done/error 驱动 reload 与 running 复位；保留本地 running 供触发瞬间立即禁用按钮（早于首个 pipeline_start 到达） |

### 顺带修复的 Medium/Low（约 25 项）

- **引擎**：风控/挂单成交传成交量映射（STOP 触发与即时路径同受 P0-04 约束）；`_fill_market` BUY 整手取整（零股仅可卖出），不足 1 手无成交并返回 `FillEvent | None`；`update_from_fill` 返回 bool + 引擎 `_apply_fill_or_skip` 统一记 ORDER_SKIPPED（新增 `CASH_INSUFFICIENT`/`POSITION_INSUFFICIENT` 枚举）；direct_orders 提交前补填 timestamp；`TimeSeriesSplit` 样本不足显式 ValueError；run() 空交易窗守卫（返回明确失败而非 IndexError）+ `_failed_run_result` 去重两处 RUN_END FAILED 块；`_maybe_precompute_panel` 提取（run() 分支数压回上限内）；`_execute_direct_orders` 拆分；`shared_data.__exit__` 注销 atexit；broker `expired_ids` 死代码删除；telemetry/parallel 的 type ignore 改为声明 `_otel_ctx: Any`（注释说明循环导入原因）/`old_val or ""` 收窄。
- **数据层**：realtime 订阅保存真实句柄 + 自增订阅 ID + 失败路径日志；provider.py docstring 订正（PG + 失败即失败）。
- **分析/记忆**：`store.remove()` 先删 PG 再动内存；search 衰减物质自身半衰期优先（消除浮点 `!=`）；conflict_group 去运行内计数器；collectors 参数类型收窄删 type ignore。
- **应用层**：analyzer 三处静默吞异常改 `logger.exception` + re-raise（端点自然 500）；分页/窗口参数 `Query(ge=, le=)` 边界。
- **脚本**：`${var}` prompt 扫描扩为全 src `**/*.md`；DuckDB 残留表述 5 处订正；`prove_from_audit` run 选择打印元数据并按策略名过滤。
- **测试**：`test_audit_flow._latest_run_id` 改 `engine._current_run_id`；`test_engine.py` `unittest.main()` 移至文件末尾；slippage 属性测试适配整手取整语义（不足 1 手无成交时跳过）；`test_metrics` 新增 `test_volume_below_lot_produces_no_fill` 守卫碎股回归，并改写 `test_volume_limit_marks_metrics_unreliable`（原测试编码的是旧碎股行为）。
- **文档**：sandbox 模块 docstring 安全声明降级为如实描述。

---

## 二、行为变化（合并方须知）

1. **API 语义**：
   - `DELETE /api/runs/{id}` 对带 `prod` 标签的 run 默认 409，须 `?force=true`；`DELETE /api/runs/clean` 只清「无 RUN_END 且最早事件早于 30 分钟」的孤儿（正在运行的回测不再被误删），`deleted_runs` 为实际删除数；DB 故障从 200 空列表变为 500；分页越界 422。
   - **`web/openapi.json` 尚未重生成**（force 参数与 ge/le 会改变契约）：合并后需跑 `uv run python scripts/_dump_openapi.py` 再 `npm run api:gen`。
2. **回测语义**（正确性修复，历史回测数字可能有微小差异）：
   - 买入受参与率限额 + 整手取整：限额不足 1 手 = 无成交（旧行为产生碎股成交并被收最低佣金）；
   - 卖出成交不得超过实际持仓（超卖截断/拒绝，杜绝凭空增资）；
   - 挂单 STOP 触发成交受成交量参与率与冲击成本约束（与即时路径同口径）；
   - 成交被组合拒绝时审计记 `ORDER_SKIPPED`（CASH_INSUFFICIENT / POSITION_INSUFFICIENT）而非 FILL SUCCESS；
   - 并行 walk-forward fold 边界与单进程版对齐（排除 warmup）——历史并行 fold 级 OOS 数字与新版不可直接比较；
   - benchmark 对齐指标（alpha/beta/IR/TE）在 warmup_days>0 时数值会变（旧行为错位）。
3. **脚本**：`find_best_strategy.py` 默认不再自动执行双季度验证（新增 `--final-validation`）；相关脚本输出路径改 `LONG_EARN_DATA_DIR` 裁决。
4. **前端**：WS 断线重连改指数退避（5s/3s → 上限 30s）；EventFlowPage 触发后按钮跟随真实管线终态解禁。
5. **记忆系统**：compress 合并结果即时落盘（重启不丢）；remove 失败时内存不再先行删除。

---

## 三、暂缓项（登记 TODO.md 跟进）

| 项 | 级别 | 暂缓原因 |
|----|------|----------|
| 止盈成交价口径（日内 high 判定 + high 成交双重乐观） | M | 改变回测成交语义，需专项决策并与止损保守口径统一设计 |
| 风控卖出单（止盈/止损/回撤清仓）贯通 `_pre_trade_check`（跌停/停牌拒单） | M | 同上；且现有风控测试断言需同步重设计 |
| app 层绕 services 直连 DataCache 私有成员跑裸 SQL（六处） | M | 属分层重构，需下沉为 DataCache 公有方法，改动面独立成项 |
| `tools/backtest_analyzer.py` 整模块死代码删除 | M | 删除类动作留待所有权人确认 |
| `core/pg.py` conninfo 转义、`master_agent.get_llm()` 单例、`research_service` 原地改写 config、`htr_subgraph` insight 覆盖 / volume 恒 False、`acceptance.primary_metric` 失效、quality_momentum null→满分、causality 指纹/覆盖缺口、算子名实不符、热注册盘/进程漂移、operator_executor alias 覆盖、ciccwm TLS、pg conninfo 等 Medium | M | 逐项独立修复，本轮聚焦 Critical/High |
| openapi.json 重生成 + 前端 api:gen | M | 需按项目流程执行（见行为变化 1） |
| C6 遗留观察点：启动同步未运行时读路径重复拉取沉默股票；`ensure_financial_cache` docstring 仍写 DuckDB | L | 前者为已声明的设计权衡；后者在所有者 WIP 文件内 |
| 其余 Low（前端竞态守卫推广、可访问性、personas/analyst 双轨、pyproject duckdb 死依赖等） | L | 见评审记录 Low 摘要 |

---

## 五、第二轮修复（2026-08-30，Medium/Low 全量）

> 主线：关闭第一轮暂缓的 Medium/Low；错误结论驳回；冲突项改为声明对齐/护栏硬化。

### 5.1 驳回或冲突改判

| 项 | 处置 |
|----|------|
| 文首「后端 High 21」 | 笔误 → 订正为 17（正文 H1–H17） |
| H17「invoke 应重置 trial_count」 | 已驳回（DSR 跨 invoke 累计） |
| `connector._parse_quarter` 抛 ValueError | 评审过时：实现返回 `("", "")` 与 docstring 一致 |
| 算子 `roe_quality` / `gross_margin_stability` 改名 | 冲突 YAML → 只改 docstring，不改 ID |
| remote 完整鉴权 | 无身份产品 → 只加固 Origin |
| 因果性补全基本面形式化证明 | 收窄注册声明，不扩写证明管线 |
| 热注册强制覆盖磁盘 | warning + 指纹漂移告警，不默认覆盖 |
| acceptance 首轮负 sharpe | 设计意图（建基线）；只修 `primary_metric` 使用 |

### 5.2 本轮已修

| 主题 | 说明 |
|------|------|
| 止盈成交口径 | 触发用 high；成交 `min(止盈线, high)`，与止损对称；`TestTakeProfitConservativeFill` |
| 风控贯通 `_pre_trade_check` | `_execute_risk_market_sell`；**跌停拒卖与信号单同口径**（持仓保留、次 bar 再试）；`test_max_drawdown_limit_down_retries` |
| ciccwm TLS | 默认校验证书；`CICCWM_SSL_VERIFY=false` 逃生 |
| pg conninfo | `psycopg.conninfo.make_conninfo` |
| quality_momentum | 去掉 null→0→满分；null 自然传播 |
| 算子声明 | roe_quality/gross_margin docstring；热注册 warning；alias 择列；指纹声明收窄 |
| 服务层 | ingestion docstring；backtest_service 双轨/run_grid；stock_service revenue；config lazy mkdir；master_agent LLM |
| 策略/记忆 | primary_metric；research_service 拷贝 config；HTR insight 优先；motion 反查；llm_factory |
| App/tools | 删 `tools/backtest_analyzer`；sector stats 公有 API；Origin 硬化；realtime_alert 锁；tools/store |
| 脚本 | `--auto-window` held-out；`langgraph.json`→master_agent；CI 接 check_deprecated |
| 测试 | integration 标记 / conftest；PG 清理加固 |
| 前端 | Blob 预览；abort/stale；error 态；Low（常量/Esc/主题/formatDate） |
| Low | miniqmt 死代码、duckdb 依赖、md_splitter、tree_store、personas EXAMPLES 等 |
| parallel 兜底 provider | `_prepare_data` 未注入时 MiniQmt+DataCache 用毕 close |
| unit PG 假绿 | 剩余 skipif 全部改为 `pytest.mark.integration`（[PG假绿改integration标记](cf93f000-4476-4ea3-bcdd-3432d01b3ee7)） |

### 5.3 第一节暂缓表状态

第三节原暂缓项已由第二轮关闭或改判（见 5.1）；openapi 契约已在第一轮后 `ffaed4b` 同步。

---

## 四、改动文件清单（43 个）

- **scripts（10）**：backtest_recent / check_deprecated_syntax / compare_strategies / download_data / find_best_strategy / oos_validate / prove_backtest / prove_from_audit / test_cache_sync / validate_dual_quarter
- **engine（10）**：audit / broker / core / dsl / parallel / portfolio / shared_data / telemetry / timeseries_split / （operators）causality / log_return
- **data（4）**：akshare_provider / miniqmt_provider / provider / realtime（cache.py 因所有者 WIP 明确排除）
- **app（2）**：app / analyzer
- **分析与记忆（7）**：stock_analysis state+subgraph / substance motion+store / event_inference subgraph+collectors/__init__ / services kimi_web_search
- **operator_dev（1）**：sandbox
- **tests（5）**：test_engine / test_audit_flow / test_metrics / test_properties / test_research_agent
- **web（3）**：useWebSocket / useResearchWebSocket / EventFlowPage
- **docs（3）**：reviews/README / 本评审记录 / 本修复记录

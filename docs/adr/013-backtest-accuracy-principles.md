# ADR-013：回测引擎准确性原则与陷阱清单

> **状态**：已采纳  
> **日期**：2026-07-11  
> **作者**：shiyz  
> **关联**：ADR-005（事件驱动回测框架）、ADR-008（并行回测）、ADR-009（算子目录）

---

## 一、动机

回测引擎是量化交易系统最核心的基础设施。一个回测结果如果包含了未检测到的偏差，其结论比没有回测更危险——它会给你虚假的信心，投入真金白银后遭受亏损。

本 ADR 系统梳理量化回测领域已知的全部常见错误，分类列出每类错误的成因、危害、检测方法和本引擎的防护状态，作为后续测试设计和代码审查的纲领性文件。

---

## 二、回测准确性七维分类框架

所有回测错误可以分为以下 7 个维度，按对回测可信度的影响程度排序：

### 1. 数据正确性（Data Integrity）

回测的输入数据本身存在偏差或错误，导致结果系统性失真。

| # | 陷阱 | 描述 | 危害 | 检测方法 |
|---|---|---|---|---|
| D1 | **幸存者偏差** | 使用当前成分股回测历史区间，退市/ST 股被剔除 | 回测业绩系统性高估（通常 2-5%/年） | universe 快照日期校验 + PIT 警告标志 |
| D2 | **前向填充泄漏** | `ffill()` 在未排序数据上执行，将未来值填充到过去 | 财务因子回测虚高 | 因果性测试（扰动后半段断言前半段不变） |
| D3 | **披露延迟未对齐** | 使用财报"截止日"而非"披露日"作为数据可见起点 | 经典未来函数泄漏 | PIT 对齐测试（assert 公告日前为 NaN） |
| D4 | **复权不一致** | 不同数据源复权方式不同（前复权/后复权/不复权） | 收益率计算系统性偏差 | provider 基类强制声明 adjust 策略 |
| D5 | **拆分/分红未处理** | 除权除息日前后价格跳空未调整 | 回测期间出现虚假涨跌幅 | 复权因子校验 |
| D6 | **停牌数据填充** | 停牌日价格不变但成交量=0，ffill 后误判为可交易 | 停牌日虚假交易 | 成交量=0 时拒绝交易 |

### 2. 时序偏差（Temporal Bias）

回测过程中不恰当地使用了"未来"信息。

> **T6 补充背景（2026-08 评审发现）**：`_compute_warmup_days`（`backtest_service.py:126`）曾只扫描 `operator_factors` 的 `period`/`window`/`span` 三键名。实测漏算：`shift` 用 `periods`（复数）-> 永远取 0；`macd` 用 `fast`/`slow`/`signal` -> 永远取 0（EMA 预热期不计入）；且完全不扫 `signals` 里的算子步骤。所有 technical 算子的 `min_history: ClassVar[int] = 0` 是静态占位（`windowed.py:43` 注释自承「apply 前无法静态确定」），warmup 计算机制设计上不完整。此 bug 在 ADR-008 并行回测扩展到 HTR 候选批量回测（ADR-010 阶段 5 收尾）前必须修复，否则被 `max_warmup` 取多候选 max 的机制掩盖。修复方向：算子目录暴露 `lookback(params) -> int` 方法（替代静态 `min_history=0` 占位），`_compute_warmup_days` 遍历 factor + signal 全部算子步骤求 max。

| # | 陷阱 | 描述 | 危害 | 检测方法 |
|---|---|---|---|---|
| T1 | **T日信号T日成交** | 基于当日收盘价决策并以当日收盘价成交 | 回测业绩显著虚高 | 因果性测试 + T+1 执行检查 |
| T2 | **未来数据进入计算** | 指标计算中使用了尚未发生的数据点（如 rolling 窗口未对齐） | 因子值"偷看"未来 | 可见性守卫 + 算子因果性证明 |
| T3 | **回测区间选择偏差** | 选择表现最好的时间段作为回测区间 | 策略只在特定市场环境有效 | 多区间测试 + Walk-Forward OOS |
| T4 | **样本内外泄漏** | Walk-Forward 中训练集和测试集时间重叠 | OOS 业绩虚高 | 严格时序分割校验 |
| T5 | **信息到达时间错位** | 收盘前发布的新闻/公告被当作次日才可用 | 事件驱动策略虚高 | 披露时间戳对齐测试 |
| T6 | **warmup 漏算致因子前视截断** | `_compute_warmup_days` 只扫 `period`/`window`/`span` 三键，遗漏 `shift.periods`、`macd.fast/slow/signal`、`ewm_mean.span` 等算子回溯窗口；且不扫 `signals` 里的算子步骤。结果预取区间短于真实回溯需求，因子前若干 bar 全 NaN，`rank_top` 选不出股票 | 回测 `trade_count=0` 误判策略退化；或退化策略因 sharpe 数值「占优」混入最优（关联 C5） | 算子 `lookback(params) -> int` 契约测试 + warmup 区间覆盖率测试（每算子参数键均被计入） |

### 3. 交易执行（Execution Fidelity）

模拟交易与真实交易的差异。

| # | 陷阱 | 描述 | 危害 | 检测方法 |
|---|---|---|---|---|
| E1 | **全额成交假设** | 假设市价单总是全额成交，无视流动性 | 大单策略业绩严重虚高 | 成交量参与率限制测试 |
| E2 | **固定滑点** | 使用固定 bps 滑点而非动态冲击模型 | 低估大单成本 | 冲击模型验证 |
| E3 | **最低佣金未实现** | A 股佣金最低 5 元/单，小单实际成本远高于费率 | 高频/小资金策略虚高 | 佣金计算测试 |
| E4 | **印花税遗漏** | 卖出印花税未计算（A 股万五） | 换手率高的策略虚高 | 税费计算测试 |
| E5 | **过户费遗漏** | 沪市过户费未计算（双向万分之 0.1） | 沪市交易成本低估 | 过户费测试 |
| E6 | **成交价过于乐观** | 使用 bar 内最优价（high/low）而非保守价（open+滑点） | 限价单策略虚高 | 限价单保守成交测试 |
| E7 | **部分成交未追踪** | 大单部分成交后剩余部分被丢弃 | 实际持仓与模拟不符 | partial_fill 追踪测试 |
| E8 | **盘中决策用收盘价** | 策略假设盘中可以以收盘价成交 | 趋势跟踪策略虚高 | 信号-成交时戳差异检查 |

### 4. 市场微观结构（Market Microstructure）

| # | 陷阱 | 描述 | 危害 | 检测方法 |
|---|---|---|---|---|
| M1 | **涨跌停板忽略** | 涨停可买入、跌停可卖出 | 封板策略严重失真 | 涨跌停拒单测试 |
| M2 | **T+1 制度忽略** | 当日买入当日可卖出 | 日内反转策略虚高 | T+1 约束测试 |
| M3 | **停牌忽略** | 停牌期间照常交易 | 停牌股虚假交易 | 停牌拒单测试 |
| M4 | **最小交易单位忽略** | A 股 100 股（手）为最小交易单位，零股不可交易 | 持仓计算偏差 | 最小单位检查 |
| M5 | **价格档位忽略** | A 股不同价格区间最小变动单位不同（0.01/0.001） | 限价单价格无效 | 档位对齐检查 |
| M6 | **限价/停板价格不取整** | 涨跌停价格未四舍五入到分 | 风控判断偏差 | `round(price, 2)` 验证 |

### 5. 投资组合与风控（Portfolio & Risk）

| # | 陷阱 | 描述 | 危害 | 检测方法 |
|---|---|---|---|---|
| P1 | **现金约束不准确** | 现金不足时抛异常终止回测而非跳过 | 回测不完整 | 现金不足跳过测试 |
| P2 | **行业集中度未限制** | 全部资金买入同一行业股票 | 集中度过高风险 | 行业集中度测试 |
| P3 | **止盈缺失** | 仅有止损无止盈 | 浮盈回吐未被控制 | 止盈触发测试 |
| P4 | **max_turnover 未实现** | 配置了换手率限制但代码不检查 | 配置无效 | 换手率检查测试 |
| P5 | **杠杆未控制** | 允许现金不足时融资买入 | 回测使用不现实的杠杆 | 现金约束检查 |
| P6 | **日内多次交易未合并** | 同一 bar 内多次信号独立执行，未合并订单 | 交易次数虚高 | 订单合并检查 |

### 6. 指标计算（Metrics Computation）

| # | 陷阱 | 描述 | 危害 | 检测方法 |
|---|---|---|---|---|
| C1 | **年化公式不一致** | 夏普比率用算术年化但波动率用几何年化 | 指标口径混乱 | 公式对齐测试（与 numpy 直接计算比对） |
| C2 | **无风险利率假设** | 使用 R_f=0 忽略无风险利率 | 低利率环境下 Alpha 虚高 | 参数化 R_f 测试 |
| C3 | **样本量不足输出指标** | 3 天回测算出夏普比率 10+ | 极端值误导决策 | min_trading_days 门槛测试 |
| C4 | **复利计算偏差** | 日收益率累乘 vs 简单累加 | 长期回测偏差显著 | 几何/算术收益率比对测试 |
| C5 | **退化策略混入最优** | filter 全部筛掉 → 空仓 → 零收益，被误标为"无亏损策略" | 网格搜索选到"啥都不做"的"最优" | metrics_unreliable 过滤测试 |

### 7. 工程与审计（Engineering & Audit）

| # | 陷阱 | 描述 | 危害 | 检测方法 |
|---|---|---|---|---|
| A1 | **审计日志丢失** | 异常路径/并行 worker 的审计不持久化 | 无法追溯失败原因 | 审计事件完整性测试 |
| A2 | **非确定性审计时间戳** | 使用 `datetime.now()` 而非单调时钟 | 重放时序混乱 | 时间戳单调性测试 |
| A3 | **审计阻断主流程** | 审计写入失败抛异常终止回测 | 小问题导致整个回测丢失 | 审计降级测试 |
| A4 | **审计数据不可重放** | RUN_START 缺少完整输入参数 | 无法根据日志重建回测 | 输入参数完整性测试 |

---

## 三、检测方法论

### 3.1 因果性测试（Causality Test）

**原理**：跑一次回测得权益曲线 E1；把回测区间后半段价格大幅扰动（模拟"未来被改写"），再跑得 E2；断言前半段逐日权益不变。

**这是最强大的单一检测手段**——它能同时检测出：
- T1（T日信号T日成交）：T+1 执行使边界退后 1 bar
- D2（ffill 泄漏）：未来数据填充到前半段
- T2（未来数据进入计算）：任何形式的未来泄漏

**本引擎实现**：`tests/unit/test_backtest/test_operators/test_operator_dsl_causality.py`

### 3.2 PIT 对齐测试（Point-in-Time Test）

**原理**：构造已知披露日期的财报数据，验证在披露日期之前该数据为 NaN，披露日当天起可见。

**检测范围**：D3（披露延迟未对齐）

**本引擎实现**：`tests/unit/test_backtest/test_pit_regression.py`

### 3.3 对比测试（Reference Comparison）

**原理**：用 numpy/pandas 直接计算期望值，与引擎输出比对（`pytest.approx`）。

**检测范围**：C1-C4（指标计算）

**本引擎实现**：`tests/unit/test_backtest/test_operators/test_numerics.py`

### 3.4 合规约束测试（Compliance Constraint Test）

**原理**：构造已知应被拒绝的交易场景，验证引擎正确跳过并审计。

**检测范围**：M1-M6（市场微观结构）、E1-E3（交易执行约束）

**本引擎实现**：`tests/unit/test_backtest/test_compliance.py`

### 3.5 回归测试（Regression Test）

**原理**：对已修复的缺陷构造特定场景，断言修复后行为正确，防止回归。

**检测范围**：所有已修复缺陷

**本引擎实现**：使用 `@pytest.mark.regression` 标记

---

## 四、本引擎防护状态总览

| 类别 | 总项 | 已防护 | 未防护 | 覆盖率 |
|---|---|---|---|---|
| D: 数据正确性 | 6 | 2 (D2,D3) | 4 (D1,D4,D5,D6) | 33% |
| T: 时序偏差 | 6 | 2 (T1,T2) | 4 (T3,T4,T5,T6) | 33% |
| E: 交易执行 | 8 | 5 (E1,E2,E3,E4,E5) | 3 (E6,E7,E8) | 63% |
| M: 市场微观结构 | 6 | 3 (M1,M2,M3) | 3 (M4,M5,M6) | 50% |
| P: 投资组合与风控 | 6 | 2 (P1,P3) | 4 (P2,P4,P5,P6) | 33% |
| C: 指标计算 | 5 | 2 (C3,C5) | 3 (C1,C2,C4) | 40% |
| A: 工程与审计 | 4 | 2 (A1,A3) | 2 (A2,A4) | 50% |
| **合计** | **41** | **18** | **23** | **44%** |

---

## 五、测试组织规范

### 5.1 测试文件按准确性主题组织

```
tests/unit/test_backtest/
├── test_data_correctness/        # D 类：数据正确性
│   ├── test_pit_regression.py    # D3 PIT 对齐
│   ├── test_ffill_sorted.py      # D2 ffill 排序
│   └── test_universe_pit.py      # D1 幸存者偏差
├── test_temporal_bias/           # T 类：时序偏差
│   ├── test_causality.py         # T1/T2 因果性（无未来函数）
│   └── test_walk_forward.py      # T3/T4 Walk-Forward 正确性
├── test_execution/               # E 类：交易执行
│   ├── test_broker.py            # E1-E8 撮合/成本/滑点
│   └── test_impact_model.py      # E2 冲击模型
├── test_compliance/              # M 类：A 股合规
│   └── test_compliance.py        # M1-M6 A 股制度
├── test_portfolio/               # P 类：投资组合
│   ├── test_portfolio.py         # P1-P6 风控/现金/换手率
│   └── test_risk.py              # P3 止盈止损
├── test_metrics/                 # C 类：指标计算
│   └── test_numerics.py          # C1-C5 数值正确性
└── test_audit/                   # A 类：审计
    └── test_audit_flow.py        # A1-A4 审计完整性
```

### 5.2 测试标记规范

```python
@pytest.mark.regression       # 回归测试（防回退）
@pytest.mark.causality        # 因果性证明（无未来函数）
@pytest.mark.compliance       # A 股合规
@pytest.mark.slow             # 慢测试（>1s）
@pytest.mark.requires_credentials  # 需要外部凭证
```

### 5.3 每个测试必须断言的内容

每个准确性测试必须至少断言以下之一：
1. ✅ **正确执行**：assert result.success
2. ✅ **正确拒绝**：assert ORDER_SKIPPED 审计事件
3. ✅ **数值相等**：assert value == pytest.approx(expected, rel=1e-5, abs=1.0)
4. ✅ **无泄漏**：因果性测试中 assert 前半段权益不变

---

## 六、参考资源

- [Backtesting: Common Mistakes to Avoid - QuantInsti](https://blog.quantinsti.com/common-backtesting-mistakes/)
- [Walk-Forward Analysis - Cambridge University Press](https://www.cambridge.org/core/books/abs/algorithmic-trading/walkforward-analysis/)
- [Overcoming Backtesting Overfitting - Bailey et al. (2016)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2847416)
- [Pseudo-Mathematics and Financial Charlatanism - Mahalanobis (2022)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3193478)

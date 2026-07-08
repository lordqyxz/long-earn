# ADR-007: 使用真实财报发布日期（announce_date）替代固定延迟 + 全量字段提取

## 状态
已确认（2026-07-09）— Phase 1-3 全部实施

- Phase 1：announce_date 必填，无回退，不兼容旧数据，缓存可全量重建
- Phase 2：财务数据接口统一到 miniqmt，屏蔽 ciccwm/akshare 降级分支
- Phase 3：四表合并全量字段提取（Income + Balance + CashFlow + Pershareindex，18 字段）

## 背景

### Phase 1：PIT 修复
当前数据层 PIT 契约使用 `report_date + 60天固定延迟`，审计发现 AUDIT-P0-01：
- 年报 report_date=12-31，法定披露截止次年 4-30（120 天），60 天延迟导致 3-01 至 4-29 约 40 个交易日的未来函数泄漏
- 调研发现 akshare 已返回 `公告日期`、miniqmt 已返回 `m_anntime`，但代码完全忽略

### Phase 2：接口统一
ciccwm/akshare 财务降级分支增加维护成本，且字段口径与 miniqmt 不一致。
用户决策：屏蔽 ciccwm/akshare 财务降级，聚焦 miniqmt 打通系统核心目标。

### Phase 3：全量字段
原系统只提取 7 个财务字段（revenue/net_profit/eps/roe/gross_margin/net_profit_yoy/revenue_yoy），
xtquant 提供的 8 张财务表中仅用了 Income 和 Balance 两张。大量可用字段未入库。

## 决策

### Phase 1：announce_date 必填，无回退

**announce_date 必填，无回退。** `_quarterly_to_daily` 只有一个逻辑：`visible_from = announce_date`。

#### 架构原则
- 简洁第一：`_quarterly_to_daily` 不再有 `publication_lag_days` 参数，不再有回退分支
- 不兼容旧数据：缓存表直接 DROP + CREATE，旧数据全量重建
- Provider 自治：各 provider 自己负责提取/构造 announce_date 字段
  - miniqmt：从 `m_anntime` 字段提取（**唯一保留的财务路径**）

#### _quarterly_to_daily 逻辑（miniqmt 唯一实现）

```python
def _quarterly_to_daily(self, quarterly_df, symbols, trading_dates, fields) -> pd.DataFrame:
    for _, row in symbol_data.iterrows():
        visible_from = pd.to_datetime(row["announce_date"])  # 唯一逻辑，无回退
        mask = daily.index >= visible_from
        ...
```

### Phase 2：财务接口统一到 miniqmt

- `CompositeDataProvider` 的 `get_financial_panel` 只走 miniqmt 路径
- `akshare_provider.py` / `ciccwm_provider.py` **删除全部财务方法**
  （`get_financial_panel` / `_quarterly_to_daily` / `_normalize_finance_items` /
  `_lag_by_report_type` / `CICCWM_FINANCIAL_FIELD_MAP`）
- ciccwm 保留 `MarketIntelligenceProvider` 接口（资金流向/排行/板块/资讯）
- akshare 保留行情/成分股降级能力

### Phase 3：四表合并全量字段提取

#### 字段映射（FINANCIAL_FIELD_MAP，18 字段）

```python
FINANCIAL_FIELD_MAP = {
    # 利润表（Income）原始字段
    "revenue": "revenue",
    "net_profit": "net_profit",
    "eps": "eps",
    "research_expenses": "research_expenses",
    # 资产负债表（Balance）原始字段
    "total_equity": "total_equity",
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
    # 现金流量表（CashFlow）原始字段
    "ocf": "ocf",
    "capex": "capex",
    # 每股指标/主要指标表（Pershareindex）预计算字段
    "bps": "bps",
    "ocf_per_share": "ocf_per_share",
    "debt_to_assets": "debt_to_assets",
    "net_profit_margin": "net_profit_margin",
    "roe_weighted": "roe_weighted",
    # 衍生指标（Pershareindex 预计算优先，手算兜底）
    "net_profit_yoy": "net_profit_yoy",
    "revenue_yoy": "revenue_yoy",
    "roe": "roe",
    "gross_margin": "gross_margin",
}
```

#### 四表合并提取（_fetch_financials）

miniqmt 的 `_fetch_financials` 并行获取四张表，以 Income 表为基础按 `(symbol, report_date)` 对齐：
- **Income**：revenue / net_profit / eps / research_expenses / total_operating_cost
- **Balance**：total_equity（多字段兜底映射）/ total_assets / total_liabilities
- **CashFlow**：ocf（net_cash_flows_oper_act）/ capex（cash_pay_acq_const_fiolta）
- **Pershareindex**：bps / ocf_per_share / debt_to_assets / net_profit_margin / roe_weighted
  + 预计算衍生：roe（du_return_on_equity）/ gross_margin（gross_profit）/
  net_profit_yoy（du_profit_rate）/ revenue_yoy（inc_revenue_rate）

#### Pershareindex 预计算值优先

`_compute_derived_financials` 改为**手算兜底模式**：
- 优先使用 Pershareindex 表的预计算值（监管口径，比手算更准确）
- 仅当预计算值缺失（NaN）时才用手算兜底
- 手算 ROE 年化系数：Q1×4 / Q2×2 / Q3×4÷3 / Q4×1（粗糙，仅兜底）

#### 缓存表结构（22 列，DROP + CREATE）

```sql
DROP TABLE IF EXISTS financial_quarterly;
CREATE TABLE financial_quarterly (
    symbol VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    announce_date DATE NOT NULL,
    -- Income 表字段
    revenue DOUBLE, net_profit DOUBLE, eps DOUBLE, research_expenses DOUBLE,
    -- Balance 表字段
    total_equity DOUBLE, total_assets DOUBLE, total_liabilities DOUBLE,
    -- CashFlow 表字段
    ocf DOUBLE, capex DOUBLE,
    -- Pershareindex 表预计算字段
    bps DOUBLE, ocf_per_share DOUBLE, debt_to_assets DOUBLE,
    net_profit_margin DOUBLE, roe_weighted DOUBLE,
    -- 衍生指标（Pershareindex 预计算优先，手算兜底）
    net_profit_yoy DOUBLE, revenue_yoy DOUBLE, roe DOUBLE, gross_margin DOUBLE,
    PRIMARY KEY (symbol, report_date)
)
```

#### 消费方更新

- `polars_adapter.py`：`get_merged_panel_as_polars` 不再硬编码 4 字段，
  传 `financial_fields=None` 让 provider 默认返回全量 18 字段
- `strategy_develop_prompt.md`：字段白名单从 7 字段扩展到 23 字段
  （5 行情 + 18 财务），告知 LLM 新增字段可用

### 常量清理
- 删除 `DEFAULT_PUBLICATION_LAG_DAYS = 60`（akshare/ciccwm）
- 删除 miniqmt 方法签名字面量 `60`
- `_quarterly_to_daily` 签名删除 `publication_lag_days` 参数
- 删除 ciccwm 的 `_lag_by_report_type` / `CICCWM_FINANCIAL_FIELD_MAP`（财务代码已删除）

## 影响范围

### Phase 1-2（已提交 f2708c3 + 851c3dc）
1. `cache.py` — 表结构新增 announce_date 列
2. `miniqmt_provider.py` — 提取 `m_anntime` + 简化 _quarterly_to_daily
3. `akshare_provider.py` — 删除财务方法
4. `ciccwm_provider.py` — 删除财务方法 + 差异化 lag
5. `provider.py` — 屏蔽 ciccwm/akshare 降级分支
6. `test_provider_pit_contract.py` — 更新契约测试

### Phase 3（本次变更）
1. `miniqmt_provider.py` — FINANCIAL_FIELD_MAP 7→18 字段 + _fetch_financials 四表合并 + _merge_by_symbol_date + _compute_derived_financials 手算兜底
2. `cache.py` — 表结构 10→22 列 + save_financials/get_financials 全字段同步
3. `polars_adapter.py` — 删除硬编码 4 字段，改为默认全量
4. `strategy_develop_prompt.md` — 字段白名单 7→23 字段
5. `test_provider_pit_contract.py` — 新增 C5 字段提取契约测试（18 字段 + 缓存往返）

## 验证
- 全量单元测试通过（549 passed）
- lint-imports 架构契约通过（3 kept, 0 broken）
- ruff check 全部通过
- 新增测试覆盖：FINANCIAL_FIELD_MAP 18 字段、缓存表 22 列、save/get 全字段往返
- 端到端回测验证无 PIT 泄漏（Phase 1 已验证）

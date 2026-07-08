# ADR-007: 使用真实财报发布日期（announce_date）替代固定延迟

## 状态
已确认（2026-07-09）— 不兼容旧数据，缓存可全量重建，简洁可维护优先

## 背景

当前数据层 PIT 契约使用 `report_date + 60天固定延迟`，审计发现 AUDIT-P0-01：
- 年报 report_date=12-31，法定披露截止次年 4-30（120 天），60 天延迟导致 3-01 至 4-29 约 40 个交易日的未来函数泄漏
- 调研发现 akshare 已返回 `公告日期`、miniqmt 已返回 `m_anntime`，但代码完全忽略

## 决策

**announce_date 必填，无回退。** `_quarterly_to_daily` 只有一个逻辑：`visible_from = announce_date`。

### 架构原则
- 简洁第一：`_quarterly_to_daily` 不再有 `publication_lag_days` 参数，不再有回退分支
- 不兼容旧数据：缓存表直接 DROP + CREATE，旧数据全量重建
- Provider 自治：各 provider 自己负责提取/构造 announce_date 字段
  - akshare：从 `公告日期` 列提取
  - miniqmt：从 `m_anntime` 列提取
  - ciccwm：API 未确认有该字段，用差异化 lag 估算填充（封装在 provider 内部）

### 差异化 lag 估算函数（仅 ciccwm 使用）

```python
def _lag_by_report_type(report_date: pd.Timestamp) -> int:
    """按报告期类型返回法定披露最小 lag（天）。"""
    month = report_date.month
    if month == 12:   # 年报 → 次年 4-30
        return 120
    if month == 6:    # 半年报 → 8-31
        return 65
    return 35         # Q1 (3月) / Q3 (9月)
```

### _quarterly_to_daily 新逻辑（三 provider 统一）

```python
def _quarterly_to_daily(self, quarterly_df, symbols, trading_dates, fields) -> pd.DataFrame:
    for _, row in symbol_data.iterrows():
        visible_from = pd.to_datetime(row["announce_date"])  # 唯一逻辑，无回退
        mask = daily.index >= visible_from
        ...
```

### DuckDB 缓存（全量重建）

```sql
DROP TABLE IF EXISTS financial_quarterly;
CREATE TABLE financial_quarterly (
    symbol VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    announce_date DATE NOT NULL,  -- 新增，必填
    ...
    PRIMARY KEY (symbol, report_date)
)
```

### 常量清理
- 删除 `DEFAULT_PUBLICATION_LAG_DAYS = 60`（akshare/ciccwm）
- 删除 miniqmt 方法签名字面量 `60`
- `_quarterly_to_daily` 签名删除 `publication_lag_days` 参数

## 影响范围（5 个文件）
1. `cache.py` — 表结构 + save/get
2. `akshare_provider.py` — 提取 `公告日期` + 简化 _quarterly_to_daily
3. `miniqmt_provider.py` — 提取 `m_anntime` + 简化 _quarterly_to_daily
4. `ciccwm_provider.py` — 差异化 lag 填充 announce_date + 简化 _quarterly_to_daily
5. `test_provider_pit_contract.py` — 更新契约测试

## 验证
- 全量单元测试通过
- 新增测试覆盖：announce_date 优先、字段提取、差异化 lag（ciccwm）
- 端到端回测验证无 PIT 泄漏

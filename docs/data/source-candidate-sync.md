# CSI 300 数据源候选同步

## 目标与边界

`scripts/sync_csi300_source_candidates.py` 将两路原始数据保存在 PostgreSQL 的
`research_staging` schema：

- AkShare `index_stock_cons_csindex("000300")`：当前 CSI 300 成分快照。
- MiniQMT `xtdata.get_divid_factors`：PG 已保存的历史 CSI 300 成分与 AkShare 当前快照成分的并集之除权因子。

脚本只写 `research_staging.source_candidate_batch` 和
`research_staging.source_candidate_record`，不写 `public.universe_constituents`、
现金分红表或回测缓存。同步批次的 `verification_status` 为 `unverified`，
`rights_review_status` 为 `not_assessed`。

## 数据含义

AkShare 文档将 `index_stock_cons_csindex` 定义为中证指数成分目录接口，参数只有指数代码；
返回含 `日期` 和成分券字段的当前快照。返回的 `日期` 保留在 payload 的
`provider_snapshot_date`，不写成成员的 `effective_date`，因为接口没有提供历史成员区间。

MiniQMT 文档将 `get_divid_factors(stock_code, start_time, end_time)` 定义为除权数据接口，
返回 DataFrame。样本实测列为 `time`、`interest`、`stockBonus`、`stockGift`、`allotNum`、
`allotPrice`、`gugai`、`dr`，行索引为 `YYYYMMDD`。脚本保存整行原始字段；只在行索引严格匹配
`YYYYMMDD` 时填写 `effective_date`。`interest` 等字段不转换成现金分红记录，`dr` 也不据此
宣称整个回测价格序列已验证。

## 执行

在 `D:\dev\long-earn` 的项目虚拟环境中运行：

```powershell
.\.venv\Scripts\python.exe scripts\sync_csi300_source_candidates.py
```

脚本先保存 AkShare 快照，再按 100 个证券分块请求 MiniQMT。MiniQMT 每个完整分块单独提交，
相同请求与相同响应通过内容哈希保持幂等；若后续请求中断，已完成分块仍可保留，重新运行即可。
除权因子请求使用 MiniQMT 当前本地可用的完整区间，不触发交易接口。

## 官方资料

- [AkShare 中证指数成分接口文档](https://github.com/akfamily/akshare/blob/main/docs/data/index/index.md#中证指数成份股)
- [迅投 XtData 行情模块文档](https://dict.thinktrader.net/nativeApi/xtdata.html)
- [迅投除权除息日和复权因子字段说明](https://dict.thinktrader.net/innerApi/data_function.html?id=I3DJ97)

## 使用限制

候选记录供人工抽样比对和后续来源评估。只有经过事件级核验、来源授权审核和独立复权验证后，
才可以讨论迁移到规范数据表。当前 AkShare 成分接口不能替代历史 PIT 成分数据；MiniQMT 因子
不能自动补齐分红公告、股权登记日、派息日或税务口径。

## 2026-10-09 同步记录

本次运行成功，AkShare 返回的快照日期为 2026-10-08：

| 数据集 | 请求范围 | PG 候选记录 | 日期字段 | 状态 |
|---|---|---:|---|---|
| `csi300_constituents_current` | 沪深 300 当前快照 | 300 | `effective_date` 均为空；快照日期在 payload | 未验证 / 权限未评估 |
| `csi300_miniqmt_adjustment_factors` | 744 个历史或当前成分代码 | 13,323 | 1991-05-02 至 2026-10-08；均从 MiniQMT 行索引解析 | 未验证 / 权限未评估 |

其中 739 个代码返回了除权因子，5 个代码未返回因子。原有规范表
`public.universe_constituents` 中 CSI 300 的 49,800 行未改动。

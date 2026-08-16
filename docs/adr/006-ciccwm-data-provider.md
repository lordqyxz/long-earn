# ADR-006: 引入 ciccwm 财经数据 Provider

日期: 2026-06
状态: Accepted（面板「降级链」叙事由 [ADR-018](018-think-on-graph-research-agent.md) 修订：ciccwm 保留为情报独占源 / 显式次选面板源，不再作 Composite 静默 fallback）

## 背景

原数据链为缓存 -> miniqmt (xtquant) -> akshare。miniqmt 必须在装有 miniQMT 客户端的机器上运行；akshare 字段不稳定、速率受限；资金流向 / 涨跌幅排行 / 关联板块 / 热榜资讯等能力完全缺失。中金财富（ciccwm）提供基于 HTTP 的财经数据 skill（已实测可用），补齐缺口且零本地依赖。

## 决策

新增 `backtest/data/ciccwm_provider.py`（Provider）+ `ciccwm_client.py`（HTTP 客户端 + 鉴权，标准库 `urllib`）：

1. **面板数据次选源**：实现 `DataProvider` 面板方法（行情历史、财务报表），字段口径比 akshare 稳定。ADR-018 后不再作静默 fallback，仅显式点名使用。
2. **独占能力**：资金流向 / 涨跌幅排行 / 个股关联板块 / 热榜 / 专题资讯以扩展方法暴露（`get_fund_flow` / `get_ranking` / `get_related_blocks` / `get_hot_rank` / `get_topic_news`），后抽为 `MarketIntelligenceProvider` 协议（ciccwm 独占），不进面板 Protocol；失败显式报错，不静默吞错。
3. **符号格式在 provider 边界抹平**：`600519.SH` <-> `(code, market=数值)`，不泄漏到上层。

### 服务事实

- 服务地址 `https://skill.ciccwm.com/zzt/ext/fcgi/common.fcgi`（`cmdname/param` 包装）；鉴权 `Cookie: apiKey=<key>`，凭证在 `~/.config/ciccwm/config.json` 的 `CICCWM_API_KEY`；鉴权失效返回 `ret=5002`。
- 市场代码：深 0 / 沪 1 / 北 2 / 港 31 / 美股 74 / 美股指数 12。

## 踩坑记录（实测所得）

1. **凭证文件必须 UTF-8 无 BOM**：PowerShell `Set-Content -Encoding utf8` 会写 BOM 导致 `json.load` 抛 `JSONDecodeError`；读取可用 `utf-8-sig` 容错。
2. **解释器**：本机 `python`/`python3` 是 Microsoft Store 占位符，实际可用 `py` 启动器（项目内 `uv run` 不受影响）。
3. **接口硬限制**：排行 limit 上限 80、历史行情默认近 5 日、财务默认近 5 期，透传时需文档化，上层勿以为可无限拉取。

## 后果

- 数据层新增纯 HTTP 源，无新第三方依赖；需遵守 `backtest.data` import-linter 合约。
- 非公开 skill 接口，存在鉴权策略变更或下线风险，需对 `ret=5002` / 网络失败优雅处理。
- client 鉴权与解析逻辑属系统关键环节写单元测试；真实 HTTP 调用属集成测试范畴。

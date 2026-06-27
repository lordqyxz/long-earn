# ADR-006: 引入 ciccwm 财经数据 Provider

## 状态
Accepted

## 上下文

当前数据层（`src/long_earn/backtest/data/`）的数据源降级链为：

```
DuckDB 缓存 → miniqmt (xtquant) → akshare
```

- **miniqmt**：本地 xtquant，行情/财务数据权威，但**必须在装有 miniQMT 客户端的机器上运行**，跨机/无终端环境不可用。
- **akshare**：公共降级源，覆盖面广但字段不稳定、速率受限。

在以下场景现有数据源覆盖不足：

1. **无 miniQMT 环境的基本面分析**：`stock_analysis` 子图需要财务报表（利润表/现金流/资产负债表）和主要指标，akshare 字段口径与命名常变动，维护成本高。
2. **资金流向 / 涨跌幅排行 / 关联板块**：现有 `DataProvider` Protocol 仅定义了 `get_price_panel` / `get_financial_panel` / `get_merged_panel` 三类面板接口，**不覆盖**资金流向、板块排行、个股关联板块等横向市场数据。
3. **资讯面（热榜/专题）**：现有数据源完全无资讯能力，而资讯情绪是股票分析与策略研发的有价值输入。

调研中发现中金财富提供了一套基于 HTTP 的财经数据 skill（部署在 `D:\dev\cidd\.claude\skills\` 下的三个 Claude Code skill），其脚手架已封装好鉴权、请求、字段解析，且**已实测可用**（`ret=0` 鉴权通过）。它正好补齐上述缺口，且不依赖本地 miniQMT，仅需一个 API Key 与网络。

### 数据源能力概览（已实测）

| Skill | 脚本 | 核心函数 | 能力 |
|-------|------|----------|------|
| 行情数据 `ciccwm-market-analysis` | `market_query.py` | `fetch_info` / `fetch_fund_flow` / `fetch_ranking` / `fetch_history` / `fetch_related_blocks` | 证券详情、资金流向、涨跌幅排行（≤80）、多日历史行情、个股关联板块 |
| 财务分析 `ciccwm-stock-finance-analysis` | `finance_query.py` | `query_finance(statement, code, qtime, gtype, limit)` | 财务主要指标 / 利润表 / 现金流量表 / 资产负债表（默认近 5 期） |
| 热榜资讯 `ciccwm-hot-news-analysis` | `get_data.py` | `query_hot_rank` / `query_topic_info` | 今日热榜、专题资讯列表 |

服务与鉴权事实：

- 服务地址：`https://skill.ciccwm.com/zzt/ext/fcgi/common.fcgi`（统一 `cmdname/param` 包装）
- 鉴权：`Cookie: apiKey=<key>` 请求头；凭证存于 `~/.config/ciccwm/config.json` 的 `CICCWM_API_KEY` 字段
- 鉴权失效返回 `ret = 5002`，需引导用户去 skills-center 重装
- 覆盖市场：沪深 A、北交所、港股、美股（`market` 代码：深 0 / 沪 1 / 北 2 / 港 31 / 美股 74 / 美股指数 12）

## 决策

新增一个 **ciccwm 财经数据 Provider**，作为 `DataProvider` 的第四个实现，纳入降级链与 Protocol 扩展。

### 核心优先级
1. **定位：紧跟 miniqmt 的第二优先源**。降级链确定为 `DuckDB 缓存 → miniqmt → ciccwm → akshare`：ciccwm 严格排在 miniqmt 之后、akshare 之前。相对 akshare，ciccwm 字段稳定、口径固定（财务缩写字段有明确含义），故在 miniqmt 不可用时**优先于 akshare** 作为行情/财务的获取源，而非与 akshare 择优。
2. **独占能力**：资金流向 / 涨跌幅排行 / 个股关联板块 / 热榜资讯**只有 ciccwm 能提供**——miniqmt 与 akshare 均无对应能力。对这类数据，ciccwm 不是降级源而是**唯一源**，上层调用无其它兜底，需对失败显式报错而非静默返回空。
3. **零本地依赖**：纯 HTTP（标准库 `urllib`），不依赖 miniQMT / xtquant，补齐无终端环境的数据能力。
4. **Protocol 兼容**：实现现有 `DataProvider` 三方法（行情历史→`get_price_panel`，财务报表→`get_financial_panel`），可直接接入 `CompositeDataProvider` 降级链。
5. **能力扩展**：独占数据以**扩展方法**暴露（不污染核心 Protocol），供 `stock_analysis`、`strategy_rd` 子图按需调用。

## 架构方案

### 1. 落点与命名

```
src/long_earn/backtest/data/
├── ciccwm_provider.py      # 新增：ciccwm HTTP 数据提供者
└── ciccwm_client.py        # 新增：底层 HTTP 客户端 + 鉴权 + 凭证加载（与 skill 脚本逻辑等价）
```

- `ciccwm_client.py`：移植 skill 脚本中已验证的 `load_api_key` / `send_request` / 包装层解析逻辑（`cmdname/param/entry/tdx_param`、`rsp.rsp_json` 解析、`ListHead/ListItem` → 命名记录转换）。**凭证读取路径与 skill 一致**：`~/.config/ciccwm/config.json`。
- `ciccwm_provider.py`：实现 `DataProvider` Protocol，并在 Protocol 之外提供扩展能力方法。

### 2. Protocol 映射

| `DataProvider` 方法 | ciccwm 实现 |
|---------------------|-------------|
| `get_price_panel` | 逐 symbol 调 `fetch_history(code, market, days)`，按日期区间切片，转 `(date, symbol)` MultiIndex DataFrame |
| `get_financial_panel` | 逐 symbol 调 `query_finance("indicators"/"income"/...，code)`，按报告期前向填充到日级 |
| `get_merged_panel` | 复用上两者并 `merge`（沿用 `CompositeDataProvider.get_merged_panel` 既有合并逻辑） |
| `is_available` | 探测：凭证文件存在且 `CICCWM_API_KEY` 非空；可选轻量 `info` 探活 |

> 符号格式差异需在 provider 内部抹平：long-earn 用 xtquant 格式 `600519.SH`，ciccwm 用 `code` + `market` 数值。provider 内置 `600519.SH → (code="600519", market=1)` 转换。

### 3. 扩展能力（超出 Protocol）

在 `CiccwmDataProvider` 上以独立方法暴露，**不进 `DataProvider` Protocol**，避免强迫其它实现也提供：

```python
def get_fund_flow(self, symbol: str) -> pd.DataFrame: ...        # 资金流向
def get_ranking(self, market: int, limit: int, sort_type: int): ...  # 涨跌幅排行
def get_related_blocks(self, symbol: str) -> list[dict]: ...     # 关联板块
def get_hot_rank(self, page_size: int = 10) -> pd.DataFrame: ... # 今日热榜
def get_topic_news(self, subject_id: int | None) -> pd.DataFrame: ...  # 专题资讯
```

### 4. 接入降级链

`CompositeDataProvider` 降级链确定为（ciccwm 紧跟 miniqmt，严格优先于 akshare）：

```
DuckDB 缓存 → miniqmt → ciccwm → akshare
```

**降级语义区分两类数据：**

- **共享数据**（行情历史、财务报表）：miniqmt、ciccwm、akshare 均可提供，按上述链逐级降级，任一返回非空即止。miniqmt 不可用时 ciccwm 优先于 akshare 接管，因 ciccwm 字段口径更稳定。
- **ciccwm 独占数据**（资金流向、涨跌幅排行、关联板块、热榜资讯）：miniqmt 与 akshare **均无对应能力**，不进降级链。`get_fund_flow` / `get_ranking` / `get_related_blocks` / `get_hot_rank` / `get_topic_news` 直接调用 ciccwm，失败时抛异常或返回空并明确标注「ciccwm 不可用，无替代源」，不静默吞错。

### 5. 调研/接入阶段产物

- ciccwm 三 skill 的源脚本位于 `D:\dev\cidd\.claude\skills\ciccwm-*/scripts/*.py`，作为 API 调用范式与字段映射的**参考实现**。long-earn 的 provider 是**重新实现**（符合整洁架构、类型注解、ruff 规范），而非直接 import 这些脚本。
- 凭证文件复用 skill 已写入的 `~/.config/ciccwm/config.json`，无需重复配置。

## 影响

- **架构依赖**：`ciccwm_provider.py` 属数据层，必须遵守 import-linter 合约（`backtest.data` 不依赖上层、不依赖 `tools`）。HTTP 客户端仅用标准库 `urllib`，无新第三方依赖。
- **Protocol 扩展策略**：横向市场数据/资讯以 provider 实例方法暴露，上层通过 `context.data_provider` 的**具体类型**（而非 Protocol）访问扩展方法，或后续若多 provider 都需要时再抽象为 `MarketDataProvider` / `NewsProvider` 子 Protocol。
- **测试**：`ciccwm_client` 的鉴权与解析逻辑属「系统关键环节」，按项目测试原则写单元测试（凭证缺失抛 `CICCWMCredentialError`、`rsp_json` 解析、`ListHead/ListItem` 转换）；HTTP 真实调用属集成测试范畴，需 `.env`/凭证配置。
- **风险**：ciccwm 接口为非公开 skill 接口，存在鉴权策略变更（如开始强制校验 header）或下线风险。provider 需对 `ret=5002` / 网络失败做优雅降级，返回空数据而非抛异常，与 miniqmt/akshare 的降级语义保持一致。

## 踩坑记录（实测所得）

1. **凭证文件编码**：`config.json` 必须是 **UTF-8 无 BOM**。Windows PowerShell 的 `Set-Content -Encoding utf8` 会写入 UTF-8 BOM，导致 Python `json.load` 抛 `JSONDecodeError`。long-earn 内若需生成该文件，应用 `json.dump` + `encoding="utf-8"`，或在读取时 `json.loads(text.encode("utf-8").decode("utf-8-sig"))` 容错。
2. **Python 解释器**：开发机（Windows）上 `python` / `python3` 是 Microsoft Store 占位符，实际可用解释器是 `py` 启动器。脚本/文档示例中的 `python3` 在本机应替换为 `py`（long-earn 通过 `uv run` 执行不受影响）。
3. **市场代码**：ciccwm 的 `market` 是数值（0/1/2/31/74/12），与 long-earn 的 `600519.SH` 字符串格式不同，转换必须在 provider 边界完成，勿泄漏到上层。
4. **排行 `--limit` 上限 80**、**历史行情默认近 5 日**、**财务默认近 5 期**：均为脚本侧硬限制，provider 透传时需文档化，避免上层误以为可无限制拉取。

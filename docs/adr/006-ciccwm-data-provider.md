---
id: 6
title: ciccwm 财经数据 Provider
status: Accepted
date: 2026-06
summary: HTTP 财经情报与面板次选源；面板仅显式点名，不作静默跨源回退。
amended_by: ["ADR-018"]
related: ["ADR-018"]
---

# ADR-006: ciccwm 财经数据 Provider


## 背景

原数据路径为缓存 → miniqmt (xtquant) → akshare。miniqmt 依赖本机客户端；akshare 字段与速率不稳定；资金流向、排行、关联板块、热榜等能力缺失。中金财富（ciccwm）提供基于 HTTP 的财经接口，可补齐上述能力且无本地客户端依赖。

## 决策

新增 `ciccwm_provider` 与 `ciccwm_client`（标准库 `urllib` + Cookie 鉴权）：

1. **面板次选源**：实现行情与财务等面板方法；**仅在调用方显式点名时使用**，不作静默跨源回退（ADR-018）。
2. **情报独占能力**：资金流向 / 排行 / 板块 / 热榜 / 专题资讯经 `MarketIntelligenceProvider` 暴露；失败显式报错。
3. **符号在边界转换**：`600519.SH` 与内部 `(code, market)` 映射不泄漏到上层。

服务端点、鉴权与市场代码编码以源码与配置为准。

## 后果

- **正面**：纯 HTTP 源、无新增重型依赖；情报能力与面板能力分离。
- **负面**：非公开 skill 接口存在鉴权变更或下线风险；须处理 `ret=5002` 与网络失败。
- **中性**：凭证文件须 UTF-8（可读 `utf-8-sig`）；排行与历史窗口存在服务端上限，须在调用方文档化。鉴权与解析属关键路径，写单元测试；真实 HTTP 属集成测试。

# 架构决策记录（ADR）

> **代码是第一真相**：本文档只维护 ADR **编写与维护规范**。各决策正文见同目录 `NNNN-*.md`（元数据在 YAML frontmatter）；实施进度、文件清单、覆盖率以源码为准。  
> 项目级开发规范见根目录 [AGENTS.md](../../AGENTS.md)。运行时总览见 [architecture.md](../architecture.md)。

---

## 何时撰写

依据 Michael Nygard（2011）《Documenting Architecture Decisions》与 [adr.github.io](https://adr.github.io/)。

只记录影响结构、非功能特性、依赖关系、接口或构建技术的架构决策。琐碎实现选择不写。一篇 ADR 只承载一项决策。

---

## 文件与 frontmatter

- 文件名：`NNNN-短横线英文标题.md`，四位数字前缀，编号单调递增且**永不复用**（含已删文件的编号，如 ADR-003 / ADR-004）。
- 文件以 YAML frontmatter 开头，再接 H1 与正文。**基本信息不写在正文**（禁止再写独立的「日期 / 状态 / 关联」行）。

```yaml
---
id: 22                          # 整数，与文件名前缀一致
title: 短中文标题                 # 不含「ADR-NNN:」前缀
status: Accepted                # Proposed | Accepted | Deprecated | Superseded | Deferred
date: 2026-08-30                # YYYY-MM-DD 或 YYYY-MM
summary: 一两句简述               # 学术书面语
# 可选：
supersedes: ["ADR-015"]         # 本 ADR 取代谁
amended_by: ["ADR-021"]         # 本 ADR 被谁修订
superseded_by: "ADR-009"        # status=Superseded 时必填
deprecated_note: "..."          # status=Deprecated 时可选短注
related: ["ADR-018"]            # 其它关联；勿与上列键重复
---
```

### 正文结构

1. **标题（H1）** — `# ADR-NNN: 短名词短语`（可与 `title` 同义，允许稍详）
2. **背景** — 价值中立，只陈述事实与张力
3. **决策** — 主动语态（「我们将……」）
4. **后果** — 正面、负面、中性一并列出

可选正文小节：**参考**（文献与外部标准）。谱系关系优先写在 frontmatter，不必在正文重复「关联」段。

---

## 维护原则

1. **决策反转则新建**：整篇推翻时撰写新 ADR，旧文 `status: Superseded` 并填 `superseded_by`，新文填 `supersedes`。仅修订某一条款时：被修订方填 `amended_by`，双方可用 `related` 互指；**勿**对仍 `Accepted` / `Deferred` 的全文误标 `supersedes`。
2. **短小精悍**：正文限于背景 / 决策 / 后果；不维护实施进度表、Phase 状态、文件清单、行号——以源码为准。
3. **及时压缩**：实现完成后删除分阶段计划与已失效历史原文（git 历史即归档）；仅具历史价值的退役 ADR 可删文件，编号永不复用。
4. **后果即下一代背景**：本 ADR 的后果构成后续 ADR 的背景，形成决策谱系。
5. **用语**：采用学术与工程规范汉语。既成缩写（DSR、PBO、OOS、PIT、ToG 等）可直接使用；生僻术语首次出现时可括注英文全称一次，勿逐词中英夹注。
6. **无中央索引**：有效 / 废弃以各文件 frontmatter 的 `status` 为准；需要目录时按文件名排序或扫描 `docs/adr/*.md`。

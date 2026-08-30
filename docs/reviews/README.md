# docs/reviews — 版本审计记录

本目录存放**全量/专项代码评审与修复的版本化审计记录**。每轮评审落一对文件：

| 文件 | 内容 |
|------|------|
| `YYYY-MM-DD-<范围>-review.md` | 评审记录：范围、方法、按严重度分级的全部发现（含证据与定位） |
| `YYYY-MM-DD-<范围>-remediation.md` | 修复记录：逐条发现的处置映射（已修/暂缓/上游已修）、行为变化、验证结果 |

## 约定

- **严重度口径**：Critical（立即整改：数据分割铁律/未来函数/安全逃逸/合并门破坏）> High（正确性/可用性缺陷，尽快修）> Medium > Low。
- **评审方法**：OpenCodeReview delegate 模式（OCR CLI 负责文件集枚举与规则解析，`ocr delegate preview` / `ocr delegate rule`），实际评审由宿主智能体分组执行；Critical 与关键 High 须经主评审人源码级二次复核后方可定稿。
- **与 TODO.md 的关系**：评审暂缓项按威胁程度登记 TODO.md（观察项/后续轮次），本目录记录保留完整上下文。
- **与 docs/review-rules.md 的关系**：本目录是评审的**时点产物**；评审规则清单的稳定沉淀在 [docs/review-rules.md](../review-rules.md)（冲突时以 AGENTS.md + ADR 为准）。

## 索引

| 日期 | 范围 | 记录 | 修复记录 |
|------|------|------|----------|
| 2026-08-30 | 全系统（后端 src+scripts+tests / 前端 web） | [2026-08-30-full-system-review.md](2026-08-30-full-system-review.md) | [2026-08-30-remediation.md](2026-08-30-remediation.md) |

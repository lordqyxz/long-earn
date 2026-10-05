---
id: 1
title: YAML DSL 策略描述
status: Accepted
amended_by: ["dsh-long-earn-quant ADR-001（docs/adr/001-dsh-runtime-and-deterministic-engine-boundary.md）"]
date: 2024-05
summary: 以 YAML DSL 替代 LLM 生成 Python/qlib 策略代码，并将回测引擎内嵌于主项目。
---

# ADR-001: YAML DSL 策略描述

> **处置（M5 划线，2026-10-06）：维持 Accepted，标注单条款被修订。** 本次划线为清单外处置，理由是决策的**后半段**与实际架构相反：本 ADR 决策「并将回测引擎内嵌于主项目」已被新仓 ADR-001 §决策 3 反转——回测引擎外置为独立 Rust 仓库 `d:/dev/long-earn-engine`，以 CLI 子进程 + Arrow IPC 文件契约消费，零数据库依赖，落库由调用方（新仓 `packages/server/src/engine/bridge.ts`）负责；「LLM → YAML DSL → 本地事件驱动引擎 → 结果」路径中的「本地」由同进程改为同机子进程。
> **维持 Accepted 而非 Superseded 的理由**：决策的**前半段**（以 YAML DSL 描述策略、避免 LLM 生成可执行代码）仍是现行事实——引擎 `--strategy <YAML>` 消费的仍是同一 DSL 家族，编译见引擎仓 `src/dsl/compile.rs`；被反转的只是「内嵌于主项目」这一部署条款，按 ADR 维护规范属「修订某一条款」，故记 `amended_by` 而非改写状态。
> **注**：本仓 DSL 解析与算子目录实现（`backtest/operators/`）随归档冻结保留；引擎仓 `src/factor/` 截至本次划线仍为空目录，算子层尚未迁 Rust，故 ADR-009 不作处置（见新仓 `docs/adr-status.md`）。

## 背景

早期策略由 LLM 生成 Python 代码（依赖 pyqlib），经独立 HTTP 回测服务执行。主要问题：

- LLM 生成代码语法错误率高，输出不稳定；
- pyqlib 依赖引发版本冲突，需独立子项目；
- HTTP 往返引入额外延迟；
- 经 `eval()` 执行的代码存在注入风险。

## 决策

我们将策略描述迁移为 **YAML DSL**，并将回测引擎内嵌于主项目：

```
旧路径: LLM → Python → HTTP → 外部回测服务 (pyqlib)
新路径: LLM → YAML DSL → 本地事件驱动引擎 → 结果
```

## 后果

- **正面**：声明式结构使 LLM 输出更可控；本地执行无网络开销；移除独立回测子项目，降低部署复杂度。
- **负面**：复杂控制流（任意循环、递归）的表达力受限；须维护 DSL 规范与解析期校验。
- **中性**：表达式求值路径后由 ADR-009 算子目录取代 AST 白名单（ADR-003 已退役）；缓存后端现为 PostgreSQL（ADR-019）。

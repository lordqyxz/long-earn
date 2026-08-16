# 归档：废弃的线性 strategy_rd 子图

`subgraph.py` 已由 HTR（ADR-010）取代，编排控制器地位再由 ResearchAgent（ADR-018）取代。

保留本文件仅供考古；**禁止**生产路径 import。测试若仍引用，应改为 HTR / ResearchAgent 或标记 skip。

## 归档内容（2026-08 架构整改收尾）

- `subgraph.py`：废弃线性子图的完整实现（原 `strategy_rd/_archive/subgraph.py`）
- `subgraph_shim.py`：旧兼容 shim（原 `strategy_rd/subgraph.py`，ADR-014/018 后已废弃）
- `debug_checkpoint.py` / `debug_memory_checkpoint.py`：针对该子图的 checkpoint 调试脚本
- `test_strategy_rd_e2e*.py` / `test_reflection_roi.py`：该子图的端到端/收益对比脚本
- `test_strategy_rd_subgraph.py`：该子图的集成测试（原 `tests/integration/`）

全部脱离 Python 包（`src/`）与 import-linter / ruff 分析范围，仅供回滚参考。


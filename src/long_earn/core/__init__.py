"""核心工具层 — prompt 加载、LLM 工具、统一存储路径辅助。

core 是最底层模块，不得依赖 backtest/services/tools/strategy_rd 等上层
（import-linter ``core_independent`` 合约）。
"""

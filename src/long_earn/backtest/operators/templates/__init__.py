"""算子 DSL YAML 策略模板（ADR-009 收尾）。

替代旧 strategy_templates.py 的 3 个 MLSignalStrategy 子类。
模板均为声明式算子 DSL，不复刻状态机；研究员可基于这些模板用算子目录表达
类似策略（双均线 / RSI 均值回归 / MACD 柱）。

模板清单：
- double_ma.yaml: 双均线策略（短均线 > 长均线 → 偏多）
- rsi_mean_reversion.yaml: RSI 超卖回归（RSI < 30 → 超卖反弹）
- macd_histogram.yaml: MACD 柱策略（柱 > 0 → 多头动能）
"""

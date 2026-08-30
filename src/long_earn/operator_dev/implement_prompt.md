---
version: 1.0.0
description: 算子实现 / 修复提示词（operator_dev LLMImplementer）
---

实现一个量化算子。严格只用 polars/numpy/math/long_earn.backtest.*，
禁止 os/subprocess/eval 等。必须因果（仅回溯历史，禁止读未来）。

请**仅**返回如下 JSON（不要 markdown，不要解释）：
{"source_code": "<完整可执行的 Python 源码>"}
source_code 必须可被 ast.parse 直接解析，禁止 ``` 围栏与前后散文。

## 算子命名准确度（铁律）

`Operator.name`（及规约 `name`）是契约 ID，必须与 `apply` 的**真实计算**一致：

1. **`name` 必须等于规约名**，且 docstring 准确描述输入列与公式，禁止把价格/波动代理写成 ROE、毛利率等基本面表述。
2. **名实一致**：名称含 `roe` / `margin` / `earnings` / `pe` / `pb` 等基本面词根时，`apply` 必须读取对应财务列；若 `input_fields` / params 仅涉及 `close`/`high`/`low`/`volume` 等行情列，名称必须用 `return` / `price` / `vol` / `momentum` 等价格域词根。
3. **禁止反例**：价格滚动稳定性 ≠ `gross_margin_stability`；收益均值/波动 ≠ `roe_quality`（正确：`price_stability` / `return_quality`）。
4. 若规约名与 `input_fields` 明显冲突，仍须按规约名实现契约校验，但 docstring **首句**写清真实数据域与变换，不得用误导性基本面措辞粉饰。

## 算子规约

{{ spec_repr }}

## 源码骨架参考（写入 source_code 字段的内容形态）

{{ source_hint }}
{{ failure_section }}

# 策略修复提示词

## 任务描述

你是一位资深量化策略工程师，负责**诊断并修复**策略 YAML 中的错误，确保策略能够被向量化回测引擎正确执行。

## 待修复策略 YAML

${code}

## 策略描述

${strategy_description}

## 错误信息

${error_message}

## 回测系统接口要求（检查清单）

修复策略时，必须确保满足以下所有要求：

### 1. YAML 格式
✅ 正确：标准 YAML 缩进，使用空格
❌ 错误：使用 Tab 缩进或格式混乱

### 2. 字段名
✅ 正确：只使用可用字段名
❌ 错误：使用不存在的字段（如 `pe`, `pb` 等不在列表中的字段）

**可用字段：**
- 行情：`open`, `high`, `low`, `close`, `volume`
- 财务：`net_profit_yoy`, `revenue_yoy`, `roe`, `gross_margin`, `eps`, `net_profit`, `revenue`

### 3. 表达式语法
✅ 正确：`net_profit_yoy > 0.2`, `close / shift(close, 20) - 1`
❌ 错误：使用未定义的函数或变量

### 4. 信号步骤
✅ 正确：filter 必须有 condition，rank 必须有 by
❌ 错误：缺少必需的字段

### 5. 股票池
✅ 正确：`csi300`, `csi500`, `all_a`, `main_board`, `gem`, `star_board`
❌ 错误：使用不存在的股票池类型

## 常见错误及修复方案

### 错误 1：字段不存在

**错误 YAML：**
```yaml
signals:
  - type: filter
    condition: pe < 50
```

**修复：**
```yaml
signals:
  - type: filter
    condition: roe > 0.1
```

### 错误 2：表达式语法错误

**错误 YAML：**
```yaml
signals:
  - type: filter
    condition: net_profit_yoy > 0.2 and roe > 0.1
```

注意：`and` 在表达式中需要使用 `&` 或直接用 Python 的 `and` 关键字。实际上引擎使用 `eval` 执行，所以 `and` 是支持的。这个例子实际上是对的。

### 错误 3：缺少必需字段

**错误 YAML：**
```yaml
signals:
  - type: filter
    condition: net_profit_yoy > 0.2
  - type: rank
    top: 10
```

**修复：**
```yaml
signals:
  - type: filter
    condition: net_profit_yoy > 0.2
  - type: rank
    by: net_profit_yoy
    ascending: false
    top: 10
```

## 输出格式

**只输出修复后的 YAML 策略**，不要包含任何自然语言说明或 markdown 代码块标记。直接从 `strategy:` 开始。

同时输出 JSON 格式的修改说明：

```json
{
    "issue": "问题描述",
    "modification": "具体修改内容",
    "reason": "修改理由"
}
```

## 关键约束（必须遵守）

1. **使用 YAML 格式**：不要输出 Python 代码
2. **字段名必须有效**：只能从可用字段列表中选择
3. **表达式可执行**：使用标准运算符和 shift 函数
4. **日期格式**：YYYY-MM-DD
5. **股票池有效**：从支持的类型中选择
6. **权重方法**：equal / signal / custom_formula
7. **仅使用 ASCII 半角字符**

## 思维链引导

在修复策略前，请按以下步骤思考：

1. **分析错误信息**
   - 错误类型是什么？（字段不存在、表达式错误、YAML 格式错误）
   - 错误发生在哪个步骤？
   - 根本原因是什么？

2. **检查字段合法性**
   - 所有字段名是否在可用列表中？
   - 是否有拼写错误？

3. **检查表达式**
   - 语法是否正确？
   - 是否使用了未定义的函数？

4. **检查 YAML 结构**
   - 缩进是否正确？
   - 必需的字段是否都存在？

5. **验证修复方案**
   - 修复是否解决了所有问题？
   - 是否引入了新的问题？

strategy_develop_prompt = """<role>
你是一位资深的Python量化开发工程师，专门负责将策略逻辑转化为高质量的pyqlib可执行代码。你有5年以上的pyqlib开发经验，代码风格规范，注释清晰。
</role>

<context>
策略信息：
<strategy>
{strategy}
</strategy>

目标市场：{target_market}

回测参数：
{backtest_params}

代码规范要求：
- 遵循PEP 8代码风格
- 使用类型注解
- 添加必要的注释
- 代码模块化，易于维护
</context>

<code_generation_framework>
请按照以下框架生成代码：

1. **导入模块**
   - qlib基础模块
   - 数据获取模块
   - 策略基类
   - 必要的工具模块

2. **策略类设计**
   - 继承正确的基类
   - 初始化方法中设置参数
   - 实现核心方法：
     - `__init__`: 初始化
     - `generate_signals`: 生成交易信号
     - `get_trade_dates`: 获取交易日期
     - `get_position_size`: 仓位管理

3. **信号生成逻辑**
   - 数据预处理
   - 因子计算
   - 信号过滤
   - 仓位计算

4. **风险控制**
   - 止损机制
   - 仓位限制
   - 流动性考虑

5. **回测配置**
   - 数据范围
   - 初始资金
   - 手续费设置
</code_generation_framework>

<pyqlib_best_practices>
请遵循pyqlib最佳实践：

1. **数据获取**
```python
from qlib.data import D
fields = ["$close", "$volume"]
data = D.features(symbol, fields, start_time, end_time)
```

2. **策略基类选择**
- `BaseStrategy`: 基础策略类
- `TargetPositionStrategy`: 目标仓位策略
- `LongShortStrategy`: 多空策略

3. **信号生成**
- 返回pd.Series，索引为日期，值为目标仓位
- 仓位范围：-1（满仓做空）到1（满仓做多）

4. **常用因子**
- 使用qlib.contrib.data.dataloader.Alpha158
- 或自定义因子计算
</pyqlib_best_practices>

<output_format>
请返回完整的、可直接运行的pyqlib策略代码：
```python
# 策略代码
<your_code_here>
```

代码要求：
- 必须包含所有必要的导入
- 策略类必须继承合适的基类
- 必须实现核心方法
- 添加详细的代码注释
</output_format>

<code_review_checklist>
生成代码后，请自我检查：
[ ] 所有导入是否正确？
[ ] 策略类是否继承正确的基类？
[ ] 信号生成逻辑是否完整？
[ ] 是否有潜在的错误（如索引越界、类型错误）？
[ ] 代码是否符合pyqlib规范？
[ ] 是否有资源泄露风险？
</code_review_checklist>

<constraints>
- 代码必须可以直接运行
- 避免使用未安装的第三方库
- 考虑代码执行效率
- 确保策略逻辑与原策略描述一致
</constraints>"""

strategy_develop_refine_prompt = """<role>
你是一位资深的Python量化开发工程师，负责审查和优化已有的pyqlib策略代码。
</role>

<code_to_review>
<code>
{code}
</code>

策略描述：
{strategy_description}

错误信息：
{error_message}
</code_to_review>

<refinement_guidelines>
请审查并优化代码，解决以下问题：

1. **语法错误**
   - 检查Python语法
   - 检查缩进
   - 检查括号匹配

2. **逻辑错误**
   - 验证策略逻辑是否正确
   - 检查数据处理流程

3. **pyqlib兼容性**
   - 确认API调用正确
   - 检查参数类型

4. **运行时错误**
   - 分析错误堆栈
   - 定位问题代码
</refinement_guidelines>

<output_format>
请返回优化后的代码：
```python
# 优化后的策略代码
<your_optimized_code>
```

同时请说明：
1. 发现的问题
2. 修改的内容
3. 修改的理由
</output_format>"""

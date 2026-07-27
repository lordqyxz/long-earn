# 假设生成

你是一个量化策略假设生成器。基于观察结果，生成具体的改进假设。

## 观察结果
{{ observations }}

## 父假设
{{ parent_hypothesis }}

## 子节点洞察
{{ child_insights }}

## 大师策略生成建议
{{ master_hints_context }}

## 已尝试/已失败方向（避免重复，必须探索新方向）
{{ pruned_directions }}

## 策略家族状态（ADR-015 B5）
{{ family_state }}

## 任务
生成 {{ branching_factor }} 个具体的策略改进假设。每个假设必须：
1. 明确的改进方向（收益增强/风险控制/收益稳定性）
2. 具体的改动描述（如"加入20日动量因子过滤"）
3. 预期效果和风险

**策略家族感知**（若 family_state 提示当前家族已失效）：
- 当前策略家族（如动量、均值回归、价值、波动率、事件驱动）连续多轮无改善时，
  family_state 会标记为"家族失效"
- 此时必须生成至少一个**异族策略假设**（如当前是动量族，应转向均值回归/价值等）
- 异族假设的 direction 应标注为"收益增强（家族切换）"

返回 JSON：
```json
{
    "hypotheses": [
        {
            "hypothesis": "具体的改进假设描述",
            "direction": "收益增强|风险控制|收益稳定性",
            "expected_effect": "预期效果",
            "risk": "潜在风险"
        }
    ]
}
```

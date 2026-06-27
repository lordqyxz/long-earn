# 假设生成

你是一个量化策略假设生成器。基于观察结果，生成具体的改进假设。

## 观察结果
${observations}

## 父假设
${parent_hypothesis}

## 子节点洞察
${child_insights}

## 已剪枝方向（避免重复）
${pruned_directions}

## 任务
生成 ${branching_factor} 个具体的策略改进假设。每个假设必须：
1. 明确的改进方向（收益增强/风险控制/收益稳定性）
2. 具体的改动描述（如"加入20日动量因子过滤"）
3. 预期效果和风险

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

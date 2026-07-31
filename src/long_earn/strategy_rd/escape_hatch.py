"""Executor 逃生口 — 算子缺口同步闭环 + 失败路径选择（ADR-016 阶段 2+3）。

阶段 2：当 executor 内 develop_strategy + backtest 因算子缺失失败时，
在 executor 内部同步研发算子并重试，不中断六步循环。

阶段 3：当 executor 回测失败（非算子缺失）时，LLM 分类失败类型：
- fixable（YAML 语法、参数范围）→ refine + 重试
- directional（假设不合理、无信号）→ 直接 prune

设计原则：
- 逃生口在 executor 内部闭环，不中断六步循环主流程
- 研发委托 operator_dev 子图，内部强制 prove_causality 因果性证明
- 最多重试一次，避免无限循环
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from long_earn.backtest.operators._loader import list_operators
from long_earn.operator_dev.spec import OperatorSpec, OperatorSpecPriority
from long_earn.operator_dev.subgraph import create_operator_dev_subgraph

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService

# 算子缺失错误正则：匹配 "未知算子 'xxx'" 或 "未知算子 "xxx""
_OPERATOR_NOT_FOUND_PATTERN = re.compile(r"未知算子\s*['\"](.+?)['\"]")


@dataclass
class EscapeHatchResult:
    """逃生口执行结果。

    Attributes:
        success: 算子是否已就绪（研发成功或已存在）
        operator_name: 缺失的算子名
        error: 失败原因（success=False 时非空）
        audit_log: 审计日志（记录决策过程与理由）
    """

    success: bool
    operator_name: str = ""
    error: str = ""
    audit_log: str = ""


def detect_missing_operator(error: Exception) -> str | None:
    """从异常中提取缺失的算子名。

    匹配 backtest 引擎层 ``_resolve_op`` 抛出的 ``ValueError("未知算子 'xxx'")``，
    也兼容 ``OperatorNotFoundError`` 的消息格式。

    Args:
        error: executor 捕获的异常

    Returns:
        算子名，或 None（非算子缺失错误）
    """
    error_msg = str(error)
    match = _OPERATOR_NOT_FOUND_PATTERN.search(error_msg)
    if match:
        return match.group(1)
    return None


def attempt_operator_development(
    operator_name: str,
    strategy_yaml: str,
    hypothesis: str,
    context: RuntimeContext,
    logger: LoggerService | None = None,
) -> EscapeHatchResult:
    """同步研发缺失算子。

    1. 确认算子目录确实缺该算子（list_operators 查重）
    2. 创建 OperatorSpec 并提交 backlog
    3. 调用 operator_dev 子图（spec→审计→因果证明→register）
    4. 确认算子已注册

    Args:
        operator_name: 缺失的算子名
        strategy_yaml: 触发缺口的策略 YAML（OperatorSpec.reference_strategy 强制非空）
        hypothesis: 当前假设描述（用于 motivation 字段追溯）
        context: 运行时上下文（DI 容器，提供 operator_backlog）
        logger: 日志服务

    Returns:
        EscapeHatchResult — success=True 表示算子已就绪可重试
    """
    audit_parts: list[str] = [
        f"[逃生口] 算子缺口检测: {operator_name}",
        f"  假设: {hypothesis[:80]}",
    ]

    # 1. 确认目录确实缺该算子
    existing = list_operators()
    if operator_name in existing:
        audit_parts.append(f"  决策: 算子 {operator_name} 已在目录中，无需研发")
        if logger:
            logger.info(f"[逃生口] 算子 {operator_name} 已存在，跳过研发")
        return EscapeHatchResult(
            success=True,
            operator_name=operator_name,
            audit_log="\n".join(audit_parts),
        )

    # 2. 创建 OperatorSpec
    spec = _create_operator_spec(operator_name, strategy_yaml, hypothesis)
    audit_parts.append(f"  OperatorSpec: category={spec.category}, inputs={spec.input_fields}")

    # 3. 提交到 backlog
    backlog = context.operator_backlog
    if backlog is None:
        audit_parts.append("  决策: OperatorBacklog 未初始化，放弃研发")
        return EscapeHatchResult(
            success=False,
            operator_name=operator_name,
            error="OperatorBacklog 未初始化",
            audit_log="\n".join(audit_parts),
        )

    submitted = backlog.submit(spec)
    if not submitted and logger:
        logger.info(f"[逃生口] 算子 {operator_name} 已在 backlog 中，直接消费")

    # 4. 调用 operator_dev 子图
    try:
        op_subgraph = create_operator_dev_subgraph(context, backlog=backlog)
        op_subgraph.invoke({})
    except Exception as e:
        audit_parts.append(f"  决策: operator_dev 子图执行异常: {e}")
        if logger:
            logger.error(f"[逃生口] 算子 {operator_name} 研发子图执行失败: {e}")
        return EscapeHatchResult(
            success=False,
            operator_name=operator_name,
            error=f"算子研发子图执行失败: {e}",
            audit_log="\n".join(audit_parts),
        )

    # 5. 确认算子已注册
    updated = list_operators()
    if operator_name in updated:
        audit_parts.append(f"  决策: 算子 {operator_name} 研发成功并已注册")
        if logger:
            logger.info(f"[逃生口] 算子 {operator_name} 研发成功并已注册")
        return EscapeHatchResult(
            success=True,
            operator_name=operator_name,
            audit_log="\n".join(audit_parts),
        )

    audit_parts.append(f"  决策: 算子 {operator_name} 研发后未注册（可能被 blocked）")
    if logger:
        logger.warning(
            f"[逃生口] 算子 {operator_name} 研发后未注册（可能被 blocked）"
        )
    return EscapeHatchResult(
        success=False,
        operator_name=operator_name,
        error=f"算子 {operator_name} 研发后未注册（可能被 blocked）",
        audit_log="\n".join(audit_parts),
    )


def _create_operator_spec(
    operator_name: str,
    strategy_yaml: str,
    hypothesis: str,
) -> OperatorSpec:
    """根据算子名与上下文创建 OperatorSpec。

    算子类别推断：基于算子名前缀做简单启发式分类，默认 factor。
    """
    category = _infer_category(operator_name)
    input_fields = _infer_input_fields(operator_name)

    return OperatorSpec(
        name=operator_name,
        intent=(
            f"executor 逃生口触发：假设「{hypothesis[:80]}」"
            f"需要算子 {operator_name}"
        ),
        input_fields=input_fields,
        category=category,
        expected_output="每行 float",
        reference_strategy=strategy_yaml[:500],
        motivation=(
            f"executor 检测到算子缺失，LLM 生成的策略 YAML"
            f"引用用了不存在的算子 {operator_name}"
        ),
        priority=OperatorSpecPriority.HIGH,
    )


def _infer_category(operator_name: str) -> str:
    """根据算子名推断类别。

    启发式规则：
    - 含 filter/stop/profit/trend → filter
    - 含 rank/top/bottom → rank
    - 含 ma/macd/rsi/boll/band → technical
    - 其余 → factor
    """
    name_lower = operator_name.lower()
    if any(kw in name_lower for kw in ("filter", "stop", "profit", "trend")):
        return "filter"
    if any(kw in name_lower for kw in ("rank", "top", "bottom")):
        return "rank"
    if any(kw in name_lower for kw in ("ma", "macd", "rsi", "boll", "band")):
        return "technical"
    return "factor"


def _infer_input_fields(operator_name: str) -> list[str]:
    """根据算子名推断输入字段。

    含 volume/vol → 需要 close + volume；其余默认 close。
    """
    name_lower = operator_name.lower()
    if "vol" in name_lower:
        return ["close", "volume"]
    return ["close"]


def escape_hatch_with_retry(  # noqa: PLR0913
    error: Exception,
    strategy_yaml: str,
    optimized: dict[str, Any],
    hypothesis: str,
    context: RuntimeContext,
    develop_func: Any,
    backtest_func: Any,
    logger: LoggerService | None = None,
) -> dict[str, Any]:
    """逃生口 + 重试 develop→backtest（ADR-016 阶段 2）。

    非算子缺失错误 → 直接返回 ``{"error": str(error)}``
    算子缺失 + 研发成功 + 重试成功 → 返回
        ``{"strategy_yaml": ..., "backtest_result": ..., "escape_hatch_triggered": True}``
    算子缺失 + 研发失败 → 返回
        ``{"error": ..., "escape_hatch_triggered": True}``
    算子缺失 + 研发成功 + 重试失败 → 返回
        ``{"error": ..., "escape_hatch_triggered": True}``

    Args:
        error: executor 捕获的异常
        strategy_yaml: 触发异常的策略 YAML
        optimized: 优化后的策略 dict（重试 develop 用）
        hypothesis: 当前假设描述
        context: 运行时上下文
        develop_func: 可调用，接受 optimized dict，返回 YAML str
        backtest_func: 可调用，接受 strategy_yaml str，返回 result dict
        logger: 日志服务

    Returns:
        结果 dict，调用方据此判断是重试成功还是失败
    """
    hatch_result = apply_escape_hatch_on_error(
        error=error,
        strategy_yaml=strategy_yaml,
        hypothesis=hypothesis,
        context=context,
        logger=logger,
    )

    if hatch_result is not None:
        # 非算子缺失错误，或算子研发失败
        return hatch_result

    # 逃生口成功，算子已就绪，重试 develop + backtest
    try:
        new_yaml = develop_func(optimized)
        new_result = backtest_func(new_yaml)
        if logger:
            logger.info("[逃生口] 重试 develop + backtest 成功")
        return {
            "strategy_yaml": new_yaml,
            "backtest_result": new_result,
            "escape_hatch_triggered": True,
        }
    except Exception as retry_error:
        if logger:
            logger.error(
                f"[逃生口] 重试 develop + backtest 失败: {retry_error}"
            )
        return {
            "error": f"逃生口重试失败: {retry_error}",
            "escape_hatch_triggered": True,
        }


def apply_escape_hatch_on_error(
    error: Exception,
    strategy_yaml: str,
    hypothesis: str,
    context: RuntimeContext,
    logger: LoggerService | None = None,
) -> dict[str, Any] | None:
    """在 executor 捕获异常后应用逃生口。

    如果异常是算子缺失错误，尝试研发算子。研发成功返回 None（表示算子已就绪，
    调用方应重试 develop + backtest）；研发失败或非算子缺失错误返回错误 dict。

    Args:
        error: executor 捕获的异常
        strategy_yaml: 触发异常的策略 YAML
        hypothesis: 当前假设描述
        context: 运行时上下文
        logger: 日志服务

    Returns:
        None — 算子已就绪，调用方应重试
        dict — 错误结果（含 error + escape_hatch_audit），调用方应直接使用
    """
    missing_op = detect_missing_operator(error)
    if missing_op is None:
        # 非算子缺失错误，不触发逃生口
        return {"error": str(error), "escape_hatch_triggered": False}

    if logger:
        logger.info(
            f"[逃生口] 检测到算子缺失: {missing_op}，"
            f"触发同步研发"
        )

    result = attempt_operator_development(
        operator_name=missing_op,
        strategy_yaml=strategy_yaml,
        hypothesis=hypothesis,
        context=context,
        logger=logger,
    )

    if result.success:
        # 算子已就绪，调用方应重试
        return None

    # 研发失败，返回错误
    return {
        "error": f"算子 {missing_op} 研发失败: {result.error}",
        "escape_hatch_triggered": True,
        "escape_hatch_audit": result.audit_log,
    }


# ── 阶段 3：失败路径选择逃生口 ──────────────────────────────────────

# LLM 分类失败类型的 prompt
_FAILURE_CLASSIFICATION_PROMPT = """你是量化交易策略研发助手。executor 回测失败，请判断失败类型。

错误信息：{error_message}
假设描述：{hypothesis}

失败类型：
- fixable: 可修复错误（YAML 语法错误、参数范围错误、算子参数不匹配等临时性问题）
- directional: 方向性失败（假设本身不合理、因子在训练集无信号、策略逻辑根本性错误）

只返回 fixable 或 directional，不要解释。"""


def classify_failure_type(
    error: Exception,
    hypothesis: str,
    llm_service: Any,
    logger: LoggerService | None = None,
) -> str:
    """LLM 分类 executor 失败类型（ADR-016 阶段 3）。

    Args:
        error: executor 捕获的异常
        hypothesis: 当前假设描述
        llm_service: LLM 服务（需有 invoke 方法）
        logger: 日志服务

    Returns:
        "fixable" 或 "directional"，LLM 调用失败时默认 "fixable"
    """
    prompt = _FAILURE_CLASSIFICATION_PROMPT.format(
        error_message=str(error)[:500],
        hypothesis=hypothesis[:200],
    )

    try:
        response = llm_service.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        content_lower = content.strip().lower()

        if "directional" in content_lower:
            if logger:
                logger.info("[逃生口-失败路径] LLM 分类: directional")
            return "directional"

        if logger:
            logger.info("[逃生口-失败路径] LLM 分类: fixable")
        return "fixable"
    except Exception as e:
        if logger:
            logger.warning(f"[逃生口-失败路径] LLM 分类失败，默认 fixable: {e}")
        return "fixable"


def escape_hatch_failure_path(  # noqa: PLR0913
    error: Exception,
    strategy_yaml: str,
    optimized: dict[str, Any],
    hypothesis: str,
    llm_service: Any,
    refine_func: Any,
    backtest_func: Any,
    logger: LoggerService | None = None,
) -> dict[str, Any]:
    """失败路径逃生口 — LLM 分类后选择 refine 或 prune（ADR-016 阶段 3）。

    非算子缺失错误 → LLM 分类失败类型
    fixable → refine_code + 重试 backtest
    directional → 返回 pruned 结果

    Args:
        error: executor 捕获的异常（已确认非算子缺失）
        strategy_yaml: 失败的策略 YAML
        optimized: 优化后的策略 dict（refine 用）
        hypothesis: 当前假设描述
        llm_service: LLM 服务
        refine_func: 可调用，签名 (strategy, error_message, failed_code) -> yaml_str
        backtest_func: 可调用，签名 (yaml_str) -> result_dict
        logger: 日志服务

    Returns:
        逃生口处理结果 dict:
        - fixable + refine 成功: ``{"strategy_yaml": ..., "backtest_result": ..., "escape_hatch_triggered": True, "failure_path": "fixable"}``
        - fixable + refine 失败: ``{"error": ..., "escape_hatch_triggered": True, "failure_path": "fixable"}``
        - directional: ``{"error": ..., "escape_hatch_triggered": True, "failure_path": "directional"}``
    """
    failure_type = classify_failure_type(
        error=error,
        hypothesis=hypothesis,
        llm_service=llm_service,
        logger=logger,
    )

    audit = f"[逃生口-失败路径] 错误: {str(error)[:200]}\n  分类: {failure_type}"

    if failure_type == "directional":
        return {
            "error": f"方向性失败: {error}",
            "escape_hatch_triggered": True,
            "failure_path": "directional",
            "escape_hatch_audit": audit,
        }

    # fixable → refine + 重试
    try:
        refined_yaml = refine_func(optimized, str(error), strategy_yaml)
        backtest_result = backtest_func(refined_yaml)
        if logger:
            logger.info("[逃生口-失败路径] refine + 重试 backtest 成功")
        return {
            "strategy_yaml": refined_yaml,
            "backtest_result": backtest_result,
            "escape_hatch_triggered": True,
            "failure_path": "fixable",
            "escape_hatch_audit": audit,
        }
    except Exception as retry_error:
        if logger:
            logger.error(f"[逃生口-失败路径] refine 重试失败: {retry_error}")
        return {
            "error": f"refine 重试失败: {retry_error}",
            "escape_hatch_triggered": True,
            "failure_path": "fixable",
            "escape_hatch_audit": audit,
        }

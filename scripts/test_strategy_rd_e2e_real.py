"""端到端验证：策略研发子图迭代循环 + 算子研发链路（真实 LLM + 真实 miniQMT 数据源）

验证目标（对应"策略改进迭代 + 因子研发功能"）：
  1. 策略研发子图完整链路能跑通：
     init → 检索 → research(LLM) → develop(LLM生成YAML) → backtest(真实引擎+真实数据)
     → reflection(ToT) → save_experience → supervisor → optimize(优化迭代)
  2. 算子研发子图链路能跑通：
     pick_task → spec_review → implement(LLM) → test_validate(因果性证明) → register

运行前提：
  - ollama 已拉取模型（LLM_TYPE/LLM_MODEL 配置）
  - miniQMT 客户端在运行（真实数据源）
  - 为加速回测，临时把回测日期区间缩短为 3 个月、迭代次数限制为 1

用法：
  uv run python scripts/test_strategy_rd_e2e_real.py
"""

from __future__ import annotations

import sys
import traceback

# 强制 stdout 无缓冲：后台运行时输出立即写日志文件，便于实时追踪进度
try:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

# 临时缩短回测区间 + 限制迭代，加速端到端验证（在 import config 前通过环境变量覆盖）
import os

os.environ.setdefault("BACKTEST_START_DATE", "2024-01-01")
os.environ.setdefault("BACKTEST_END_DATE", "2024-03-31")
os.environ.setdefault("MAX_ITERATIONS", "1")

from long_earn.config import AppConfig, RuntimeContext
from long_earn.context_init import create_runtime_context
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph


def _banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _step(msg: str) -> None:
    print(f"\n▶ {msg}")


def test_strategy_rd_subgraph(context: RuntimeContext) -> dict:
    """测试 1：策略研发子图完整迭代循环（真实 LLM + 真实数据源）"""
    _banner("测试 1：策略研发子图迭代循环（多因子策略）")
    config = context.config
    print(f"LLM: {config.llm_type} / {config.llm_model} @ {config.llm_base_url}")
    print(f"回测区间: {config.backtest_start_date} ~ {config.backtest_end_date}")
    print(f"最大迭代: {config.max_iterations}")

    _step("编译策略研发子图")
    subgraph = create_strategy_rd_subgraph(context)
    print("  子图节点:", list(subgraph.get_graph().nodes.keys()))

    _step("以多因子策略查询驱动子图（真实 LLM 研究 + 开发）")
    query = "研发一个多因子选股策略：结合动量因子（近20日涨幅）和价值因子（低估值），筛选并等权配置"
    print(f"  查询: {query}")

    try:
        result = subgraph.invoke({"query": query})
    except Exception:
        print("  ✗ 子图执行抛异常:")
        traceback.print_exc()
        return {"ok": False, "error": "subgraph invoke raised"}

    _step("子图执行完成，检查关键状态")
    _print_state_summary(result)

    ok = _validate_strategy_rd_result(result)
    return {"ok": ok, "result": result}


def _print_state_summary(state: dict) -> None:
    """打印子图最终状态的关键字段"""
    strategy = state.get("strategy") or {}
    print(f"  strategy_name      : {strategy.get('strategy_name', '(无)')}")
    desc = (strategy.get("description") or "").strip()
    print(f"  strategy_description: {desc[:120]}{'...' if len(desc) > 120 else ''}")
    yaml = state.get("strategy_yaml") or ""
    print(f"  strategy_yaml 长度 : {len(yaml)} 字符")
    if yaml:
        print(f"  strategy_yaml 预览 : {yaml.splitlines()[0][:100] if yaml.splitlines() else '(空)'}")

    bt = state.get("backtest_result") or {}
    if bt.get("error"):
        print(f"  backtest           : 失败 [{bt.get('error_category')}] {str(bt.get('error'))[:100]}")
    else:
        print(
            f"  backtest           : total_return={bt.get('total_return')}, "
            f"sharpe={bt.get('sharpe_ratio')}, max_dd={bt.get('max_drawdown')}, "
            f"days={bt.get('trading_days')}"
        )

    refl = (state.get("reflection") or "").strip()
    print(f"  reflection         : {len(refl)} 字符" + (f" | {refl[:100]}..." if refl else ""))
    print(f"  experience_saved   : {state.get('experience_saved')}")
    print(f"  should_continue    : {state.get('should_continue')}")
    print(f"  iteration          : {state.get('iteration')}")

    opt_yaml = state.get("optimized_strategy_yaml") or ""
    print(f"  optimized_yaml 长度: {len(opt_yaml)} 字符")
    if opt_yaml:
        print(f"  optimized 预览     : {opt_yaml.splitlines()[0][:100]}")


def _validate_strategy_rd_result(state: dict) -> bool:
    """验证策略研发链路是否真正执行了各环节（不强求回测盈利，只验证流程贯通）"""
    issues = []
    if not (state.get("strategy") or {}).get("description"):
        issues.append("research 节点未产出策略描述")
    if not state.get("strategy_yaml"):
        issues.append("develop 节点未产出 strategy_yaml")
    if not state.get("backtest_result"):
        issues.append("backtest 节点未执行")
    if not state.get("reflection"):
        issues.append("reflection 节点未产出反思")
    if state.get("experience_saved") is None:
        issues.append("save_experience 节点未执行")

    if issues:
        print("\n  ✗ 链路验证失败:")
        for i in issues:
            print(f"    - {i}")
        return False
    print("\n  ✓ 策略研发链路贯通：research→develop→backtest→reflection→save_experience 均执行")
    return True


def test_operator_dev(context: RuntimeContext) -> dict:
    """测试 2：算子研发子图链路（pick→实现→因果证明→注册）"""
    _banner("测试 2：算子研发子图（因子研发 + 因果性证明）")

    from long_earn.operator_dev.backlog import OperatorBacklog
    from long_earn.operator_dev.spec import OperatorSpec, OperatorSpecPriority
    from long_earn.operator_dev.subgraph import create_operator_dev_subgraph

    _step("向 backlog 投递一个因子算子缺口")
    backlog = OperatorBacklog()
    spec = OperatorSpec(
        name="momentum_20d",
        category="factor",
        intent="20日动量因子：计算过去20日收盘价收益率，用于动量选股",
        input_fields=["close", "timestamp"],
        expected_output="每行 float，过去20日收益率",
        reference_strategy="动量选股策略：按20日动量排序取前10",
        motivation="现有目录无现成的动量因子算子",
        priority=OperatorSpecPriority.NORMAL,
    )
    backlog.submit(spec)
    print(f"  投递算子: {spec.name} ({spec.category})")

    _step("编译算子研发子图（注入真实 context + 真实 backlog）")
    subgraph = create_operator_dev_subgraph(context, backlog=backlog)
    print("  子图节点:", list(subgraph.get_graph().nodes.keys()))

    _step("执行：pick_task → spec_review → implement(LLM) → test_validate(因果证明) → register")
    try:
        result = subgraph.invoke({})
    except Exception:
        print("  ✗ 算子研发子图执行抛异常:")
        traceback.print_exc()
        return {"ok": False, "error": "operator_dev invoke raised"}

    _step("检查算子研发结果")
    results = result.get("results", [])
    registered = result.get("registered_names", [])
    print(f"  处理结果条目: {len(results)}")
    for r in results:
        print(f"    - {r.get('name')}: {r.get('status')} | {str(r.get('detail'))[:80]}")
    print(f"  成功注册算子: {registered}")

    if registered:
        print("\n  ✓ 算子研发链路贯通：pick→实现→因果证明→注册成功")
        # 验证算子确实进入了全局注册表
        from long_earn.backtest.operators import OPERATOR_REGISTRY

        in_registry = all(n in OPERATOR_REGISTRY for n in registered)
        print(f"  算子已进入全局注册表: {in_registry}")
        return {"ok": True, "result": result}

    print("\n  △ 算子研发链路执行完毕但未注册成功（可能 LLM 生成的代码未通过因果证明）")
    return {"ok": False, "result": result, "note": "链路执行但未注册"}


def main() -> int:
    _banner("端到端验证：策略改进迭代 + 因子研发功能（真实 LLM + 真实数据源）")

    _step("初始化运行时上下文（真实 LLM + miniQMT 数据源）")
    try:
        context = create_runtime_context(AppConfig.from_env())
        context.require_memory().initialize()
        print(f"  RuntimeContext 就绪")
        print(f"  data_provider: {type(context.data_provider).__name__ if context.data_provider else None}")
    except Exception:
        print("  ✗ 上下文初始化失败:")
        traceback.print_exc()
        return 2

    # 测试 LLM 可用性（前置检查）
    _step("LLM 可用性探活")
    try:
        resp = context.llm_service.invoke("回复两个字：可用", format="")
        print(f"  LLM 响应: {repr(resp.content)[:60] if hasattr(resp, 'content') else resp}")
    except Exception as e:
        print(f"  ✗ LLM 不可用: {type(e).__name__}: {str(e)[:150]}")
        print("  请先 ollama pull 模型并配置 .env 的 LLM_MODEL")
        return 3

    # 探活数据源
    _step("miniQMT 数据源探活")
    try:
        from long_earn.backtest.data.miniqmt_provider import MiniQmtClient

        avail = MiniQmtClient.get().is_available
        print(f"  xtquant is_available: {avail}")
        if not avail:
            print("  ✗ 数据源不可用，回测将失败")
            return 4
    except Exception as e:
        print(f"  ✗ 数据源探活异常: {e}")
        return 4

    rd_ok = test_strategy_rd_subgraph(context)
    op_ok = test_operator_dev(context)

    _banner("总结")
    print(f"  策略研发子图迭代循环 : {'✓ 通过' if rd_ok['ok'] else '✗ 未通过'}")
    print(f"  算子研发链路         : {'✓ 通过' if op_ok['ok'] else '✗ 未通过'}")
    return 0 if (rd_ok["ok"] and op_ok["ok"]) else 1


if __name__ == "__main__":
    sys.exit(main())

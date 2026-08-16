#!/usr/bin/env python3
"""用 LangGraph checkpoint 断点测试记忆系统（ADR-007 SubstanceStore）。

方案：构造一个最小策略研发子图，手动注入完整 state（含策略 YAML + 回测结果），
用 checkpoint 在 save_experience 节点前后断点，精确验证：
1. 节点执行前：state 含完整策略数据
2. 节点执行后：experience_saved=True，DuckDB 落盘
3. 往返验证：search_experience 检索到刚保存的经验
4. 知识检索：init_dir 知识加载到记忆库

不依赖完整 LLM 子图运行（避免耗时 + Ollama 限制），只测记忆系统节点。

用法:
    uv run python scripts/debug_memory_checkpoint.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="ERROR")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.strategy_rd.state import State  # noqa: E402

# 直接导入 _save_experience_node 的实现逻辑（不依赖完整子图）
from long_earn.strategy_rd.subgraph import _save_experience_node  # noqa: E402


def _make_test_state() -> dict:
    """构造一个完整策略 state（模拟子图跑到 save_experience 前的状态）。"""
    return {
        "query": "研究一个基于ROE和净利润增长的选股策略",
        "strategy_name": "TestRoeStrategy",
        "design_rationale": "选择 ROE>0.12 且净利润同比增长>20% 的股票",
        "strategy_yaml": (
            "strategy:\n"
            "  name: TestRoeStrategy\n"
            "  description: ROE 选股策略\n"
            "  universe:\n"
            "    type: csi300\n"
            "    rebalance_freq: 20D\n"
            "  signals:\n"
            "    - type: filter\n"
            "      condition: roe > 0.12\n"
            "    - type: rank\n"
            "      by: roe\n"
            "      ascending: false\n"
            "      top: 10\n"
            "  weights:\n"
            "    method: equal\n"
        ),
        "backtest_result": {
            "total_return": 0.15,
            "annual_return": 0.30,
            "sharpe_ratio": 1.2,
            "max_drawdown": -0.10,
            "win_rate": 0.55,
            "trading_days": 120,
            "volatility": 0.18,
            "strategy_diagnostics": {"trade_count": 45},
        },
        "reflection": "策略表现良好，ROE 筛选有效，但可考虑加入动量因子增强收益。",
        "improvement_suggestions": ["加入 20 日动量因子", "调整 ROE 阈值至 0.15"],
    }


def main() -> None:  # noqa: PLR0915
    with tempfile.TemporaryDirectory(prefix="long_earn_mem_") as tmpdir:
        tmp_path = Path(tmpdir)
        memory_db = tmp_path / "substances.duckdb"
        init_dir = tmp_path / "init"
        init_dir.mkdir()
        (init_dir / "test_knowledge.md").write_text(
            "# 测试知识\nROE（净资产收益率）是衡量企业盈利能力的核心指标。\n"
            "动量因子在A股长周期可能不稳健，需结合基本面因子。\n",
            encoding="utf-8",
        )

        config = AppConfig.from_env()
        config.memory_path = str(memory_db)
        config.init_dir = str(init_dir)

        print("=" * 70)
        print("checkpoint 记忆系统测试")
        print("=" * 70)
        print(f"  临时记忆库: {memory_db}")
        print(f"  init_dir: {init_dir}")

        ctx = initialize_context(config)
        memory = ctx.require_memory()

        # ── 测试 1：initialize 后知识检索 ──
        print("\n[测试 1] initialize 后知识检索（init_dir → DuckDB）")
        results = memory.search("ROE 盈利能力", k=3)
        print(f"  知识检索结果数: {len(results)}")
        db_exists = memory_db.exists()
        print(f"  DuckDB 文件存在: {db_exists}")
        test1_pass = len(results) > 0 and db_exists

        # ── 测试 2：用 checkpoint 在 save_experience 节点断点 ──
        print("\n[测试 2] checkpoint 断点 save_experience 节点")

        # 构造最小子图：只有 save_experience 节点
        from long_earn.strategy_rd.agents.strategy_develop_agent import (  # noqa: PLC0415
            StrategyDevelopAgent,
        )

        develop_agent = StrategyDevelopAgent(context=ctx)
        checkpointer = MemorySaver()

        # 直接调用 _save_experience_node（模拟 LangGraph 节点执行）
        # checkpoint 价值：我们可以先检查输入 state，再执行，再检查输出
        test_state = _make_test_state()

        # 断点前：检查输入 state 完整性
        print("  [断点前] 输入 state 检查:")
        print(f"    strategy_name: {test_state['strategy_name']}")
        print(f"    strategy_yaml 长度: {len(test_state['strategy_yaml'])}")
        print(f"    backtest_result total_return: {test_state['backtest_result']['total_return']}")
        print(f"    reflection 长度: {len(test_state['reflection'])}")
        input_valid = (
            len(test_state["strategy_yaml"]) > 0
            and test_state["backtest_result"]["total_return"] is not None
        )
        print(f"    输入 state 完整: {input_valid}")

        # 执行 save_experience 节点
        print("  [执行] _save_experience_node(...)")
        output = _save_experience_node(
            test_state,
            memory=memory,
            develop_agent=develop_agent,
            logger=ctx.logger,
        )
        experience_saved = output.get("experience_saved", False)
        print(f"  [断点后] experience_saved: {experience_saved}")
        test2_pass = bool(experience_saved)

        # ── 测试 3：search_experience 往返验证 ──
        print("\n[测试 3] search_experience 往返验证")
        experiences = memory.search_experience(query="ROE 选股", k=5)
        print(f"  search_experience 结果数: {len(experiences)}")
        if experiences:
            exp = experiences[0]
            print(f"    name: {exp.name}")
            print(f"    rationale 长度: {len(exp.rationale)}")
            print(f"    code 长度: {len(exp.code)}")
            metrics_keys = list(exp.metrics.keys()) if exp.metrics else []
            print(f"    metrics keys: {metrics_keys}")
            print(f"    reflection 长度: {len(exp.reflection)}")
        test3_pass = len(experiences) > 0

        # ── 测试 4：DuckDB 落盘验证 ──
        print("\n[测试 4] DuckDB 落盘验证")
        from long_earn.substance.persistence import load_all  # noqa: PLC0415

        substances = load_all(memory_db)
        print(f"  DuckDB 物质总数: {len(substances)}")
        forms: dict[str, int] = {}
        for s in substances:
            form = s.form.value if hasattr(s.form, "value") else str(s.form)
            forms[form] = forms.get(form, 0) + 1
        print(f"  按形态分布: {forms}")
        strategy_count = forms.get("strategy", 0)
        knowledge_count = forms.get("knowledge", 0)
        print(f"  STRATEGY 物质: {strategy_count}")
        print(f"  KNOWLEDGE 物质: {knowledge_count}")
        test4_pass = strategy_count > 0 and knowledge_count > 0

        # ── 测试 5：checkpoint MemorySaver 状态快照 ──
        print("\n[测试 5] checkpoint MemorySaver 状态快照")
        # 用 MemorySaver 构造一个真正的 LangGraph 子图验证 checkpoint 机制
        mini_graph = StateGraph(State)
        mini_graph.add_node(
            "save_experience",
            lambda state: _save_experience_node(
                state, memory=memory, develop_agent=develop_agent, logger=ctx.logger
            ),
        )
        mini_graph.add_edge(START, "save_experience")
        mini_graph.add_edge("save_experience", END)
        # 用 interrupt_before 在 save_experience 前暂停
        compiled2 = mini_graph.compile(
            checkpointer=checkpointer,
            interrupt_before=["save_experience"],
        )
        thread2 = {"configurable": {"thread_id": "mem-interrupt"}}
        compiled2.invoke(test_state, config=thread2)
        snapshot = compiled2.get_state(thread2)
        interrupted = bool(snapshot.next)
        print(f"  interrupt_before 生效（暂停在 save_experience）: {interrupted}")
        print(f"  快照 strategy_name: {snapshot.values.get('strategy_name')}")
        # 继续
        result_final = compiled2.invoke(None, config=thread2)
        print(f"  继续执行后 experience_saved: {result_final.get('experience_saved')}")
        test5_pass = interrupted and result_final.get("experience_saved")

        # ── 最终结论 ──
        print("\n" + "=" * 70)
        print("记忆系统测试结论")
        print("=" * 70)

        checks = {
            "测试1: initialize 知识检索 + DuckDB 创建": test1_pass,
            "测试2: save_experience 节点执行成功": test2_pass,
            "测试3: search_experience 往返检索": test3_pass,
            "测试4: DuckDB 落盘（STRATEGY+KNOWLEDGE）": test4_pass,
            "测试5: checkpoint interrupt + resume": test5_pass,
        }

        all_pass = True
        for name, passed in checks.items():
            status = "✅" if passed else "❌"
            print(f"  {status} {name}")
            if not passed:
                all_pass = False

        print()
        if all_pass:
            print("  ✅ 记忆系统运行正常：知识加载 + 经验保存 + DuckDB 持久化 + 往返检索 + checkpoint 全通过")
        else:
            print("  ❌ 记忆系统存在问题，见上方失败项")


if __name__ == "__main__":
    main()

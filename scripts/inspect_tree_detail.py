"""深入查看最近一次假设树的节点详情（sharpe / 决策 / 假设）。"""
from __future__ import annotations

import json
from pathlib import Path

from long_earn.core.storage import hypothesis_tree_dir


def main() -> None:
    tree_dir = Path(hypothesis_tree_dir())
    files = sorted(tree_dir.glob("*.json"))
    if not files:
        return
    # 取最大的文件（通常是真实运行）
    f = max(files, key=lambda p: p.stat().st_size)
    print(f"=== {f.name} ===")
    data = json.loads(f.read_text(encoding="utf-8"))
    print(f"run_id: {data.get('run_id')}")
    print(f"root_id: {data.get('root_id')}")
    print(f"current_best_id: {data.get('current_best_id')}")

    nodes = data.get("nodes", {})
    print(f"\n节点总数: {len(nodes)}")

    # 收集每个节点的核心字段
    for nid, node in nodes.items():
        if not isinstance(node, dict):
            continue
        hypothesis = str(node.get("hypothesis", ""))[:80]
        status = node.get("status", "?")
        sharpe = node.get("sharpe", node.get("metrics", {}).get("sharpe_ratio", "?"))
        oos_sharpe = node.get("oos_sharpe", "?")
        decision = node.get("decision", "?")
        depth = node.get("depth", "?")
        parent = node.get("parent_id", "-")
        print(f"\n[{nid}] depth={depth} parent={parent} status={status}")
        print(f"  hypothesis: {hypothesis}")
        print(f"  sharpe={sharpe} oos_sharpe={oos_sharpe} decision={decision}")

    # 最佳节点详情
    best_id = data.get("current_best_id")
    if best_id and best_id in nodes:
        best = nodes[best_id]
        print(f"\n=== 最佳节点 {best_id} 详情 ===")
        # 找 strategy yaml
        strat = best.get("strategy_yaml") or best.get("strategy")
        if isinstance(strat, str):
            print(strat[:1500])
        else:
            print(f"  keys: {list(best.keys())[:20]}")


if __name__ == "__main__":
    main()

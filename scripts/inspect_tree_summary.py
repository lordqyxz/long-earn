"""查看假设树最近一次运行的摘要（best/oos/iteration/决策）。"""
from __future__ import annotations

import json
from pathlib import Path

from long_earn.core.storage import hypothesis_tree_dir


def main() -> None:
    tree_dir = Path(hypothesis_tree_dir())
    files = sorted(tree_dir.glob("*.json"))
    if not files:
        print("(无假设树文件)")
        return
    # 取最大的 2 个文件
    for f in files[-2:]:
        print(f"\n=== {f.name} ({f.stat().st_size} bytes) ===")
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  解析失败: {e}")
            continue
        # 顶层字段
        print(f"  顶层 keys: {list(data.keys())[:15]}")
        # 基本字段
        for k in ("iteration", "status", "best_oos_sharpe", "best_sharpe", "idea"):
            if k in data:
                v = data[k]
                if isinstance(v, str) and len(v) > 200:
                    v = v[:200] + "..."
                print(f"  {k}: {v}")
        # 树节点摘要
        nodes = data.get("nodes", data.get("tree", {}))
        if isinstance(nodes, dict):
            keys_preview = list(nodes.keys())[:10] if nodes else "(空)"
            print(f"  nodes/tree 字典 keys: {keys_preview}")
        elif isinstance(nodes, list):
            print(f"  nodes/list 长度: {len(nodes)}")
            if nodes:
                print(f"  首节点 keys: {list(nodes[0].keys())[:10] if isinstance(nodes[0], dict) else type(nodes[0])}")


if __name__ == "__main__":
    main()

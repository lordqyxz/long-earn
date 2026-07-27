"""快速查看策略研发历史与最佳策略指标。"""
from __future__ import annotations

import json
from pathlib import Path

from long_earn.core.storage import (
    best_strategy_path,
    hypothesis_tree_dir,
    strategy_results_path,
)


def main() -> None:
    # 1. 最佳策略
    best_path = best_strategy_path()
    print(f"\n=== 最佳策略文件: {best_path} ===")
    if Path(best_path).exists():
        content = Path(best_path).read_text(encoding="utf-8")
        # 只打印前 30 行
        for line in content.splitlines()[:30]:
            print(line)

    # 2. 历史研发结果
    results_path = strategy_results_path()
    print(f"\n=== 研发结果汇总: {results_path} ===")
    if not Path(results_path).exists():
        print("  (无)")
        return
    data = json.loads(Path(results_path).read_text(encoding="utf-8"))
    print(f"  top-level keys: {list(data.keys())[:10]}")

    runs = data.get("runs", [])
    print(f"  总运行数: {len(runs)}")
    if runs:
        print("  最近 5 次：")
        for r in runs[-5:]:
            ts = str(r.get("timestamp", "?"))[:19]
            name = str(r.get("strategy_name", "?"))[:50]
            metrics = r.get("metrics", {}) or {}
            sharpe = metrics.get("sharpe_ratio", "?")
            ret = metrics.get("total_return", "?")
            dd = metrics.get("max_drawdown", "?")
            print(f"    {ts} | {name} | sharpe={sharpe} ret={ret} dd={dd}")

    # 3. 假设树
    tree_dir = hypothesis_tree_dir()
    print(f"\n=== 假设树目录: {tree_dir} ===")
    p = Path(tree_dir)
    if p.exists():
        files = sorted(p.glob("*.json"))
        print(f"  总树文件数: {len(files)}")
        for f in files[-5:]:
            print(f"    {f.name} ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

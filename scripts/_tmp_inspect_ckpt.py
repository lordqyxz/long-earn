"""临时脚本：读取 ToG checkpoint 中的策略 YAML 与回测诊断。"""
import json
import sqlite3
import sys

from long_earn.core.storage import checkpoint_db_path


def main() -> None:
    thread_id = sys.argv[1] if len(sys.argv) > 1 else "tog-20260803-v3"
    p = checkpoint_db_path()
    print(f"checkpoint: {p} exists={p.exists()}")
    if not p.exists():
        return
    conn = sqlite3.connect(str(p))
    rows = conn.execute(
        "SELECT thread_id, checkpoint_id, checkpoint FROM checkpoints "
        "WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 3",
        (thread_id,),
    ).fetchall()
    print(f"rows for {thread_id}: {len(rows)}")
    if not rows:
        threads = conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE '%tog%'"
        ).fetchall()
        print("available tog threads:", [t[0] for t in threads])
        return
    for thread_id, ckpt_id, blob in rows:
        print(f"\n=== {thread_id} / {ckpt_id} ===")
        cp = json.loads(blob)
        ch = cp.get("channel_values", {})
        print("channel keys:", sorted(ch.keys()))
        for k in (
            "strategy_yaml",
            "optimized_strategy_yaml",
            "backtest_result",
            "messages",
        ):
            v = ch.get(k)
            if not v:
                continue
            print(f"\n--- {k} ---")
            if k == "backtest_result" and isinstance(v, dict):
                diag = v.get("strategy_diagnostics", {})
                print("metrics_unreliable:", v.get("metrics_unreliable"))
                print("failed_step_labels:", diag.get("failed_step_labels"))
                sf = diag.get("step_failures", [])
                if sf:
                    print("first step_failure:", sf[0])
                    print("step_failure count:", len(sf))
                print("trade_count:", diag.get("trade_count"))
                print("total_return:", v.get("total_return"))
            else:
                s = str(v)
                print(s[:3000])
                if len(s) > 3000:
                    print(f"... ({len(s)} chars total)")


if __name__ == "__main__":
    main()

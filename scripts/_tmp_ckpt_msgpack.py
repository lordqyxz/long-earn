"""读取 LangGraph SqliteSaver checkpoint（msgpack）。"""
import sqlite3
import sys

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from long_earn.core.storage import checkpoint_db_path


def main() -> None:
    thread_id = sys.argv[1] if len(sys.argv) > 1 else "tog-20260803-v3"
    p = checkpoint_db_path()
    conn = sqlite3.connect(str(p))
    rows = conn.execute(
        "SELECT checkpoint_id, checkpoint, metadata FROM checkpoints "
        "WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1",
        (thread_id,),
    ).fetchall()
    if not rows:
        print("no rows")
        return
    ckpt_id, blob, meta_blob = rows[0]
    print("checkpoint_id", ckpt_id)
    serde = JsonPlusSerializer()
    cp = serde.loads(blob)
    meta = serde.loads(meta_blob) if meta_blob else {}
    print("meta keys", meta.keys() if isinstance(meta, dict) else type(meta))
    ch = cp.get("channel_values", {}) if isinstance(cp, dict) else {}
    print("channel keys", sorted(ch.keys()) if isinstance(ch, dict) else type(ch))
    for k, v in (ch.items() if isinstance(ch, dict) else []):
        if k in {"messages"}:
            continue
        s = str(v)
        if len(s) > 20:
            print(f"\n=== {k} ===")
            print(s[:4000])


if __name__ == "__main__":
    main()

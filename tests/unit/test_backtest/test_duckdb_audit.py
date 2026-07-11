"""DuckDB 审计 Provider 安全测试

覆盖：
- P2-13：query_events key 白名单校验（防 SQL 注入）
- P2-14：DuckDB 单连接线程安全（并发写不崩溃）
"""

import threading
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from long_earn.backtest.domain.interfaces import AuditRecord
from long_earn.backtest.engine.audit import DuckDBAuditProvider


def _make_record(
    run_id: str = "test-run",
    trace_id: str = "trace-0",
    event_type: str = "MARKET_DATA",
    parent_id: str | None = None,
) -> AuditRecord:
    """构造测试用审计记录"""
    return AuditRecord(
        run_id=run_id,
        timestamp=datetime.now(),
        event_type=event_type,
        trace_id=trace_id,
        parent_id=parent_id,
        component="engine",
        status="OK",
        payload={"bar": "2024-01-01"},
        latency_ms=0.5,
    )


class TestQueryEventsWhitelist(unittest.TestCase):
    """P2-13：query_events key 白名单校验"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "audit.duckdb"
        self.provider = DuckDBAuditProvider(db_path=self.db_path)
        self.provider.log_event(_make_record())

    def tearDown(self) -> None:
        self.provider.close()
        self._tmp.cleanup()

    def test_whitelisted_key_accepted(self) -> None:
        """白名单内的字段应正常过滤"""
        records = self.provider.query_events("test-run", {"event_type": "MARKET_DATA"})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].event_type, "MARKET_DATA")

    def test_non_whitelisted_key_rejected(self) -> None:
        """非白名单 key 应抛 ValueError，不拼接到 SQL"""
        with self.assertRaises(ValueError) as ctx:
            self.provider.query_events("test-run", {"1=1 OR 1": "x"})
        self.assertIn("非白名单", str(ctx.exception))

    def test_sql_injection_attempt_rejected(self) -> None:
        """典型 SQL 注入 payload 应被白名单拒绝"""
        with self.assertRaises(ValueError):
            self.provider.query_events(
                "test-run", {"event_type; DROP TABLE logs--": "x"}
            )

    def test_empty_filters_returns_all(self) -> None:
        """空 filters 应返回该 run_id 全部记录"""
        records = self.provider.query_events("test-run", {})
        self.assertEqual(len(records), 1)


class TestDuckDBThreadSafety(unittest.TestCase):
    """P2-14：DuckDB 单连接线程安全（锁保护）"""

    def test_concurrent_log_event_no_crash(self) -> None:
        """多线程并发 log_event 不应崩溃（锁串行化）"""
        self._tmp = TemporaryDirectory()
        try:
            db_path = Path(self._tmp.name) / "audit.duckdb"
            provider = DuckDBAuditProvider(db_path=db_path)

            errors: list[Exception] = []

            def worker(thread_id: int) -> None:
                try:
                    for i in range(20):
                        provider.log_event(
                            _make_record(
                                run_id=f"run-{thread_id}",
                                trace_id=f"trace-{thread_id}-{i}",
                            )
                        )
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            provider.close()

            self.assertEqual(errors, [], f"并发写入产生异常: {errors}")
            # 验证所有记录都写入成功
            provider2 = DuckDBAuditProvider(db_path=db_path)
            for t in range(4):
                records = provider2.query_events(f"run-{t}", {})
                self.assertEqual(len(records), 20, f"run-{t} 记录数不完整")
            provider2.close()
        finally:
            self._tmp.cleanup()

    def test_concurrent_query_event_no_crash(self) -> None:
        """多线程并发读 + 写不应崩溃"""
        self._tmp = TemporaryDirectory()
        try:
            db_path = Path(self._tmp.name) / "audit.duckdb"
            provider = DuckDBAuditProvider(db_path=db_path)
            # 预写若干记录
            for i in range(10):
                provider.log_event(_make_record(run_id="rw-run", trace_id=f"trace-{i}"))

            errors: list[Exception] = []

            def reader() -> None:
                try:
                    for _ in range(20):
                        provider.query_events("rw-run", {})
                except Exception as e:
                    errors.append(e)

            def writer() -> None:
                try:
                    for i in range(20):
                        provider.log_event(
                            _make_record(run_id="rw-run", trace_id=f"new-trace-{i}")
                        )
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=reader) for _ in range(3)] + [
                threading.Thread(target=writer) for _ in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            provider.close()
            self.assertEqual(errors, [], f"并发读写产生异常: {errors}")
        finally:
            self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()

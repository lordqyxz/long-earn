"""miniQMT 到本地 DuckDB 的增量同步模块。

DuckDB 是系统的优先访问层，承载业务操作所需的本地同步数据；miniQMT 是
唯一上游数据客户端，仅在显式同步或本地数据缺失、过期时用于增量补齐。
同步写入采用幂等 upsert，不会清理本地数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from long_earn.services.data_ingestion_service import DataIngestionService

if TYPE_CHECKING:
    from long_earn.services import LoggerService


@dataclass(frozen=True)
class IncrementalSyncReport:
    """一次 miniQMT 同步的稳定结果摘要。"""

    status: str
    mode: str
    universe: str
    price_symbols: int
    financial_symbols: int
    cache_path: str
    reason: str = ""

    @classmethod
    def from_result(cls, result: dict[str, object]) -> IncrementalSyncReport:
        """将底层采集结果转换为同步模块的公开结果。"""
        return cls(
            status=str(result.get("status", "error")),
            mode=str(result.get("mode", "incremental")),
            universe=str(result.get("universe", "")),
            price_symbols=int(result.get("price_symbols", 0)),
            financial_symbols=int(result.get("financial_symbols", 0)),
            cache_path=str(result.get("cache_path", "")),
            reason=str(result.get("reason", "")),
        )

    def as_dict(self) -> dict[str, object]:
        """提供给 CLI、启动流程和旧调用点的字典表示。"""
        return {
            "status": self.status,
            "mode": self.mode,
            "universe": self.universe,
            "price_symbols": self.price_symbols,
            "financial_symbols": self.financial_symbols,
            "cache_path": self.cache_path,
            "reason": self.reason,
        }


class IncrementalSyncService:
    """协调 miniQMT 上游与 DuckDB 主数据访问层的增量同步。"""

    def __init__(self, logger: LoggerService | None = None) -> None:
        self._ingestion = DataIngestionService(logger=logger)

    @property
    def is_available(self) -> bool:
        """miniQMT 上游客户端是否可用于执行同步。"""
        return self._ingestion.is_available

    def sync(  # noqa: PLR0913
        self,
        universe: str = "all",
        start_date: str = "",
        end_date: str = "",
        skip_financial: bool = False,
        batch_size: int = 0,
        max_workers: int = 4,
        full: bool = False,
    ) -> IncrementalSyncReport:
        """将所需数据从 miniQMT 幂等同步到 DuckDB。

        默认仅同步本地库缺失或过期的记录；``full=True`` 只表示重新拉取指定
        范围的数据，仍以 upsert 方式写入，绝不删除缓存中的既有数据。
        """
        result = self._ingestion.run(
            universe=universe,
            start_date=start_date,
            end_date=end_date,
            skip_financial=skip_financial,
            batch_size=batch_size,
            max_workers=max_workers,
            full=full,
        )
        return IncrementalSyncReport.from_result(result)

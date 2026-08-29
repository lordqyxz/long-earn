"""ContextPreparationService 接口行为测试（ADR-021：纯确定性激活）。"""

from unittest.mock import MagicMock

from long_earn.services.context_preparation_service import (
    ContextPreparationServiceImpl,
)


def test_returns_activation_on_hit() -> None:
    memory = MagicMock()
    memory.activate_events.return_value = ["event-1", "event-2"]
    service = ContextPreparationServiceImpl(memory, MagicMock())

    result = service.prepare("茅台", k=3)

    assert result.items == ("event-1", "event-2")
    assert result.text == "event-1\nevent-2"
    assert not result.missed
    memory.activate_events.assert_called_once_with("茅台", k=3)


def test_miss_returns_empty_activation_without_inference() -> None:
    """未命中只返回 missed 标记，绝不内嵌采集推理（ADR-021）。"""
    memory = MagicMock()
    memory.activate_events.return_value = []
    service = ContextPreparationServiceImpl(memory, MagicMock())

    result = service.prepare("新能源")

    assert result.missed
    assert result.text == ""
    memory.activate_events.assert_called_once_with("新能源", k=5)


def test_activation_failure_degrades_to_miss() -> None:
    memory = MagicMock()
    memory.activate_events.side_effect = RuntimeError("pg down")
    logger = MagicMock()
    service = ContextPreparationServiceImpl(memory, logger)

    result = service.prepare("市场热点")

    assert result.missed
    logger.warning.assert_called_once()


def test_empty_query_short_circuits() -> None:
    memory = MagicMock()
    service = ContextPreparationServiceImpl(memory, MagicMock())

    result = service.prepare("  ")

    assert result.missed
    memory.activate_events.assert_not_called()

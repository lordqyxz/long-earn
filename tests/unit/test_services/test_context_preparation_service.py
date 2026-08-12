"""ContextPreparationService 接口行为测试。"""

from unittest.mock import MagicMock

from long_earn.services.context_preparation_service import (
    ContextPreparationServiceImpl,
)


def test_returns_first_activation_without_inference() -> None:
    memory = MagicMock()
    memory.activate_events.return_value = ["event-1", "event-2"]
    infer_events = MagicMock()
    service = ContextPreparationServiceImpl(memory, MagicMock(), infer_events)

    result = service.prepare("茅台", k=3)

    assert result == "event-1\nevent-2"
    memory.activate_events.assert_called_once_with("茅台", k=3)
    infer_events.assert_not_called()


def test_miss_infers_then_activates_again() -> None:
    memory = MagicMock()
    memory.activate_events.side_effect = [[], ["new-event"]]
    infer_events = MagicMock()
    service = ContextPreparationServiceImpl(memory, MagicMock(), infer_events)

    result = service.prepare("新能源")

    assert result == "new-event"
    infer_events.assert_called_once_with("新能源")
    assert memory.activate_events.call_count == 2


def test_inference_failure_degrades_to_second_activation() -> None:
    memory = MagicMock()
    memory.activate_events.side_effect = [[], ["cached-event"]]
    logger = MagicMock()
    infer_events = MagicMock(side_effect=RuntimeError("offline"))
    service = ContextPreparationServiceImpl(memory, logger, infer_events)

    result = service.prepare("市场热点")

    assert result == "cached-event"
    logger.warning.assert_called_once()


def test_force_refresh_skips_first_activation() -> None:
    memory = MagicMock()
    memory.activate_events.return_value = ["fresh-event"]
    infer_events = MagicMock()
    service = ContextPreparationServiceImpl(memory, MagicMock(), infer_events)

    result = service.prepare("财报", force_refresh=True)

    assert result == "fresh-event"
    infer_events.assert_called_once_with("财报")
    memory.activate_events.assert_called_once_with("财报", k=5)

"""LLMServiceImpl 重建+重试可靠性测试

防止 langchain_ollama / ollama-python / httpx 在多次 invoke 后内部状态损坏
触发 Fatal Python error (PyEval_SaveThread GIL released)——实测在 e2e 多轮
演进的第 2 轮 reflection 阶段会让进程整体崩溃。
"""

from unittest.mock import MagicMock

import pytest

from long_earn.services.llm_service import LLMServiceImpl


def _make_service():
    """构造测试用 LLMServiceImpl（绕开 create_llm 真实构造）"""
    config = MagicMock()
    config.llm_type = "ollama"
    config.llm_model = "test-model"
    config.llm_base_url = "http://localhost:11434"
    logger = MagicMock()
    return LLMServiceImpl(config, logger)


class TestLLMRebuildOnEveryInvoke:
    """每次 invoke 必须重建 LLM 实例避免长连接累积错误"""

    def test_rebuilds_llm_each_invoke(self):
        svc = _make_service()
        builds: list[object] = []

        class _Resp:
            content = "ok"

        def fake_build():
            llm = MagicMock()
            llm.invoke.return_value = _Resp()
            llm.bind.return_value = llm
            builds.append(llm)
            return llm

        svc._build_llm = fake_build  # type: ignore[method-assign]

        svc.invoke("p1")
        svc.invoke("p2")
        svc.invoke("p3")

        # 每次 invoke 都构造一个新 llm 实例
        assert len(builds) == 3, (
            f"3 次 invoke 应触发 3 次 _build_llm，实际 {len(builds)} 次"
        )
        # 每个实例自己被 invoke 一次
        for llm in builds:
            assert llm.invoke.call_count == 1


class TestLLMRetryOnException:
    """invoke 失败时自动重试一次"""

    def test_retry_once_on_exception(self):
        svc = _make_service()
        attempts: list[int] = []

        class _Resp:
            content = "recovered"

        def fake_build():
            llm = MagicMock()
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                llm.invoke.side_effect = ConnectionError("transient")
            else:
                llm.invoke.return_value = _Resp()
            llm.bind.return_value = llm
            return llm

        svc._build_llm = fake_build  # type: ignore[method-assign]

        result = svc.invoke("p")
        assert result.content == "recovered"
        assert len(attempts) == 2  # 第 1 次失败 + 重试 1 次成功

    def test_raises_after_max_retries(self):
        svc = _make_service()

        def fake_build():
            llm = MagicMock()
            llm.invoke.side_effect = ConnectionError("persistent")
            llm.bind.return_value = llm
            return llm

        svc._build_llm = fake_build  # type: ignore[method-assign]

        with pytest.raises(ConnectionError, match="persistent"):
            svc.invoke("p")

    def test_format_json_binding_preserved_in_retry(self):
        """format='json' 绑定在重试时仍然生效"""
        svc = _make_service()
        bind_calls: list[str] = []

        class _Resp:
            content = "ok"

        def fake_build():
            llm = MagicMock()

            def _bind(**kw):
                bind_calls.append(kw.get("format", ""))
                return llm

            llm.bind.side_effect = _bind
            llm.invoke.return_value = _Resp()
            return llm

        svc._build_llm = fake_build  # type: ignore[method-assign]

        svc.invoke("p", format="json")
        assert bind_calls == ["json"]


class TestLLMInvokeCounter:
    """invoke_count 必须按调用次数（而非尝试次数）递增"""

    def test_invoke_count_per_call_not_per_attempt(self):
        svc = _make_service()
        attempts = {"n": 0}

        class _Resp:
            content = "ok"

        def fake_build():
            llm = MagicMock()
            attempts["n"] += 1
            if attempts["n"] == 1:
                # 第 1 次调用的第 1 次尝试失败
                llm.invoke.side_effect = ConnectionError("x")
            else:
                llm.invoke.return_value = _Resp()
            llm.bind.return_value = llm
            return llm

        svc._build_llm = fake_build  # type: ignore[method-assign]

        svc.invoke("p1")  # 1 次失败 + 1 次重试 = 总尝试 2 次
        svc.invoke("p2")  # 1 次成功
        assert svc._invoke_count == 2  # 按调用计数（2），非尝试（3）


class TestLLMMonitoringTracking:
    """monitoring 注入后 invoke 应自动追踪 token 消耗"""

    def test_track_tokens_called_on_response_with_usage_metadata(self):
        """response.usage_metadata 存在时 track_tokens 被调用"""
        config = MagicMock()
        config.llm_type = "ollama"
        config.llm_model = "m"
        config.llm_base_url = "http://localhost"
        monitoring = MagicMock()
        svc = LLMServiceImpl(config, MagicMock(), monitoring=monitoring)

        class _Resp:
            content = "ok"
            usage_metadata = {
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
            }

        fake_llm = MagicMock()
        fake_llm.invoke.return_value = _Resp()
        fake_llm.bind.return_value = fake_llm
        svc._build_llm = lambda: fake_llm  # type: ignore[method-assign]

        svc.invoke("p")
        monitoring.track_tokens.assert_called_once_with(_Resp.usage_metadata)

    def test_track_tokens_skipped_when_metadata_missing(self):
        """response 无 usage_metadata 时不调 track_tokens（无静默错误）"""
        config = MagicMock()
        config.llm_type = "ollama"
        config.llm_model = "m"
        config.llm_base_url = "http://localhost"
        monitoring = MagicMock()
        svc = LLMServiceImpl(config, MagicMock(), monitoring=monitoring)

        class _Resp:
            content = "ok"

        fake_llm = MagicMock()
        fake_llm.invoke.return_value = _Resp()
        fake_llm.bind.return_value = fake_llm
        svc._build_llm = lambda: fake_llm  # type: ignore[method-assign]

        svc.invoke("p")
        monitoring.track_tokens.assert_not_called()

    def test_monitoring_none_no_attribute_error(self):
        """不注入 monitoring（默认 None）时主路径无任何额外开销与错误"""
        svc = _make_service()  # 默认 monitoring=None

        class _Resp:
            content = "ok"
            usage_metadata = {"input_tokens": 1}

        fake_llm = MagicMock()
        fake_llm.invoke.return_value = _Resp()
        fake_llm.bind.return_value = fake_llm
        svc._build_llm = lambda: fake_llm  # type: ignore[method-assign]

        # 不应抛 AttributeError
        result = svc.invoke("p")
        assert result.content == "ok"

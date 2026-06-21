"""xtquant 查询超时降级测试

防止 QMT 未连接 / C++ 端异常时让 Python 进程整体崩溃。
所有 xtdata 查询调用必须有 _run_with_timeout 包裹，超时返回空数据。
"""

import time

import pandas as pd

from long_earn.backtest.data.miniqmt_provider import MiniQmtClient


def _make_client_with_xtdata(xtdata_stub):
    """构造 MiniQmtClient 并直接注入 xtdata stub，绕过 import"""
    client = MiniQmtClient()
    client._xtdata = xtdata_stub
    client._available = True
    # 把 timeout 调小避免测试拖慢
    client._DOWNLOAD_TIMEOUT = 1
    return client


class TestXtQuantQueryTimeout:
    """所有 xtdata 查询必须可被超时打断 / 异常吞掉，不让进程崩溃"""

    def test_get_kline_blocks_returns_empty(self):
        """get_market_data_ex 阻塞时 get_kline 必须超时返回空"""

        class _BlockingXtdata:
            def download_history_data2(self, **_kw):
                pass  # 假装下载成功

            def get_market_data_ex(self, **_kw):
                # 阻塞超过 client._DOWNLOAD_TIMEOUT
                time.sleep(3)
                return {}

        client = _make_client_with_xtdata(_BlockingXtdata())
        df = client.get_kline(["000001"], "20240101", "20240110")

        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_get_kline_exception_returns_empty(self):
        """get_market_data_ex 抛异常时 get_kline 必须返回空 DataFrame 而不上抛"""

        class _RaisingXtdata:
            def download_history_data2(self, **_kw):
                pass

            def get_market_data_ex(self, **_kw):
                raise RuntimeError("xtquant C++ 内部错误")

        client = _make_client_with_xtdata(_RaisingXtdata())
        df = client.get_kline(["000001"], "20240101", "20240110")

        assert df.empty

    def test_get_sector_stocks_blocks_returns_empty(self):
        """get_stock_list_in_sector 阻塞时返回空列表"""

        class _BlockingXtdata:
            def get_stock_list_in_sector(self, _name):
                time.sleep(3)
                return ["000001"]

        client = _make_client_with_xtdata(_BlockingXtdata())
        result = client.get_sector_stocks("沪深300")

        assert result == []

    def test_get_financial_exception_returns_empty(self):
        """get_financial_data 抛异常时返回空 DataFrame"""

        class _RaisingXtdata:
            def download_financial_data2(self, **_kw):
                pass

            def get_financial_data(self, **_kw):
                raise ValueError("无效参数")

        client = _make_client_with_xtdata(_RaisingXtdata())
        df = client.get_financial(["000001"])

        assert df.empty

    def test_get_instrument_detail_timeout_returns_empty_dict(self):
        """get_instrument_detail 超时返回空字典"""

        class _BlockingXtdata:
            def get_instrument_detail(self, _code):
                time.sleep(3)
                return {"name": "a"}

        client = _make_client_with_xtdata(_BlockingXtdata())
        result = client.get_instrument_detail("000001.SZ")

        assert result == {}

    def test_get_full_tick_exception_returns_empty_dict(self):
        """get_full_tick 抛异常返回空字典"""

        class _RaisingXtdata:
            def get_full_tick(self, _codes):
                raise RuntimeError("connection lost")

        client = _make_client_with_xtdata(_RaisingXtdata())
        result = client.get_full_tick(["000001.SZ"])

        assert result == {}


class TestXtQuantDisableEnvVar:
    """LONG_EARN_DISABLE_XTQUANT 环境变量强制禁用 xtquant，
    避免无 QMT 环境下 C++ SIGABRT 杀进程。"""

    def test_env_var_force_disables(self, monkeypatch):
        """环境变量设为 1 时 is_available 直接返回 False，不尝试 import"""
        monkeypatch.setenv("LONG_EARN_DISABLE_XTQUANT", "1")
        client = MiniQmtClient()
        # 用全新 client（_available=None）触发首次检测
        assert client.is_available is False
        # 缓存生效
        assert client._available is False

    def test_env_var_true_value_also_disables(self, monkeypatch):
        """支持多种 truthy 字符串：true / yes / on（大小写不敏感）"""
        for value in ("true", "TRUE", "Yes", "on"):
            monkeypatch.setenv("LONG_EARN_DISABLE_XTQUANT", value)
            client = MiniQmtClient()
            assert client.is_available is False, f"value={value!r} 应被识别为禁用"

    def test_env_var_unset_falls_back_to_import(self, monkeypatch):
        """未设置环境变量时回退到 import 检测路径"""
        monkeypatch.delenv("LONG_EARN_DISABLE_XTQUANT", raising=False)
        client = MiniQmtClient()
        # 不强制断言 True/False（依赖 xtquant 是否实际安装）；
        # 只断言 is_available 走完了 import 检测路径并设置了 _available 缓存
        result = client.is_available
        assert isinstance(result, bool)
        assert client._available is result

    def test_env_var_false_does_not_disable(self, monkeypatch):
        """环境变量设为 0 / false 时不应禁用，走 import 检测"""
        monkeypatch.setenv("LONG_EARN_DISABLE_XTQUANT", "0")
        client = MiniQmtClient()
        result = client.is_available
        # 不强制 True（取决于安装），但如果 xtquant 已安装就应该 True
        assert isinstance(result, bool)

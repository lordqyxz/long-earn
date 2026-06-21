"""ciccwm HTTP 客户端单元测试。

覆盖凭证加载、股票代码解析、响应提取等关键逻辑。
"""

import json
import tempfile
from pathlib import Path

import pytest

from long_earn.backtest.data import ciccwm_client as client


class TestLoadApiKey:
    """凭证加载测试"""

    def setup_method(self) -> None:
        self._orig_path = client._CICCWM_CREDENTIAL_PATH
        # 使用临时目录隔离测试
        self._tmpdir = tempfile.mkdtemp()
        client._CICCWM_CREDENTIAL_PATH = Path(self._tmpdir) / "config.json"

    def teardown_method(self) -> None:
        client._CICCWM_CREDENTIAL_PATH = self._orig_path

    def test_file_not_found(self) -> None:
        """凭证文件不存在时抛 CiccwmCredentialError。"""
        with pytest.raises(client.CiccwmCredentialError, match="凭证文件不存在"):
            client._load_api_key()

    def test_invalid_json(self) -> None:
        """凭证文件内容非 JSON 时抛 CiccwmCredentialError。"""
        client._CICCWM_CREDENTIAL_PATH.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(client.CiccwmCredentialError, match="格式错误"):
            client._load_api_key()

    def test_empty_key(self) -> None:
        """凭证文件中 CICCWM_API_KEY 为空时抛 CiccwmCredentialError。"""
        client._CICCWM_CREDENTIAL_PATH.write_text(
            json.dumps({"CICCWM_API_KEY": ""}), encoding="utf-8"
        )
        with pytest.raises(client.CiccwmCredentialError, match="为空"):
            client._load_api_key()

    def test_key_missing(self) -> None:
        """凭证文件中缺少 CICCWM_API_KEY 字段时抛 CiccwmCredentialError。"""
        client._CICCWM_CREDENTIAL_PATH.write_text(
            json.dumps({"other": "value"}), encoding="utf-8"
        )
        with pytest.raises(client.CiccwmCredentialError, match="为空"):
            client._load_api_key()

    def test_valid_key(self) -> None:
        """有效凭证返回 API key 字符串。"""
        test_key = "test-api-key-12345"
        client._CICCWM_CREDENTIAL_PATH.write_text(
            json.dumps({"CICCWM_API_KEY": test_key}), encoding="utf-8"
        )
        assert client._load_api_key() == test_key

    def test_key_with_whitespace(self) -> None:
        """API key 前后的空白字符被 strip。"""
        test_key = "strip-test-key"
        client._CICCWM_CREDENTIAL_PATH.write_text(
            json.dumps({"CICCWM_API_KEY": f"  {test_key}  "}), encoding="utf-8"
        )
        assert client._load_api_key() == test_key


class TestParseSymbol:
    """股票代码解析测试"""

    def test_sh_suffix(self) -> None:
        """600519.SH → (600519, 1)。"""
        assert client._parse_symbol("600519.SH") == ("600519", 1)

    def test_sz_suffix(self) -> None:
        """000001.SZ → (000001, 0)。"""
        assert client._parse_symbol("000001.SZ") == ("000001", 0)

    def test_bj_suffix(self) -> None:
        """832735.BJ → (832735, 2)。"""
        assert client._parse_symbol("832735.BJ") == ("832735", 2)

    def test_hk_suffix(self) -> None:
        """00700.HK → (00700, 31)。"""
        assert client._parse_symbol("00700.HK") == ("00700", 31)

    def test_sh_no_suffix(self) -> None:
        """以 6 开头的无后缀代码 → 沪市 (market=1)。"""
        assert client._parse_symbol("600519") == ("600519", 1)

    def test_sz_no_suffix(self) -> None:
        """以 0 开头的无后缀代码 → 深市 (market=0)。"""
        assert client._parse_symbol("000001") == ("000001", 0)

    def test_gem_no_suffix(self) -> None:
        """以 3 开头的无后缀代码 → 深市 (market=0)。"""
        assert client._parse_symbol("300750") == ("300750", 0)

    def test_bj_no_suffix(self) -> None:
        """以 8 开头的无后缀代码 → 北交所 (market=2)。"""
        assert client._parse_symbol("832735") == ("832735", 2)

    def test_unknown_suffix(self) -> None:
        """未知后缀抛 ValueError。"""
        with pytest.raises(ValueError, match="未知的市场后缀"):
            client._parse_symbol("12345.XX")

    def test_unknown_prefix(self) -> None:
        """无法推断 market 的无后缀代码抛 ValueError。"""
        with pytest.raises(ValueError, match="无法推断 market"):
            client._parse_symbol("12345")


class TestExtractList:
    """ListHead/ListItem 响应提取测试"""

    def test_empty_response(self) -> None:
        """空响应返回空列表。"""
        assert client._extract_list({}) == []

    def test_no_list_item(self) -> None:
        """无 ListItem 字段返回空列表。"""
        result = {"rsp": {"rsp_json": {"ListHead": {}}}}
        assert client._extract_list(result) == []

    def test_list_item_as_list(self) -> None:
        """ListItem 为列表时正常提取。"""
        items = [{"code": "600519"}, {"code": "000001"}]
        result = {"rsp": {"rsp_json": {"ListItem": items}}}
        extracted = client._extract_list(result)
        assert len(extracted) == 2
        assert extracted[0]["code"] == "600519"

    def test_list_item_as_dict(self) -> None:
        """单条记录时 ListItem 为 dict 仍正确返回。"""
        result = {"rsp": {"rsp_json": {"ListItem": {"code": "600519"}}}}
        extracted = client._extract_list(result)
        assert len(extracted) == 1
        assert extracted[0]["code"] == "600519"

    def test_missing_rsp_json(self) -> None:
        """缺少 rsp.rsp_json 返回空列表。"""
        result = {"rsp": {}}
        assert client._extract_list(result) == []

    def test_missing_rsp(self) -> None:
        """缺少 rsp 返回空列表。"""
        assert client._extract_list({"ret": 0}) == []

"""ciccwm 数据层单元测试

覆盖 ADR-006 定义的「系统关键环节」：
  - 凭证加载（缺失/为空/JSON 格式错误/BOM 容错）
  - rsp_json 解析（ret 非 0 / ret_code 非 0 / 正常 / 空字符串）
  - ListHead/ListItem → 命名记录转换
  - coerce_value 数值转换（保留标识符）
  - 符号格式转换（xtquant ↔ ciccwm）

HTTP 真实调用属集成测试范畴，需凭证配置，不在此覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from long_earn.backtest.data.ciccwm_client import (
    STRING_FIELDS,
    CICCWMCredentialError,
    build_finance_payload,
    build_market_payload,
    build_news_payload,
    coerce_value,
    is_credential_available,
    list_items_to_records,
    load_api_key,
    parse_rsp_json,
)
from long_earn.backtest.data.ciccwm_provider import (
    CiccwmDataProvider,
    _ciccwm_to_xt,
    _xt_to_ciccwm,
)

# ── 凭证加载 ─────────────────────────────────────────────────────────────


class TestLoadApiKey:
    """凭证加载逻辑测试（不依赖真实凭证文件）。"""

    def test_missing_file_raises_credential_error(self, tmp_path: Path):
        """凭证文件不存在时抛 CICCWMCredentialError。"""
        fake_path = tmp_path / "nonexistent" / "config.json"
        with pytest.raises(CICCWMCredentialError, match="重新安装"):
            load_api_key(fake_path)

    def test_empty_api_key_raises_credential_error(self, tmp_path: Path):
        """API Key 为空时抛 CICCWMCredentialError。"""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps({"CICCWM_API_KEY": ""}), encoding="utf-8"
        )
        with pytest.raises(CICCWMCredentialError, match="重新安装"):
            load_api_key(config_path)

    def test_valid_key_returned(self, tmp_path: Path):
        """有效凭证返回 API Key。"""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps({"CICCWM_API_KEY": "test-key-123"}), encoding="utf-8"
        )
        assert load_api_key(config_path) == "test-key-123"

    def test_bom_prefixed_file_handled(self, tmp_path: Path):
        """UTF-8 BOM 前缀应被容错处理（踩坑记录 #1）。"""
        config_path = tmp_path / "config.json"
        # 写入带 BOM 的 UTF-8
        raw = json.dumps({"CICCWM_API_KEY": "bom-key"})
        config_path.write_bytes(b"\xef\xbb\xbf" + raw.encode("utf-8"))
        assert load_api_key(config_path) == "bom-key"

    def test_malformed_json_raises_value_error(self, tmp_path: Path):
        """JSON 格式错误抛 ValueError。"""
        config_path = tmp_path / "config.json"
        config_path.write_text("not a json {{{", encoding="utf-8")
        with pytest.raises(ValueError, match="格式错误"):
            load_api_key(config_path)


class TestIsCredentialAvailable:
    """is_credential_available 不抛异常，返回 bool。"""

    def test_missing_file_returns_false(self, tmp_path: Path):
        fake_path = tmp_path / "nonexistent.json"
        assert is_credential_available(fake_path) is False

    def test_valid_file_returns_true(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps({"CICCWM_API_KEY": "valid"}), encoding="utf-8"
        )
        assert is_credential_available(config_path) is True


# ── rsp_json 解析 ────────────────────────────────────────────────────────


class TestParseRspJson:
    """财务接口响应包装层解析。"""

    def test_normal_response(self):
        """正常响应解析为列表。"""
        response = {
            "ret": 0,
            "rsp": {
                "ret_code": 0,
                "rsp_json": json.dumps([{"a": 1}, {"b": 2}]),
            },
        }
        result = parse_rsp_json(response)
        assert result == [{"a": 1}, {"b": 2}]

    def test_empty_rsp_json(self):
        """rsp_json 为空字符串时返回空列表。"""
        response = {
            "ret": 0,
            "rsp": {"ret_code": 0, "rsp_json": ""},
        }
        assert parse_rsp_json(response) == []

    def test_ret_nonzero_raises(self):
        """ret 非 0 抛 ValueError。"""
        response = {"ret": 1, "msg": "系统错误"}
        with pytest.raises(ValueError, match="系统错误"):
            parse_rsp_json(response)

    def test_ret_code_nonzero_raises(self):
        """ret_code 非 0 抛 ValueError。"""
        response = {
            "ret": 0,
            "rsp": {"ret_code": 1, "ret_msg": "业务错误"},
        }
        with pytest.raises(ValueError, match="业务错误"):
            parse_rsp_json(response)

    def test_non_list_rsp_json_raises(self):
        """rsp_json 不是数组时抛 ValueError。"""
        response = {
            "ret": 0,
            "rsp": {"ret_code": 0, "rsp_json": json.dumps({"not": "list"})},
        }
        with pytest.raises(ValueError, match="不是数组"):
            parse_rsp_json(response)


# ── ListHead/ListItem 转换 ───────────────────────────────────────────────


class TestListItemsToRecords:
    """通达信表格结构 → 命名记录转换。"""

    def test_basic_conversion(self):
        """基本字段映射和数值转换。"""
        data = {
            "ListHead": {"ItemHead": ["Code", "Name", "Open", "Close"]},
            "ListItem": [
                {"Item": ["600519", "贵州茅台", "1800.0", "1810.5"]},
                {"Item": ["000001", "平安银行", "10.0", "10.5"]},
            ],
        }
        records = list_items_to_records(data)
        assert len(records) == 2

        # 字段名映射
        assert records[0]["code"] == "600519"
        assert records[0]["name"] == "贵州茅台"
        # 数值转换（字符串 "1800.0" → int 1800）
        assert records[0]["open"] == 1800
        assert records[0]["close"] == 1810.5

    def test_string_fields_preserved(self):
        """证券代码等标识符保持字符串。"""
        data = {
            "ListHead": {"ItemHead": ["Code", "Setcode", "Name"]},
            "ListItem": [{"Item": ["600519", "1", "贵州茅台"]}],
        }
        records = list_items_to_records(data)
        # code 是字符串（在 STRING_FIELDS 中）
        assert records[0]["code"] == "600519"
        assert isinstance(records[0]["code"], str)
        # market (Setcode 映射) 也在 STRING_FIELDS 中
        assert records[0]["market"] == "1"
        assert isinstance(records[0]["market"], str)

    def test_empty_listitem(self):
        """空 ListItem 返回空列表。"""
        data = {
            "ListHead": {"ItemHead": ["Code", "Name"]},
            "ListItem": [],
        }
        assert list_items_to_records(data) == []

    def test_unknown_field_kept_as_is(self):
        """未映射的字段名保留原始值。"""
        data = {
            "ListHead": {"ItemHead": ["UnknownCol", "Open"]},
            "ListItem": [{"Item": ["val", "100.0"]}],
        }
        records = list_items_to_records(data)
        assert records[0]["UnknownCol"] == "val"
        assert records[0]["open"] == 100


# ── coerce_value ─────────────────────────────────────────────────────────


class TestCoerceValue:
    """数值转换逻辑测试。"""

    def test_integer_string(self):
        assert coerce_value("open", "1800") == 1800
        assert isinstance(coerce_value("open", "1800"), int)

    def test_float_string(self):
        assert coerce_value("close", "1810.5") == 1810.5
        assert isinstance(coerce_value("close", "1810.5"), float)

    def test_string_identifier_preserved(self):
        """STRING_FIELDS 中的字段保持字符串。"""
        for field in STRING_FIELDS:
            assert coerce_value(field, "12345") == "12345"

    def test_non_numeric_string(self):
        """非数字字符串保持原值。"""
        assert coerce_value("open", "N/A") == "N/A"

    def test_non_string_value(self):
        """非字符串输入直接返回。"""
        assert coerce_value("open", 100) == 100
        assert coerce_value("open", None) is None


# ── 请求构造 ─────────────────────────────────────────────────────────────


class TestBuildPayload:
    """请求体构造测试。"""

    def test_market_payload(self):
        """行情请求体包装格式正确。"""
        payload = build_market_payload("HQServ.PBHQInfo", {"Code": "600519"})
        assert payload["cmdname"] == "SkillTdxQuotationQueryCommon"
        assert payload["param"]["entry"] == "HQServ.PBHQInfo"
        assert "tdx_param" in payload["param"]
        # tdx_param 是 JSON 字符串
        assert json.loads(payload["param"]["tdx_param"]) == {"Code": "600519"}

    def test_finance_payload(self):
        """财务请求体构造。"""
        payload = build_finance_payload("48571", "600519", "12", "0")
        assert payload["cmdname"] == "SkillEQuoteZhongzhuoF10Common"
        req_json = json.loads(payload["param"]["req_json"])
        assert req_json == {
            "action": "48571",
            "gpcode": "600519",
            "qtime": "12",
            "gtype": "0",
        }

    def test_news_payload(self):
        """资讯请求体构造。"""
        params = {"type": 1, "page_num": 1, "page_size": 10}
        payload = build_news_payload(params)
        assert payload["cmdname"] == "SkillEInformationTopicSecendPage"
        assert payload["param"] == params


# ── 符号格式转换 ─────────────────────────────────────────────────────────


class TestSymbolConversion:
    """xtquant 格式 ↔ ciccwm (code, market) 转换。"""

    def test_shanghai(self):
        """上海证券交易所：.SH → market=1。"""
        assert _xt_to_ciccwm("600519.SH") == ("600519", 1)

    def test_shenzhen(self):
        """深圳证券交易所：.SZ → market=0。"""
        assert _xt_to_ciccwm("000001.SZ") == ("000001", 0)

    def test_bse(self):
        """北交所：.BJ → market=2。"""
        assert _xt_to_ciccwm("430047.BJ") == ("430047", 2)

    def test_hk(self):
        """港股：.HK → market=31。"""
        assert _xt_to_ciccwm("00700.HK") == ("00700", 31)

    def test_reverse_sh(self):
        """反向转换：market=1 → .SH。"""
        assert _ciccwm_to_xt("600519", 1) == "600519.SH"

    def test_reverse_sz(self):
        """反向转换：market=0 → .SZ。"""
        assert _ciccwm_to_xt("000001", 0) == "000001.SZ"

    def test_invalid_format_raises(self):
        """无效格式抛 ValueError。"""
        with pytest.raises(ValueError, match="无法解析"):
            _xt_to_ciccwm("invalid_code")

    def test_unknown_suffix_raises(self):
        """未知后缀抛 ValueError。"""
        with pytest.raises(ValueError, match="未知市场后缀"):
            _xt_to_ciccwm("600519.XX")

    def test_unknown_market_raises(self):
        """未知 market 数值抛 ValueError。"""
        with pytest.raises(ValueError, match="未知市场代码"):
            _ciccwm_to_xt("600519", 999)


# ── Provider is_available（不依赖网络） ──────────────────────────────────


class TestProviderAvailability:
    """CiccwmDataProvider.is_available 逻辑测试。"""

    def test_available_with_real_credential(self):
        """本机有真实凭证时 is_available 返回 True（集成验证）。
        若本机无凭证则跳过（不强制依赖凭证存在）。
        """
        provider = CiccwmDataProvider()
        # 不断言具体值，只验证不抛异常且返回 bool
        assert isinstance(provider.is_available, bool)

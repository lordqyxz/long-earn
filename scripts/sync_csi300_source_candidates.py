"""将 CSI 300 当前成分和 MiniQMT 除权因子保存到隔离候选区。"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import math
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd
from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from long_earn.core.pg import pg_connect  # noqa: E402

INDEX_CODE = "csi300"
AKSHARE_INDEX_SYMBOL = "000300"
FACTOR_BATCH_SIZE = 100
AKSHARE_API = "index_stock_cons_csindex"
AKSHARE_REFERENCE = (
    "https://github.com/akfamily/akshare/blob/main/docs/data/index/index.md"
    "#中证指数成份股"
)
MINIQMT_API = "xtdata.get_divid_factors"
MINIQMT_REFERENCE = "https://dict.thinktrader.net/nativeApi/xtdata.html"
MINIQMT_FACTOR_COLUMNS = (
    "time",
    "interest",
    "stockBonus",
    "stockGift",
    "allotNum",
    "allotPrice",
    "gugai",
    "dr",
)
AKSHARE_COLUMNS = (
    "日期",
    "指数代码",
    "指数名称",
    "指数英文名称",
    "成分券代码",
    "成分券名称",
    "成分券英文名称",
    "交易所",
    "交易所英文名称",
)
EXCHANGE_SUFFIXES = {
    "上海证券交易所": "SH",
    "深圳证券交易所": "SZ",
    "北京证券交易所": "BJ",
}


def _json_safe(value: Any) -> Any:
    """将 Pandas 标量和日期转换成 JSON 可存储值。"""
    if (
        value is None
        or value is pd.NA
        or value is pd.NaT
        or (pd.api.types.is_scalar(value) and pd.isna(value))
    ):
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        value = None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical_json(value: Any) -> str:
    """生成稳定的 UTF-8 JSON 表示。"""
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _make_record(
    *,
    record_key: str,
    index_code: str,
    symbol: str,
    effective_date: dt.date | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "record_key": record_key,
        "index_code": index_code,
        "symbol": symbol,
        "effective_date": effective_date,
        "payload": payload,
        "payload_sha256": _sha256(payload),
    }


def _save_candidate_batch(
    *,
    dataset_name: str,
    provider_name: str,
    api_name: str,
    source_reference: str,
    source_version: str,
    requested_params: dict[str, Any],
    records: list[dict[str, Any]],
    notes: str,
) -> tuple[uuid.UUID, bool]:
    """保存一批原始候選資料，重複響應保持冪等。"""
    ordered_records = sorted(records, key=lambda record: record["record_key"])
    response_sha256 = _sha256(
        {"requested_params": requested_params, "records": ordered_records}
    )
    batch_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{provider_name}:{dataset_name}:{response_sha256}",
    )
    with pg_connect(row_factory=None) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO research_staging.source_candidate_batch (
                batch_id, dataset_name, provider_name, api_name,
                source_reference, source_version, requested_params,
                response_row_count, response_sha256, verification_status,
                rights_review_status, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                      'unverified', 'not_assessed', %s)
            ON CONFLICT (provider_name, dataset_name, response_sha256)
            DO NOTHING
            RETURNING batch_id
            """,
            (
                batch_id,
                dataset_name,
                provider_name,
                api_name,
                source_reference,
                source_version,
                Jsonb(_json_safe(requested_params)),
                len(ordered_records),
                response_sha256,
                notes,
            ),
        )
        inserted = cur.fetchone()
        if inserted is None:
            cur.execute(
                """
                SELECT batch_id
                FROM research_staging.source_candidate_batch
                WHERE provider_name = %s
                  AND dataset_name = %s
                  AND response_sha256 = %s
                """,
                (provider_name, dataset_name, response_sha256),
            )
            existing = cur.fetchone()
            if existing is None:
                raise RuntimeError("候选批次冲突后未找到已存在的批次")
            batch_id = existing[0]
        else:
            batch_id = inserted[0]

        if ordered_records:
            cur.executemany(
                """
                INSERT INTO research_staging.source_candidate_record (
                    batch_id, record_key, index_code, symbol, effective_date,
                    payload, payload_sha256
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (batch_id, record_key) DO NOTHING
                """,
                [
                    (
                        batch_id,
                        record["record_key"],
                        record["index_code"],
                        record["symbol"],
                        record["effective_date"],
                        Jsonb(record["payload"]),
                        record["payload_sha256"],
                    )
                    for record in ordered_records
                ],
            )
    return batch_id, inserted is not None


def _normalize_akshare_symbol(code: Any, exchange: Any) -> str:
    code_text = str(code).strip()
    if not code_text.isdigit() or len(code_text) > 6:
        raise ValueError(f"AkShare 返回了无法识别的成分券代码：{code!r}")
    suffix = EXCHANGE_SUFFIXES.get(str(exchange).strip())
    if suffix is None:
        raise ValueError(f"AkShare 返回了无法识别的交易所：{exchange!r}")
    return f"{code_text.zfill(6)}.{suffix}"


def _fetch_current_constituents() -> tuple[list[dict[str, Any]], str, list[str]]:
    frame = ak.index_stock_cons_csindex(symbol=AKSHARE_INDEX_SYMBOL)
    if frame.empty:
        raise RuntimeError("AkShare 沪深 300 成分接口返回空数据")
    missing_columns = set(AKSHARE_COLUMNS) - set(frame.columns)
    if missing_columns:
        raise RuntimeError(f"AkShare 返回字段缺失：{sorted(missing_columns)}")

    snapshot_dates = {str(value) for value in frame["日期"].dropna().tolist()}
    index_codes = {str(value).strip() for value in frame["指数代码"].dropna().tolist()}
    if len(snapshot_dates) != 1 or index_codes != {AKSHARE_INDEX_SYMBOL}:
        raise RuntimeError(
            "AkShare 响应包含多个快照日期或指数代码，拒绝合并写入候选批次"
        )
    snapshot_date = snapshot_dates.pop()
    if len(frame) != 300:
        raise RuntimeError(f"AkShare 沪深 300 快照应有 300 行，实际为 {len(frame)}")

    records: list[dict[str, Any]] = []
    symbols: list[str] = []
    for row in frame.loc[:, AKSHARE_COLUMNS].to_dict(orient="records"):
        symbol = _normalize_akshare_symbol(row["成分券代码"], row["交易所"])
        if symbol in symbols:
            raise RuntimeError(f"AkShare 响应含重复成分券：{symbol}")
        symbols.append(symbol)
        payload = {
            "provider_snapshot_date": snapshot_date,
            "index_code": AKSHARE_INDEX_SYMBOL,
            "fields": {str(key): _json_safe(value) for key, value in row.items()},
            "membership_semantics": "latest_snapshot_only_no_historical_intervals",
        }
        records.append(
            _make_record(
                record_key=f"{AKSHARE_INDEX_SYMBOL}:{snapshot_date}:{symbol}",
                index_code=INDEX_CODE,
                symbol=symbol,
                effective_date=None,
                payload=payload,
            )
        )
    return records, snapshot_date, sorted(symbols)


def _historical_csi300_symbols() -> list[str]:
    """读取 PG 中现有 CSI 300 PIT 成分出现过的代码。"""
    with pg_connect(read_only=True, row_factory=None) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT symbol
            FROM public.universe_constituents
            WHERE lower(index_code) = %s AND symbol IS NOT NULL
            ORDER BY symbol
            """,
            (INDEX_CODE,),
        )
        return [str(row[0]) for row in cur.fetchall()]


def _parse_event_date(value: Any) -> dt.date | None:
    text = str(value).strip()
    if not re.fullmatch(r"\d{8}", text):
        return None
    try:
        return dt.datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _fetch_factor_chunk(
    xtdata: Any,
    symbols: list[str],
) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    no_factor_count = 0
    for symbol in symbols:
        if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol) is None:
            raise ValueError(f"MiniQMT 请求代码格式不符合约定：{symbol!r}")
        frame = xtdata.get_divid_factors(symbol)
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"MiniQMT 对 {symbol} 返回的不是 DataFrame")
        if frame.empty:
            no_factor_count += 1
            continue
        missing_columns = set(MINIQMT_FACTOR_COLUMNS) - set(frame.columns)
        if missing_columns:
            raise RuntimeError(
                f"MiniQMT 对 {symbol} 返回字段缺失：{sorted(missing_columns)}"
            )
        for event_index, row in frame.loc[:, MINIQMT_FACTOR_COLUMNS].iterrows():
            event_key = str(event_index)
            event_date = _parse_event_date(event_key)
            payload = {
                "source_symbol": symbol,
                "event_date_index": event_key,
                "fields": {
                    str(column): _json_safe(row[column])
                    for column in MINIQMT_FACTOR_COLUMNS
                },
            }
            records.append(
                _make_record(
                    record_key=f"{symbol}:{event_key}",
                    index_code=INDEX_CODE,
                    symbol=symbol,
                    effective_date=event_date,
                    payload=payload,
                )
            )
    return records, no_factor_count


def _sync_akshare_snapshot() -> tuple[list[str], str, uuid.UUID, bool]:
    records, snapshot_date, symbols = _fetch_current_constituents()
    params = {
        "symbol": AKSHARE_INDEX_SYMBOL,
        "snapshot_date": snapshot_date,
        "semantic": "latest_snapshot_only_no_historical_intervals",
    }
    batch_id, inserted = _save_candidate_batch(
        dataset_name="csi300_constituents_current",
        provider_name="AkShare",
        api_name=AKSHARE_API,
        source_reference=AKSHARE_REFERENCE,
        source_version=_version("akshare"),
        requested_params=params,
        records=records,
        notes=(
            "原始中证指数最新成分快照；provider_snapshot_date 仅标注该快照日期，"
            "effective_date 留空；未经 PIT 验证，不得回填历史区间或用于回测。"
        ),
    )
    return symbols, snapshot_date, batch_id, inserted


def _sync_miniqmt_factors(current_symbols: list[str]) -> dict[str, Any]:
    from xtquant import xtdata

    symbols = sorted(set(_historical_csi300_symbols()) | set(current_symbols))
    if not symbols:
        raise RuntimeError("PG 与 AkShare 均未提供 CSI 300 成分代码")
    xtdata.connect()
    batch_ids: list[str] = []
    inserted_batches = 0
    factor_rows = 0
    empty_symbols = 0
    for offset in range(0, len(symbols), FACTOR_BATCH_SIZE):
        symbol_chunk = symbols[offset : offset + FACTOR_BATCH_SIZE]
        records, empty_count = _fetch_factor_chunk(xtdata, symbol_chunk)
        params = {
            "index_code": INDEX_CODE,
            "symbol_source": "union_of_pg_csi300_history_and_akshare_latest_snapshot",
            "symbols": symbol_chunk,
            "symbol_offset": offset,
            "symbols_with_factors": len(symbol_chunk) - empty_count,
            "symbols_without_factors": empty_count,
            "date_range": "all_available",
        }
        batch_id, inserted = _save_candidate_batch(
            dataset_name="csi300_miniqmt_adjustment_factors",
            provider_name="MiniQMT",
            api_name=MINIQMT_API,
            source_reference=MINIQMT_REFERENCE,
            source_version=_version("xtquant"),
            requested_params=params,
            records=records,
            notes=(
                "原始 MiniQMT 除权因子，event_date_index 按 DataFrame 行索引保存；"
                "effective_date 仅从 YYYYMMDD 索引解析；未经事件级核验，"
                "不等同现金分红账本或已验证复权序列。"
            ),
        )
        batch_ids.append(str(batch_id))
        inserted_batches += int(inserted)
        factor_rows += len(records)
        empty_symbols += empty_count
        completed = min(offset + len(symbol_chunk), len(symbols))
        print(
            f"MiniQMT: {completed}/{len(symbols)} symbols; "
            f"staged factors in this chunk={len(records)}"
        )
    return {
        "symbols_requested": len(symbols),
        "symbols_without_factors": empty_symbols,
        "factor_rows": factor_rows,
        "batch_ids": batch_ids,
        "inserted_batches": inserted_batches,
    }


def main() -> int:
    """抓取 AkShare 当前成分快照，再抓取其与 PG 历史成分并集的因子。"""
    symbols, snapshot_date, constituent_batch, constituent_inserted = (
        _sync_akshare_snapshot()
    )
    print(
        f"AkShare: CSI 300 snapshot date={snapshot_date}, "
        f"symbols={len(symbols)}, batch_id={constituent_batch}, "
        f"inserted={constituent_inserted}"
    )
    factor_summary = _sync_miniqmt_factors(symbols)
    print(
        "MiniQMT: "
        f"symbols={factor_summary['symbols_requested']}, "
        f"symbols_without_factors={factor_summary['symbols_without_factors']}, "
        f"factor_rows={factor_summary['factor_rows']}, "
        f"batches={len(factor_summary['batch_ids'])}, "
        f"inserted_batches={factor_summary['inserted_batches']}"
    )
    print(f"MiniQMT batch IDs: {', '.join(factor_summary['batch_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""回测审计分析工具

基于 PostgreSQL 审计日志，提供回测结果分析、风险指标计算、多策略对比
等功能。同时提供可视化所需的结构化 JSON 数据导出接口。

全量迁移 PostgreSQL 后，审计日志与价格行情统一存储于 PG（``core.pg``
裁决连接参数），不再有 DuckDB 本地文件。所有连接默认 read_only，
遵循单写者纪律（写者仅 PostgresAuditProvider / DataCache 写入路径）。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
from loguru import logger

from long_earn.core.pg import pg_connect

# 风险指标计算所需的最小日收益率数据点数
_MIN_DAILY_RETURNS_FOR_RISK = 2

# 有效回测的最小成交笔数（FILL < 此值视为无效记录，避免冒烟/调试 run 混入看板）
_MIN_VALID_FILLS = 5

# 审计日志表名（PG schema 与 DuckDB 时代一致）
_AUDIT_TABLE = '"backtest_audit".logs'


class BacktestAnalyzer:
    """
    回测审计分析工具

    允许 Agent 通过 SQL 查询 PostgreSQL 审计日志，使用 Polars 进行数据分析，
    并导出可视化所需的结构化 JSON 数据。

    所有连接默认 read_only（删除等写操作走独立的可写连接并立即提交）。
    """

    def _get_conn(self) -> Any:
        # 只读连接：审计/分析消费侧一律只读，遵循单写者纪律。
        # row_factory=None：全模块查询均以 row[N] 元组下标访问（保持
        # DuckDB 时代 fetchall 契约；PG jsonb 由 psycopg 反序列化为 dict，
        # 经 _payload_as_dict 统一兼容）。
        return pg_connect(read_only=True, row_factory=None)

    def _get_writable_conn(self) -> Any:
        # 可写连接：仅在删除操作时短暂使用，用完立即关闭。
        # 与只读连接一致返回元组行（row_factory=None）：delete_run 以
        # row[0] 下标访问，默认 dict_row 行会抛 KeyError 被吞掉、返回 0，
        # 导致删除接口误报 "Run not found"（历史 bug）。
        return pg_connect(row_factory=None)

    @staticmethod
    def _rows_to_pl(conn: Any, query: str, params: list[Any]) -> pl.DataFrame:
        """执行查询并转为 polars DataFrame（psycopg 无 .pl()）。"""
        cur = conn.execute(query, params)
        if cur.description is None:
            return pl.DataFrame()
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
        return pl.DataFrame(rows, schema=cols, orient="row")

    @staticmethod
    def _payload_as_dict(payload: Any) -> dict[str, Any]:
        """审计 payload 统一转 dict。

        PG jsonb 列经 psycopg 自动反序列化为 dict；兼容旧数据/字符串场景。
        """
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {}
        return dict(payload or {})

    # ── 基础查询接口 ──────────────────────────────────────────────────

    def get_runs_summary(self) -> list[dict[str, Any]]:
        """获取所有回测运行的汇总信息（含总收益、夏普、交易数、事件数、策略名）。

        从 RUN_END 事件 payload 提取 total_return/sharpe_ratio/trade_count，
        从 RUN_START 事件 payload 提取 strategy_id（作为回测方法标题），
        同时统计每个 run 的 FILL 事件数（trade_count）和总事件数（event_count）。

        Returns:
            按启动时间降序排列的 run 列表
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT
                    r.run_id,
                    r.started,
                    r.payload,
                    COALESCE(f.fill_count, 0) AS fill_count,
                    COALESCE(e.event_count, 0) AS event_count,
                    s.strategy_id,
                    s.tags
                FROM (
                    SELECT run_id, MIN(timestamp) AS started,
                        (array_agg(payload ORDER BY timestamp))[1] AS payload
                    FROM {_AUDIT_TABLE}
                    WHERE event_type = 'RUN_END'
                    GROUP BY run_id
                ) r
                LEFT JOIN (
                    SELECT run_id, COUNT(*) AS fill_count
                    FROM {_AUDIT_TABLE}
                    WHERE event_type = 'FILL'
                    GROUP BY run_id
                ) f ON r.run_id = f.run_id
                LEFT JOIN (
                    SELECT run_id, COUNT(*) AS event_count
                    FROM {_AUDIT_TABLE}
                    GROUP BY run_id
                ) e ON r.run_id = e.run_id
                LEFT JOIN (
                    SELECT run_id,
                        (array_agg(payload->>'strategy_id' ORDER BY timestamp))[1]
                            AS strategy_id,
                        (array_agg(payload->'tags' ORDER BY timestamp))[1]
                            AS tags
                    FROM {_AUDIT_TABLE}
                    WHERE event_type = 'RUN_START'
                    GROUP BY run_id
                ) s ON r.run_id = s.run_id
                ORDER BY r.started DESC
                """
            ).fetchall()
        except Exception:
            conn.close()
            return []

        conn.close()
        runs: list[dict[str, Any]] = []
        for row in rows:
            run_id, started, payload, fill_count, event_count, strategy_id, tags = row
            total_return = 0.0
            sharpe = 0.0
            trade_count = fill_count
            if payload:
                pl_data = self._payload_as_dict(payload)
                try:
                    total_return = float(pl_data.get("total_return", 0))
                    sharpe = float(pl_data.get("sharpe_ratio", 0))
                    trade_count = int(pl_data.get("trade_count", fill_count))
                except (ValueError, TypeError):
                    pass
            runs.append(
                {
                    "run_id": run_id,
                    "started": str(started) if started else "",
                    "total_return": round(total_return, 6),
                    "sharpe": round(sharpe, 4),
                    "trade_count": trade_count,
                    "event_count": event_count,
                    "strategy_id": str(strategy_id) if strategy_id else "",
                    "tags": list(tags or []),
                }
            )
        return runs

    def delete_run(self, run_id: str) -> int:
        """删除指定回测运行的所有审计日志。

        Returns:
            删除的记录数
        """
        conn = self._get_writable_conn()
        try:
            # 先统计待删除记录数
            row = conn.execute(
                f"SELECT COUNT(*) FROM {_AUDIT_TABLE} WHERE run_id = %s",
                [run_id],
            ).fetchone()
            count = row[0] if row else 0
            if count == 0:
                conn.close()
                return 0
            conn.execute(
                f"DELETE FROM {_AUDIT_TABLE} WHERE run_id = %s",
                [run_id],
            )
            conn.commit()
            conn.close()
            logger.info(f"删除回测运行 {run_id}: {count} 条记录")
            return count
        except Exception as e:
            logger.error(f"删除回测运行 {run_id} 失败: {e}")
            conn.close()
            return 0

    def get_empty_or_error_runs(self) -> list[str]:
        """获取无效回测运行的 run_id 列表。

        无效口径（与项目维护约定一致）：
        - 空跑：无 FILL 事件
        - 错误：有 RUN_ERROR 事件
        - test 标签：RUN_START payload.tags 含 ``RUN_TAG_TEST``（"test"，
          测试/冒烟回测专用标签）**且不含 ``RUN_TAG_PROD``（"prod"，
          生产策略 DSL ``kind: production`` 自动携带，清理豁免）
        - 孤儿：无 RUN_END 事件（引擎 DATA_EMPTY 等路径直接 return 未写
          RUN_END，此类 run 含 FILL 但看板不显示、无汇总指标）
        - 成交过少：FILL 笔数 < ``_MIN_VALID_FILLS``（冒烟/调试 run）
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT DISTINCT r.run_id
                FROM {_AUDIT_TABLE} r
                WHERE
                    -- 空跑：无 FILL
                    r.run_id NOT IN (
                        SELECT DISTINCT run_id FROM {_AUDIT_TABLE}
                        WHERE event_type = 'FILL'
                    )
                    -- 错误：有 RUN_ERROR
                    OR r.run_id IN (
                        SELECT DISTINCT run_id FROM {_AUDIT_TABLE}
                        WHERE event_type = 'RUN_ERROR'
                    )
                    -- test 标签（且不含 prod 豁免）：RUN_START payload.tags
                    -- 含 'test' 且不含 'prod'（kind: production 自动带 prod）
                    OR r.run_id IN (
                        SELECT DISTINCT run_id FROM {_AUDIT_TABLE}
                        WHERE event_type = 'RUN_START'
                          AND payload->'tags' ? 'test'
                          AND NOT (payload->'tags' ? 'prod')
                    )
                    -- 孤儿：无 RUN_END
                    OR r.run_id NOT IN (
                        SELECT DISTINCT run_id FROM {_AUDIT_TABLE}
                        WHERE event_type = 'RUN_END'
                    )
                    -- 成交过少：FILL 笔数 < {_MIN_VALID_FILLS}
                    OR r.run_id IN (
                        SELECT run_id FROM {_AUDIT_TABLE}
                        WHERE event_type = 'FILL'
                        GROUP BY run_id
                        HAVING COUNT(*) < {_MIN_VALID_FILLS}
                    )
                """
            ).fetchall()
            conn.close()
            return [row[0] for row in rows]
        except Exception:
            conn.close()
            return []

    def get_zero_return_runs(self) -> list[str]:
        """获取收益率为 0 的回测运行 run_id 列表。

        从 RUN_END 事件 payload 中提取 total_return，筛选出精确等于 0 的 run。
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT run_id
                FROM {_AUDIT_TABLE}
                WHERE event_type = 'RUN_END'
                  AND payload IS NOT NULL
                  AND (payload->>'total_return')::float8 = 0
                GROUP BY run_id
                """
            ).fetchall()
            conn.close()
            return [row[0] for row in rows]
        except Exception:
            conn.close()
            return []

    def get_run_summary(self, run_id: str) -> pl.DataFrame:
        """获取特定运行 ID 的审计统计概要"""
        conn = self._get_conn()
        try:
            return self._rows_to_pl(
                conn,
                f"""
                SELECT event_type, status, COUNT(*) as count
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s
                GROUP BY event_type, status
                """,
                [run_id],
            )
        finally:
            conn.close()

    def trace_trade_lifecycle(self, trace_id: str) -> pl.DataFrame:
        """还原一个交易的完整因果链条"""
        conn = self._get_conn()
        try:
            related_ids: set[str] = {trace_id}

            current_id = trace_id
            while True:
                res = conn.execute(
                    f"SELECT parent_id FROM {_AUDIT_TABLE} WHERE trace_id = %s LIMIT 1",
                    [current_id],
                ).fetchone()
                if not res or not res[0]:
                    break
                current_id = res[0]
                related_ids.add(current_id)

            queue = list(related_ids)
            visited: set[str] = set()
            while queue:
                curr = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)
                res = conn.execute(
                    f"SELECT trace_id FROM {_AUDIT_TABLE} WHERE parent_id = %s",
                    [curr],
                ).fetchall()
                for row in res:
                    if row[0] not in related_ids:
                        related_ids.add(row[0])
                        queue.append(row[0])

            query = (
                f"SELECT * FROM {_AUDIT_TABLE} WHERE trace_id IN ("
                + ",".join(["%s"] * len(related_ids))
                + ") ORDER BY timestamp ASC"
            )
            return self._rows_to_pl(conn, query, list(related_ids))
        finally:
            conn.close()

    def analyze_rejected_events(
        self, run_id: str, event_type: str | None = None
    ) -> pl.DataFrame:
        """分析被拦截或失败的事件"""
        conn = self._get_conn()
        try:
            query = (
                f"SELECT * FROM {_AUDIT_TABLE} "
                "WHERE run_id = %s AND status != 'SUCCESS'"
            )
            params: list[Any] = [run_id]
            if event_type:
                query += " AND event_type = %s"
                params.append(event_type)

            return self._rows_to_pl(conn, query, params)
        finally:
            conn.close()

    def run_custom_query(
        self, query: str, params: list[Any] | None = None
    ) -> pl.DataFrame:
        """允许 Agent 执行自定义 SQL 查询（PostgreSQL 方言，参数占位符 %s）"""
        if params is None:
            params = []
        try:
            conn = self._get_conn()
            try:
                return self._rows_to_pl(conn, query, params)
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Custom audit query failed: {e}")
            return pl.DataFrame()

    # ── 增强分析接口 ──────────────────────────────────────────────────

    def get_daily_returns(self, run_id: str) -> pl.DataFrame:
        """从审计日志推导日收益率序列

        从 MARKET_DATA 事件中提取每日最后的 portfolio_value，
        计算日收益率 (r_t = (v_t - v_{t-1}) / v_{t-1})。

        交易日以 payload 中的 bar 时间戳（``timestamp`` 字段）为准，
        而不是审计日志的数据库写入时间戳——同一次回测的全部 MARKET_DATA
        事件在同一时刻批量落库，按写入时间分组会把所有交易日折叠为同一天，
        导致日收益率退化为 1 行、风险指标全为 0（历史 bug）。

        Returns:
            DataFrame 包含 date (Date), portfolio_value (Float64),
            daily_return (Float64)
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT timestamp, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'MARKET_DATA'
                ORDER BY timestamp ASC
                """,
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return pl.DataFrame(
                schema={
                    "date": pl.Date,
                    "portfolio_value": pl.Float64,
                    "daily_return": pl.Float64,
                }
            )

        daily_data: list[dict[str, Any]] = []
        for row in rows:
            db_ts = row[0]
            payload = self._payload_as_dict(row[1])
            value = float(payload.get("portfolio_value", 0) or 0)
            # 优先取 payload 中的 bar 时间戳（交易日），缺失时回退数据库写入时间
            bar_ts = payload.get("timestamp", "")
            dt: datetime | None = None
            if bar_ts:
                try:
                    dt = datetime.fromisoformat(bar_ts)
                except (ValueError, TypeError):
                    dt = None
            if dt is None and isinstance(db_ts, datetime):
                dt = db_ts
            if dt is not None:
                daily_data.append({"date": dt.date(), "value": value, "ts": dt})
            else:
                daily_data.append({"date": None, "value": value, "ts": None})

        df_raw = pl.DataFrame(daily_data)
        if df_raw.is_empty():
            return pl.DataFrame(
                schema={
                    "date": pl.Date,
                    "portfolio_value": pl.Float64,
                    "daily_return": pl.Float64,
                }
            )

        # 按日期取最后一个快照
        df_daily = (
            df_raw.filter(pl.col("date").is_not_null())
            .sort("ts")
            .group_by("date")
            .agg(pl.col("value").last().alias("portfolio_value"))
            .sort("date")
        )

        # 计算日收益率
        df_daily = df_daily.with_columns(
            (
                (pl.col("portfolio_value") - pl.col("portfolio_value").shift(1))
                / pl.col("portfolio_value").shift(1)
            ).alias("daily_return")
        )

        return df_daily.select(["date", "portfolio_value", "daily_return"])

    def get_risk_metrics(self, run_id: str) -> dict[str, Any]:
        """计算风险指标

        基于日收益率序列计算:
        - 总收益率 (total_return)
        - 年化收益率 (annual_return, 算术年化 mean * 252，与引擎一致)
        - 年化波动率 (annual_volatility)
        - 夏普比率 (sharpe_ratio, 与引擎一致：算术年化收益 / 年化波动率，
          不额外扣除无风险利率)
        - 最大回撤 (max_drawdown)
        - 最大回撤持续天数 (max_drawdown_duration_days)
        - VaR 95% (var_95)
        - VaR 99% (var_99)
        - CVaR 95% (cvar_95, 条件 VaR / 期望损失)

        Returns:
            包含各项风险指标的字典
        """
        daily = self.get_daily_returns(run_id)
        if (
            daily.is_empty()
            or daily["daily_return"].drop_nulls().len() < _MIN_DAILY_RETURNS_FOR_RISK
        ):
            return {
                "total_return": 0.0,
                "annual_return": 0.0,
                "annual_volatility": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "max_drawdown_duration_days": 0,
                "var_95": 0.0,
                "var_99": 0.0,
                "cvar_95": 0.0,
            }

        returns_series = daily["daily_return"].drop_nulls()
        portfolio_series = daily["portfolio_value"]

        # 总收益率
        start_val = portfolio_series[0]
        end_val = portfolio_series[-1]
        total_return = (
            float((end_val - start_val) / start_val) if start_val > 0 else 0.0
        )

        # 年化收益率：算术年化 mean(daily_return) * 252（与引擎 _calculate_metrics
        # 一致；几何年化在亏损/短样本下与引擎结果差异显著，会造成列表页与详情页
        # 数值对不上）
        # polars 聚合结果可能为 None（空序列）或 timedelta（duration 列），
        # 此处仅接受数值列，防御性收窄后再转 float
        _std = returns_series.std()
        daily_vol = float(_std) if isinstance(_std, (int, float)) else 0.0
        annual_volatility = daily_vol * (252**0.5)
        _mean = returns_series.mean()
        annual_return = (float(_mean) if isinstance(_mean, (int, float)) else 0.0) * 252

        # 夏普比率：与引擎一致（算术年化收益 / 年化波动率，不扣无风险利率）。
        # 原实现额外减 0.02 且年化口径用几何，导致与列表页（引擎 RUN_END 值）
        # 显示不一致。
        sharpe_ratio = (
            float(annual_return / annual_volatility) if annual_volatility > 0 else 0.0
        )

        # 最大回撤 & 持续天数
        cumulative_max = portfolio_series.cum_max()
        drawdowns = (portfolio_series - cumulative_max) / cumulative_max
        _min = drawdowns.min()
        max_drawdown = float(_min) if isinstance(_min, (int, float)) else 0.0

        # 最大回撤持续天数
        in_drawdown = (drawdowns < 0).cast(pl.Int32)
        in_drawdown_list: list[int] = in_drawdown.to_list()

        max_duration = 0
        current_duration = 0
        for v in in_drawdown_list:
            if v == 1:
                current_duration += 1
                max_duration = max(max_duration, current_duration)
            else:
                current_duration = 0

        # VaR & CVaR (基于日收益率，升序排列后取分位)
        returns_list = returns_series.sort().to_list()
        if returns_list:
            n = len(returns_list)
            var_95 = float(returns_list[max(int(n * 0.05), 0)])
            var_99 = float(returns_list[max(int(n * 0.01), 0)])

            # CVaR 95%: 最差 5% 样本的期望损失。原实现用 `r <= var_95` 收集尾部，
            # 当 var_95 恰好落在常见值（如大量 0 收益）时会纳入远超 5% 的样本，
            # 把 CVaR 拉向 0 导致低估风险；改为严格取排序后前 k 个最差样本。
            k = max(int(n * 0.05), 1)
            tail_95 = returns_list[:k]
            cvar_95 = float(sum(tail_95) / len(tail_95))
        else:
            var_95 = 0.0
            var_99 = 0.0
            cvar_95 = 0.0

        return {
            "total_return": round(total_return, 6),
            "annual_return": round(annual_return, 6),
            "annual_volatility": round(annual_volatility, 6),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "max_drawdown": round(max_drawdown, 6),
            "max_drawdown_duration_days": max_duration,
            "var_95": round(var_95, 6),
            "var_99": round(var_99, 6),
            "cvar_95": round(cvar_95, 6),
        }

    def compare_runs(self, run_ids: list[str]) -> pl.DataFrame:
        """多策略对比分析

        对每个 run_id 计算关键绩效指标，返回对比表。

        Args:
            run_ids: 回测运行 ID 列表

        Returns:
            包含每列指标对比结果的 DataFrame
        """
        rows: list[dict[str, Any]] = []
        for rid in run_ids:
            metrics = self.get_risk_metrics(rid)

            # 获取交易统计
            conn = self._get_conn()
            try:
                trade_count_row = conn.execute(
                    f"SELECT COUNT(*) FROM {_AUDIT_TABLE} "
                    "WHERE run_id = %s AND event_type = 'FILL'",
                    [rid],
                ).fetchone()
            finally:
                conn.close()
            trade_count = trade_count_row[0] if trade_count_row else 0

            rows.append(
                {
                    "run_id": rid,
                    "total_return": metrics["total_return"],
                    "annual_return": metrics["annual_return"],
                    "annual_volatility": metrics["annual_volatility"],
                    "sharpe_ratio": metrics["sharpe_ratio"],
                    "max_drawdown": metrics["max_drawdown"],
                    "max_drawdown_duration_days": metrics["max_drawdown_duration_days"],
                    "var_95": metrics["var_95"],
                    "var_99": metrics["var_99"],
                    "cvar_95": metrics["cvar_95"],
                    "trade_count": trade_count,
                }
            )

        return pl.DataFrame(rows)

    # ── 可视化导出接口 ──────────────────────────────────────────────

    def export_equity_curve(self, run_id: str) -> list[dict[str, Any]]:
        """导出权益曲线数据（用于折线图）

        时间轴取 payload 中的 bar 日期（``timestamp`` 字段）而非数据库写入
        时间戳——同一次回测的 MARKET_DATA 在同一时刻批量落库，若用写入时间
        x 轴会全部挤在同一天。
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT timestamp, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'MARKET_DATA'
                ORDER BY timestamp ASC
                """,
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        points: list[dict[str, Any]] = []
        for row in rows:
            db_ts = row[0]
            payload = self._payload_as_dict(row[1])
            value = float(payload.get("portfolio_value", 0) or 0)
            bar_ts = payload.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(bar_ts) if bar_ts else None
            except (ValueError, TypeError):
                dt = None
            if dt is None and isinstance(db_ts, datetime):
                dt = db_ts
            points.append(
                {
                    "time": str(dt.date()) if dt is not None else str(db_ts or ""),
                    "value": value,
                }
            )
        return points

    def export_trade_journal(self, run_id: str) -> list[dict[str, Any]]:
        """导出完整交易日志（用于表格/交易明细）"""
        conn = self._get_conn()
        try:
            fills = conn.execute(
                f"""
                SELECT timestamp, trace_id, parent_id, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'FILL'
                ORDER BY timestamp ASC
                """,
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        journal: list[dict[str, Any]] = []
        for row in fills:
            payload = self._payload_as_dict(row[3])
            # 优先使用 payload 中的 bar_date（市场交易日），
            # 旧数据无 bar_date 时回退到 timestamp（引擎时间，不够准确）
            bar_date = payload.get("bar_date", "")
            if not bar_date:
                ts_str = str(row[0]) if row[0] else ""
                bar_date = ts_str[:10] if ts_str else ""
            else:
                bar_date = str(bar_date)[:10]
            journal.append(
                {
                    "time": bar_date,
                    "trace_id": row[1],
                    "symbol": payload.get("symbol", ""),
                    "type": payload.get("type", ""),
                    "price": float(payload.get("price", 0)),
                    "quantity": float(payload.get("quantity", 0)),
                    "portfolio_value": float(payload.get("portfolio_value", 0)),
                    "reason": payload.get("reason", ""),
                }
            )
        # 附加每笔成交的审计归因链（SIGNAL→ORDER→FILL / RISK_TRIGGER→ORDER→FILL）
        attribution = self.export_trade_attribution(run_id)
        for t in journal:
            t["attribution"] = attribution.get(t["trace_id"])
        return journal

    def export_trade_attribution(self, run_id: str) -> dict[str, dict[str, Any]]:
        """还原每条 FILL 的审计归因链。

        通过 parent_id 因果关联，把每笔成交还原到上游 ORDER 与再上游的
        SIGNAL（策略选股/目标权重）或 RISK_TRIGGER（风控触发数值）。

        Returns:
            {fill_trace_id: attribution}，attribution 结构：
            - kind: signal/risk/direct/pending/unknown
            - order: 订单信息（symbol/type/quantity）
            - signal: 上游信号（strategy_id/signals/risk_triggered）
            - risk_trigger: 上游风控触发详情（risk_type/触发数值等）
            - chain: {fill, order, upstream} 三个 trace_id 供前端展示
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT trace_id, parent_id, event_type, component, status, timestamp, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s
                  AND event_type IN ('FILL', 'ORDER', 'SIGNAL', 'RISK_TRIGGER')
                """,
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        # 每个 trace 一组事件记录（trace → event_type → entry）：确定性
        # trace_id 落地后信号/订单/成交沿因果链共享同一 trace（见
        # domain.entities.bar_trace_id），单层索引会互相覆盖，按
        # event_type 二级索引；parent/payload 用于链解析，
        # component/status/timestamp 用于 hover 摘要
        by_trace: dict[str, dict[str, dict[str, Any]]] = {}
        fills: list[tuple[str, str, dict[str, Any]]] = []
        for trace, parent, etype, component, status, ts, payload in rows:
            p = self._payload_as_dict(payload)
            by_trace.setdefault(trace, {})[etype] = {
                "parent": parent or "",
                "event_type": etype,
                "payload": p,
                "component": component,
                "status": status,
                "timestamp": ts,
            }
            if etype == "FILL":
                fills.append((trace, parent or "", p))

        return {
            fill_trace: self._resolve_trade_attribution(fill_trace, parent, by_trace)
            for fill_trace, parent, _ in fills
        }

    @classmethod
    def _resolve_trade_attribution(
        cls,
        fill_trace: str,
        order_trace_id: str,
        by_trace: dict[str, dict[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        """解析单笔 FILL 的上游归因链（2 跳：FILL→ORDER→SIGNAL/RISK_TRIGGER）。"""
        # 待成交单（限价/止损触发）parent 形如 pend_xxx，无独立 ORDER 事件
        if not order_trace_id or order_trace_id.startswith("pend_"):
            return {
                "kind": "pending",
                "order": None,
                "signal": None,
                "risk_trigger": None,
                "chain": {
                    "fill": fill_trace,
                    "order": "",
                    "upstream": "",
                    "events": cls._build_chain_events(fill_trace, "", "", by_trace),
                },
            }

        # 同 trace 事件组里按事件类型取节点（信号链上 FILL/ORDER/SIGNAL
        # 共享 trace，见 bar_trace_id 的确定性派生约定）
        order_entry = by_trace.get(order_trace_id, {}).get("ORDER")
        order = None
        upstream_trace = ""
        upstream_type = ""
        upstream_payload: dict[str, Any] = {}
        if order_entry is not None:
            order = order_entry["payload"]
            upstream_trace = order_entry["parent"]
            up_events = by_trace.get(upstream_trace, {})
            for candidate in ("SIGNAL", "RISK_TRIGGER"):
                up_entry = up_events.get(candidate)
                if up_entry is not None:
                    upstream_type = candidate
                    upstream_payload = up_entry["payload"]
                    break

        chain = {
            "fill": fill_trace,
            "order": order_trace_id,
            "upstream": upstream_trace,
            "events": cls._build_chain_events(
                fill_trace, order_trace_id, upstream_trace, by_trace
            ),
        }
        if upstream_type == "SIGNAL":
            return {
                "kind": "signal",
                "order": order,
                "signal": upstream_payload,
                "risk_trigger": None,
                "chain": chain,
            }
        if upstream_type == "RISK_TRIGGER":
            return {
                "kind": "risk",
                "order": order,
                "signal": None,
                "risk_trigger": upstream_payload,
                "chain": chain,
            }
        if order is not None:
            # 订单存在但上游未知（如 direct_orders 直接提交）
            return {
                "kind": "direct",
                "order": order,
                "signal": None,
                "risk_trigger": None,
                "chain": chain,
            }
        return {
            "kind": "unknown",
            "order": None,
            "signal": None,
            "risk_trigger": None,
            "chain": chain,
        }

    @staticmethod
    def _event_summary_text(event_type: str, payload: dict[str, Any]) -> str:
        """把单个审计事件压缩成一句人话摘要（供前端 hover 展示，不暴露完整 payload）。"""
        sym = str(payload.get("symbol") or "")
        t = str(payload.get("type") or payload.get("order_type") or "")
        qty = payload.get("quantity")
        if not isinstance(qty, (int, float)):
            qty = payload.get("fill_quantity")
        price = payload.get("price")
        if not isinstance(price, (int, float)):
            price = payload.get("fill_price")
        if event_type == "SIGNAL":
            signals = payload.get("signals")
            n = len(signals) if isinstance(signals, dict) else 0
            sid = str(payload.get("strategy_id") or "")
            result = f"策略 {sid} · 选股 {n} 只" if n else f"策略 {sid}"
        elif event_type == "ORDER":
            q = qty if isinstance(qty, (int, float)) else "-"
            result = f"请求 {t} {sym} ×{q}".strip() if sym else f"请求 {t or '订单'}"
        elif event_type == "FILL":
            q = qty if isinstance(qty, (int, float)) else "-"
            p = f"{price:.2f}" if isinstance(price, (int, float)) else "-"
            reason = payload.get("reason")
            base = f"{t} {sym} ×{q} @ {p}".strip()
            result = f"{base} · {reason}" if reason else base
        elif event_type == "RISK_TRIGGER":
            risk_type = str(payload.get("risk_type") or "")
            pnl = payload.get("pnl_pct")
            pnl_s = f"{pnl * 100:.1f}%" if isinstance(pnl, (int, float)) else "-"
            dd = payload.get("drawdown")
            dd_s = f"{dd * 100:.1f}%" if isinstance(dd, (int, float)) else "-"
            if risk_type == "stop_loss":
                result = f"止损触发 · {sym}（跌幅 {pnl_s}）"
            elif risk_type == "take_profit":
                result = f"止盈触发 · {sym}（涨幅 {pnl_s}）"
            elif risk_type == "max_drawdown":
                result = f"最大回撤触发（回撤 {dd_s}）"
            else:
                result = f"{risk_type or '风控'}触发 · {sym}".strip(" ·")
        else:
            result = event_type
        return result

    @classmethod
    def _build_chain_events(
        cls,
        fill_trace: str,
        order_trace: str,
        upstream_trace: str,
        by_trace: dict[str, dict[str, dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        """按审计链节点（upstream/order/fill）构造紧凑事件摘要，供前端 hover 展示。

        同 trace 事件组里按节点语义选事件类型：upstream 优先
        SIGNAL/RISK_TRIGGER，order 取 ORDER，fill 取 FILL。
        """
        nodes: tuple[tuple[str, str, tuple[str, ...]], ...] = (
            ("upstream", upstream_trace, ("SIGNAL", "RISK_TRIGGER", "ORDER")),
            ("order", order_trace, ("ORDER",)),
            ("fill", fill_trace, ("FILL",)),
        )
        events: dict[str, dict[str, Any]] = {}
        for key, trace, preferred_types in nodes:
            if not trace:
                continue
            group = by_trace.get(trace)
            if not group:
                continue
            ent = next((group[t] for t in preferred_types if t in group), None)
            if ent is None:
                continue
            events[key] = {
                "event_type": ent["event_type"],
                "component": ent["component"],
                "status": ent["status"],
                "timestamp": str(ent["timestamp"]) if ent["timestamp"] else None,
                "summary": cls._event_summary_text(ent["event_type"], ent["payload"]),
            }
        return events

    def export_audit_event(self, run_id: str, trace_id: str) -> list[dict[str, Any]]:
        """按 trace_id 返回该事件的完整审计行（含原始 payload，供前端点击下钻核验）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT event_type, component, status, timestamp, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND trace_id = %s
                ORDER BY seq ASC
                """,
                [run_id, trace_id],
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "event_type": r[0],
                "component": r[1],
                "status": r[2],
                "timestamp": str(r[3]) if r[3] else None,
                "payload": self._payload_as_dict(r[4]),
            }
            for r in rows
        ]

    def export_signal_history(self, run_id: str) -> list[dict[str, Any]]:
        """导出信号历史（用于分析策略决策点）"""
        conn = self._get_conn()
        try:
            signals = conn.execute(
                f"""
                SELECT timestamp, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'SIGNAL'
                ORDER BY timestamp ASC
                """,
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        return [
            {
                "time": str(row[0]) if row[0] else "",
                "signals": self._payload_as_dict(row[1]).get("signals", ""),
            }
            for row in signals
        ]

    def export_dashboard_data(self, run_id: str) -> dict[str, Any]:
        """导出仪表盘所需的完整数据集

        包含权益曲线、交易日志、信号历史、事件统计、基准指标和风险指标。
        """
        conn = self._get_conn()
        try:
            perf = conn.execute(
                f"""
                SELECT event_type, COUNT(*) as count
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s
                GROUP BY event_type
                ORDER BY count DESC
                """,
                [run_id],
            ).fetchall()

            total_events = sum(row[1] for row in perf)
            event_breakdown: dict[str, int] = {row[0]: row[1] for row in perf}

            first_ts = conn.execute(
                f"SELECT MIN(timestamp) FROM {_AUDIT_TABLE} WHERE run_id = %s",
                [run_id],
            ).fetchone()[0]
            last_ts = conn.execute(
                f"SELECT MAX(timestamp) FROM {_AUDIT_TABLE} WHERE run_id = %s",
                [run_id],
            ).fetchone()[0]

            # 尝试从 MARKET_DATA 载荷中提取回测基准指标
            bm_row = conn.execute(
                f"""
                SELECT payload FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'MARKET_DATA'
                ORDER BY timestamp DESC LIMIT 1
                """,
                [run_id],
            ).fetchone()
        finally:
            conn.close()

        bm: dict[str, Any] = {}
        if bm_row:
            pl_data = self._payload_as_dict(bm_row[0])
            if "benchmark" in pl_data:
                bm = pl_data["benchmark"]

        # 附加风险指标
        risk = self.get_risk_metrics(run_id)

        # 附加交易标的列表（供仪表盘展示个股图表）
        traded_symbols = self.get_traded_symbols(run_id)

        return {
            "run_id": run_id,
            "total_events": total_events,
            "event_breakdown": event_breakdown,
            "time_range": {
                "start": str(first_ts) if first_ts else "",
                "end": str(last_ts) if last_ts else "",
            },
            "equity_curve": self.export_equity_curve(run_id),
            "trade_journal": self.export_trade_journal(run_id),
            "signal_history": self.export_signal_history(run_id),
            "benchmark": bm,
            "risk_metrics": risk,
            "traded_symbols": traded_symbols,
        }

    # ── 交易数据导出与可视化接口 ──────────────────────────────────────

    def get_traded_symbols(self, run_id: str) -> list[str]:
        """获取某次回测中所有被交易过的标的代码（去重，按代码升序）"""
        traces = self.export_trade_traces(run_id)
        symbols = sorted({t["symbol"] for t in traces if t["symbol"]})
        return symbols

    def export_trade_traces(self, run_id: str) -> list[dict[str, Any]]:
        """导出完整交易日志（含时间、代码、方向、价格、数量、金额、持仓市值）

        每条记录对应一次成交（FILL 事件），金额 = 价格 × 数量。
        """
        conn = self._get_conn()
        try:
            fills = conn.execute(
                f"""
                SELECT timestamp, trace_id, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'FILL'
                ORDER BY timestamp ASC
                """,
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        traces: list[dict[str, Any]] = []
        for row in fills:
            payload = self._payload_as_dict(row[2])
            price = float(payload.get("price", 0))
            quantity = float(payload.get("quantity", 0))
            # 优先使用 payload 中的 bar_date（市场交易日），
            # 旧数据无 bar_date 时回退到 timestamp（引擎时间，不够准确）
            bar_date = payload.get("bar_date", "")
            if not bar_date:
                ts_str = str(row[0]) if row[0] else ""
                bar_date = ts_str[:10] if ts_str else ""
            else:
                bar_date = str(bar_date)[:10]
            traces.append(
                {
                    "time": bar_date,
                    "trace_id": row[1],
                    "symbol": payload.get("symbol", ""),
                    "direction": payload.get("type", ""),
                    "price": price,
                    "quantity": quantity,
                    "amount": round(price * quantity, 2),
                    "portfolio_value": float(payload.get("portfolio_value", 0)),
                    "reason": payload.get("reason", ""),
                }
            )
        return traces

    def export_trade_traces_to_file(
        self,
        run_id: str,
        output_path: str | Path,
        fmt: str = "csv",
    ) -> Path:
        """导出交易日志到文件

        Args:
            run_id: 回测运行 ID
            output_path: 输出文件路径
            fmt: 文件格式，'csv' 或 'json'

        Returns:
            实际写入的文件路径（自动补全后缀）
        """
        output_path = Path(output_path)
        traces = self.export_trade_traces(run_id)

        if fmt.lower() == "json":
            if output_path.suffix.lower() not in {".json", ".jsonl"}:
                output_path = output_path.with_suffix(".json")
            output_path.write_text(
                json.dumps(
                    {"run_id": run_id, "trade_count": len(traces), "trades": traces},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        else:
            if output_path.suffix.lower() != ".csv":
                output_path = output_path.with_suffix(".csv")
            df = (
                pl.DataFrame(traces)
                if traces
                else pl.DataFrame(
                    schema={
                        "time": pl.Utf8,
                        "trace_id": pl.Utf8,
                        "symbol": pl.Utf8,
                        "direction": pl.Utf8,
                        "price": pl.Float64,
                        "quantity": pl.Float64,
                        "amount": pl.Float64,
                        "portfolio_value": pl.Float64,
                    }
                )
            )
            df.write_csv(str(output_path))

        logger.info(f"交易日志已导出: {output_path} ({len(traces)} 笔)")
        return output_path

    def export_symbol_chart_data(self, run_id: str, symbol: str) -> dict[str, Any]:
        """导出单只标的的价格走势 + 买卖点标注数据（用于绘制图表）

        从 price_daily 表读取该标的的日线行情（open/high/low/close/volume），
        并从审计日志中提取该标的的所有成交点（买入/卖出），返回结构化数据
        供前端绘制价格走势图并在图上标注买卖时间、价格、数量。

        Args:
            run_id: 回测运行 ID
            symbol: 标的代码

        Returns:
            dict 包含 symbol、price_history（日期/开/高/低/收/量）、
            trade_points（时间/方向/价格/数量/金额）
        """
        # 1. 读取该标的的价格走势（price_daily 表，PG 统一存储）
        price_conn = pg_connect(read_only=True, row_factory=None)
        try:
            price_rows = price_conn.execute(
                """
                SELECT date, open, high, low, close, volume
                FROM price_daily
                WHERE symbol = %s
                ORDER BY date ASC
                """,
                [symbol],
            ).fetchall()
        finally:
            price_conn.close()

        price_history: list[dict[str, Any]] = []
        for row in price_rows:
            price_history.append(
                {
                    "date": str(row[0]) if row[0] else "",
                    "open": float(row[1]) if row[1] is not None else None,
                    "high": float(row[2]) if row[2] is not None else None,
                    "low": float(row[3]) if row[3] is not None else None,
                    "close": float(row[4]) if row[4] is not None else None,
                    "volume": float(row[5]) if row[5] is not None else 0.0,
                }
            )

        # 2. 读取该标的的成交点（从审计日志，按 symbol 在 Python 端过滤
        #    避免 JSON 路径提取与比较的类型转换问题）
        conn = self._get_conn()
        try:
            fill_rows = conn.execute(
                f"""
                SELECT timestamp, payload
                FROM {_AUDIT_TABLE}
                WHERE run_id = %s AND event_type = 'FILL'
                ORDER BY timestamp ASC
                """,
                [run_id],
            ).fetchall()
        finally:
            conn.close()

        trade_points: list[dict[str, Any]] = []
        for row in fill_rows:
            payload = self._payload_as_dict(row[1])
            if payload.get("symbol", "") != symbol:
                continue
            price = float(payload.get("price", 0))
            quantity = float(payload.get("quantity", 0))
            # 优先使用 payload 中的 bar_date（市场交易日），
            # 旧数据无 bar_date 时回退到 timestamp（引擎时间，不够准确）
            bar_date = payload.get("bar_date", "")
            if not bar_date:
                ts_str = str(row[0]) if row[0] else ""
                bar_date = ts_str[:10] if ts_str else ""
            else:
                bar_date = str(bar_date)[:10]
            trade_points.append(
                {
                    "time": bar_date,
                    "direction": payload.get("type", ""),
                    "price": price,
                    "quantity": quantity,
                    "amount": round(price * quantity, 2),
                    "reason": payload.get("reason", ""),
                }
            )

        return {
            "symbol": symbol,
            "run_id": run_id,
            "price_history": price_history,
            "trade_points": trade_points,
        }

    def export_all_symbol_charts(self, run_id: str) -> list[dict[str, Any]]:
        """导出本次回测中所有交易标的的图表数据

        便捷方法：自动发现所有被交易过的标的，逐个导出价格走势 + 买卖点。
        """
        symbols = self.get_traded_symbols(run_id)
        return [self.export_symbol_chart_data(run_id, sym) for sym in symbols]

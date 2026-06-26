"""补全 DuckDB 缓存中的财务衍生字段

net_profit_yoy / revenue_yoy 从原始字段同比计算。
roe / gross_margin 已有 99.9% 覆盖，仅补零星缺失。
"""
import duckdb
import pandas as pd
import numpy as np
from loguru import logger

CACHE_PATH = r"D:\dev\long-earn\.cache\backtest_cache.duckdb"
conn = duckdb.connect(CACHE_PATH)

df = conn.execute("SELECT * FROM financial_quarterly ORDER BY symbol, report_date").fetchdf()
logger.info(f"共 {len(df)} 条记录, {df['symbol'].nunique()} 只股票")

df = df.sort_values(["symbol", "report_date"])
df["report_date"] = pd.to_datetime(df["report_date"])

# 同比计算：同一股票、同一季度、上一年
df_prev = df[["symbol", "report_date", "net_profit", "revenue"]].copy()
df_prev = df_prev.rename(columns={
    "report_date": "prev_report_date",
    "net_profit": "prev_net_profit",
    "revenue": "prev_revenue",
})
df_prev["prev_report_date"] = df_prev["prev_report_date"] + pd.DateOffset(years=1)

df = df.merge(
    df_prev,
    left_on=["symbol", "report_date"],
    right_on=["symbol", "prev_report_date"],
    how="left",
)

# 仅更新原本为 NULL 的 net_profit_yoy
mask_npy = df["net_profit_yoy"].isna() & df["prev_net_profit"].notna() & (df["prev_net_profit"] != 0)
df.loc[mask_npy, "net_profit_yoy"] = (
    (df.loc[mask_npy, "net_profit"] - df.loc[mask_npy, "prev_net_profit"])
    / df.loc[mask_npy, "prev_net_profit"].abs()
)

# 仅更新原本为 NULL 的 revenue_yoy
mask_ry = df["revenue_yoy"].isna() & df["prev_revenue"].notna() & (df["prev_revenue"] != 0)
df.loc[mask_ry, "revenue_yoy"] = (
    (df.loc[mask_ry, "revenue"] - df.loc[mask_ry, "prev_revenue"])
    / df.loc[mask_ry, "prev_revenue"].abs()
)

logger.info(f"补全 net_profit_yoy: {mask_npy.sum()} 条")
logger.info(f"补全 revenue_yoy: {mask_ry.sum()} 条")

df = df.drop(columns=["prev_report_date", "prev_net_profit", "prev_revenue"])

# 注册 pandas DataFrame 到 DuckDB 并用 UPDATE FROM 批量更新
conn.register("df_updates", df)
conn.execute("BEGIN TRANSACTION")
conn.execute("""
    UPDATE financial_quarterly f
    SET
        net_profit_yoy = u.net_profit_yoy,
        revenue_yoy = u.revenue_yoy,
        roe = u.roe,
        gross_margin = u.gross_margin,
        eps = u.eps,
        net_profit = u.net_profit,
        revenue = u.revenue
    FROM df_updates u
    WHERE f.symbol = u.symbol AND f.report_date = u.report_date
""")
conn.execute("COMMIT")

logger.info("验证补全结果：")
for col in ["net_profit_yoy", "revenue_yoy", "roe", "gross_margin"]:
    null_cnt = conn.execute(f"SELECT COUNT(*) FROM financial_quarterly WHERE {col} IS NULL").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM financial_quarterly").fetchone()[0]
    logger.info(f"  {col}: {null_cnt}/{total} = {null_cnt/total*100:.1f}% 空值")

conn.close()
logger.info("完成")

"""清理缓存中的非股票数据（债券、ETF、基金等）"""
import duckdb
from loguru import logger

CACHE_PATH = r"D:\dev\long-earn\.cache\backtest_cache.duckdb"
conn = duckdb.connect(CACHE_PATH)

# 只保留标准 A 股代码：6xxxxx.SH（沪A）、0xxxxx.SZ（深A）、3xxxxx.SZ（创业板）
# 删除：债券（75xxxx）、ETF/LOF（000xxx.SH、159xxx.SZ、16xxxx.SZ 等）、
#       8xxxxx.SH（科创板尚未上市）、9xxxxx.SH、4xxxxx、2xxxxx
before = conn.execute("SELECT COUNT(*) FROM price_daily").fetchone()[0]

conn.execute("""
    DELETE FROM price_daily 
    WHERE NOT (
        symbol LIKE '6%.SH' 
        OR symbol LIKE '0%.SZ' 
        OR symbol LIKE '3%.SZ'
    )
""")

after = conn.execute("SELECT COUNT(*) FROM price_daily").fetchone()[0]
deleted = before - after
logger.info(f"清理前: {before} 行, 清理后: {after} 行, 删除: {deleted} 行")

# 验证
remaining_symbols = conn.execute("""
    SELECT COUNT(DISTINCT symbol) FROM price_daily
""").fetchone()[0]
logger.info(f"剩余股票数: {remaining_symbols}")

conn.close()

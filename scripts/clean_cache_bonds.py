"""清理缓存中的债券代码（75xxxx）"""
import duckdb
from loguru import logger

CACHE_PATH = r"D:\dev\long-earn\.cache\backtest_cache.duckdb"
conn = duckdb.connect(CACHE_PATH)

# 检查 75xxxx 债券代码
bond_count = conn.execute("SELECT COUNT(*) FROM price_daily WHERE symbol LIKE '75%'").fetchone()[0]
logger.info(f"75xxxx 债券代码行数: {bond_count}")

if bond_count > 0:
    conn.execute("DELETE FROM price_daily WHERE symbol LIKE '75%'")
    logger.info(f"已删除 {bond_count} 条债券数据")

# 检查 24xxxx 等其他非股票代码
other = conn.execute("""
    SELECT COUNT(*) FROM price_daily 
    WHERE NOT (symbol LIKE '6%.SH' OR symbol LIKE '0%.SZ' OR symbol LIKE '3%.SZ')
""").fetchone()[0]
logger.info(f"非标准股票代码行数: {other}")

if other > 0:
    conn.execute("""
        DELETE FROM price_daily 
        WHERE NOT (symbol LIKE '6%.SH' OR symbol LIKE '0%.SZ' OR symbol LIKE '3%.SZ')
    """)
    logger.info(f"已删除 {other} 条非标准股票数据")

# 验证
remaining = conn.execute("SELECT COUNT(*) FROM price_daily").fetchone()[0]
logger.info(f"清理后行情数据: {remaining} 条")

conn.close()

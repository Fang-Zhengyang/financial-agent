import sqlite3
import inspect

import akshare as ak

print("akshare", ak.__version__)
print("sig stock_individual_fund_flow:", inspect.signature(ak.stock_individual_fund_flow))

conn = sqlite3.connect("data/akshare_cache.db")
conn.row_factory = sqlite3.Row
tables = [
    r[0]
    for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != '_cache_meta'"
    )
]
for t in tables:
    try:
        n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"{t}: {n}")
    except Exception as e:
        print(f"{t}: ERR {e}")
conn.close()

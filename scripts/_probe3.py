import sqlite3

conn = sqlite3.connect("data/akshare_cache.db")
conn.row_factory = sqlite3.Row

# per-code distinct date counts in capital_flow_eastmoney
rows = conn.execute(
    'SELECT code, COUNT(DISTINCT date) AS ndays, MIN(date) AS mn, MAX(date) AS mx '
    'FROM capital_flow_eastmoney GROUP BY code ORDER BY ndays DESC'
).fetchall()
for r in rows:
    print(dict(r))

print("--- kline per code ---")
rows = conn.execute(
    'SELECT code, COUNT(*) AS n FROM kline GROUP BY code ORDER BY n DESC'
).fetchall()
for r in rows[:20]:
    print(dict(r))
conn.close()

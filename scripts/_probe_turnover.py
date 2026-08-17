"""Quick probe: confirm live spot data has 换手率 (turnover) for the target codes."""
import akshare as ak

df = ak.stock_zh_a_spot_em()
cols = list(df.columns)
print("换手率 in columns:", "换手率" in cols)
print("量比 in columns:", "量比" in cols)

for code in ("300403", "600519", "300750"):
    row = df[df["代码"] == code]
    if row.empty:
        print(f"{code}: 未在 spot 中")
        continue
    r = row.iloc[0]
    print(f"{code} {r.get('名称')}: 最新价={r.get('最新价')} 量比={r.get('量比')} 换手率={r.get('换手率')}")

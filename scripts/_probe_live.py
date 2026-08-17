"""Probe northbound + dazong interfaces against the live eastmoney API."""
import sys
import traceback

import akshare as ak

def probe(label, fn):
    print(f"\n===== {label} =====")
    try:
        df = fn()
        if df is None:
            print("  -> returned None")
        elif hasattr(df, "empty") and df.empty:
            print("  -> returned empty DataFrame")
        else:
            print(f"  -> OK rows={len(df)} cols={list(df.columns)}")
            print(df.head(3).to_string())
    except Exception as e:
        print(f"  -> EXCEPTION {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)

# main board (known working)
probe("stock_hsgt_individual_em(600519)", lambda: ak.stock_hsgt_individual_em(symbol="600519"))
# 创业板 (the bug)
probe("stock_hsgt_individual_em(300403)", lambda: ak.stock_hsgt_individual_em(symbol="300403"))
# 创业板 detail (alt interface)
probe("stock_hsgt_individual_detail_em(300403)", lambda: ak.stock_hsgt_individual_detail_em(
    symbol="300403", start_date="20260601", end_date="20260817"))
# dazong 30d
probe("stock_dzjy_mrmx(A股 30d)", lambda: ak.stock_dzjy_mrmx(
    symbol="A股", start_date="20260718", end_date="20260817"))

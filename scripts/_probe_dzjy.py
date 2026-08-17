"""Probe akshare interfaces for dazong (大宗交易) and northbound (沪深港通)."""
import inspect

import akshare as ak

print("akshare version:", ak.__version__)

candidates = [
    "stock_dzjy_mrmx",
    "stock_dzjy_mrtj",
    "stock_dzjy_sctj",
    "stock_dzjy_mrms",
    "stock_hsgt_individual_em",
    "stock_hsgt_hold_stock_em",
    "stock_hsgt_individual_em_ths",
]

for name in candidates:
    fn = getattr(ak, name, None)
    if fn is None:
        print(f"{name}: MISSING")
        continue
    try:
        sig = inspect.signature(fn)
        print(f"{name}{sig}")
    except (ValueError, TypeError) as e:
        print(f"{name}: <no signature> {e}")
    # doc first line
    doc = (fn.__doc__ or "").strip().splitlines()
    if doc:
        print(f"    doc: {doc[0][:120]}")

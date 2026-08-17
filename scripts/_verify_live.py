"""Live verification of 300403 (创业板) — 北向/量比/换手率/大宗交易实测不崩、有数据或优雅空。"""
import io
import logging
import sys
import time

# 捕获日志输出到内存，检查是否有 tqdm/traceback 噪音
log_stream = io.StringIO()
handler = logging.StreamHandler(log_stream)
handler.setLevel(logging.DEBUG)
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.DEBUG)

from finagent.cli.main import _build_data_provider

provider = _build_data_provider()

for code in ("300403", "600519"):
    print(f"\n===== {code} =====")
    t0 = time.time()

    north = provider.get_north(code)
    print(f"  north      : {type(north).__name__} rows={len(north.rows)} "
          f"latest_shares={north.latest_hold_shares} ({(time.time()-t0):.1f}s)")

    quote = provider.get_realtime_quote(code)
    if quote:
        print(f"  realtime   : price={quote.price} volume_ratio={quote.volume_ratio} "
              f"turnover_rate={quote.turnover_rate}")
    else:
        print(f"  realtime   : None")

    dazong = provider.get_dazong(code)
    if dazong is not None:
        print(f"  dazong     : {len(dazong.items)} 条")
        for it in dazong.items[:3]:
            print(f"      {it.trade_date} 成交价={it.deal_price} 成交额={it.deal_amount:.0f} "
                  f"折溢率={it.premium_ratio} 买方={it.buyer_seat[:12]}...")
    else:
        print(f"  dazong     : None")

# 检查日志噪音
log_text = log_stream.getvalue()
has_tqdm = "|" in log_text and "%" in log_text and "it/s" in log_text
has_traceback = "Traceback (most recent call last)" in log_text
print("\n===== 日志噪音检查 =====")
print(f"  tqdm 进度条噪音: {has_tqdm}")
print(f"  traceback 噪音 : {has_traceback}")
print(f"  日志样本（含中文失败消息）:")
for line in log_text.splitlines():
    if any(k in line for k in ("失败", "无数据", "降级", "沪深港通", "数据就绪")):
        print(f"    {line[:140]}")

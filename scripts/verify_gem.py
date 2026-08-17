"""创业板（300xxx）数据源全链路实测验证脚本。

验证：
1. check_board 放行 300xxx、拒绝 688xxx
2. 数据源（kline/realtime/资金流/财务/估值/新闻/st_risk）对创业板可用
3. 涨跌停价 ±20%（round(昨收×1.20,2)/round(昨收×0.80,2)）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finagent.compute import BoardCheckInput, check_board
from finagent.compute.rules import compute_limit_price
from finagent.compute.schemas import LimitPriceInput

CODES = ["300750", "300059"]  # 宁德时代 / 东方财富


def main() -> None:
    # 1. 板块校验
    print("=" * 70)
    print("1. check_board 板块校验")
    print("=" * 70)
    for code in ["300750", "300059", "688981", "600519"]:
        r = check_board(BoardCheckInput(code=code))
        print(f"  {code}: is_supported={r.is_supported} board={r.board_name} "
              f"reason={r.reason!r}")

    # 2. 数据源全链路
    from finagent.data.cache import AkshareCache
    from finagent.data.fallback import FallbackDataProvider
    from finagent.data.sources.akshare_adapter import AkshareAdapter
    from finagent.data.sources.baostock_adapter import BaostockAdapter
    from finagent.data.sources.eastmoney_adapter import EastmoneyAdapter
    from finagent.data.sources.sina_adapter import SinaAdapter
    from finagent.data.sources.tencent_adapter import TencentAdapter
    from finagent.config.settings import DATA_DIR

    cache = AkshareCache(db_path=str(DATA_DIR / "akshare_cache.db"))
    provider = FallbackDataProvider(adapters={
        "akshare": AkshareAdapter(cache=cache),
        "eastmoney": EastmoneyAdapter(cache=cache),
        "baostock": BaostockAdapter(cache=cache),
        "sina": SinaAdapter(cache=cache),
        "tencent": TencentAdapter(cache=cache),
    }, cache=cache)

    fetchers = [
        ("kline", "get_kline", lambda c: provider.get_kline(c)),
        ("realtime_quote", "get_realtime_quote", lambda c: provider.get_realtime_quote(c)),
        ("capital_flow", "get_capital_flow", lambda c: provider.get_capital_flow(c)),
        ("financials", "get_financials", lambda c: provider.get_financials(c)),
        ("valuation", "get_valuation", lambda c: provider.get_valuation(c)),
        ("news", "get_news", lambda c: provider.get_news(c)),
        ("st_risk", "get_st_risk", lambda c: provider.get_st_risk(c)),
    ]

    for code in CODES:
        print()
        print("=" * 70)
        print(f"2. 数据源全链路 — {code}")
        print("=" * 70)
        for key, _m, fetch in fetchers:
            try:
                result = fetch(code)
                if result is None:
                    print(f"  {key:16s} ✗ None（全部源失败）")
                else:
                    src = getattr(result, "source", None) or getattr(result, "get", lambda *a: None)("source") or "?"
                    print(f"  {key:16s} ✓ source={src}")
            except Exception as e:  # noqa: BLE001
                print(f"  {key:16s} ✗ 异常: {type(e).__name__}: {e}")

        # 3. 涨跌停价验证
        print()
        print(f"  3. 涨跌停价验证（{code}）")
        try:
            quote = provider.get_realtime_quote(code)
            if quote is None:
                print("     ✗ realtime_quote 不可用，无法验证涨跌停价")
                continue
            prev_close = quote.prev_close
            name = getattr(quote, "name", "")
            is_st = "ST" in str(name)
            print(f"     名称={name}  现价={quote.price}  昨收={prev_close}")
            print(f"     数据源返回: 涨停={quote.limit_up}  跌停={quote.limit_down}")

            # 用规则引擎 C2 推算（创业板 board_name）
            calc = compute_limit_price(LimitPriceInput(
                prev_close=prev_close, is_st=is_st, board_name="创业板",
            ))
            print(f"     规则引擎C2: 涨停={calc.limit_up}  跌停={calc.limit_down} "
                  f"rate={calc.rate}")

            # 断言：源数据涨停价 == round(昨收×1.20, 2)（容差 0.02 应对四舍五入差异）
            expect_up = round(prev_close * 1.20, 2)
            expect_down = round(prev_close * 0.80, 2)
            ok_up = abs(quote.limit_up - expect_up) <= 0.02
            ok_down = abs(quote.limit_down - expect_down) <= 0.02
            print(f"     预期: 涨停={expect_up}  跌停={expect_down}")
            print(f"     校验: 涨停 ±20% {'✓' if ok_up else '✗'}  跌停 ±20% {'✓' if ok_down else '✗'}")
        except Exception as e:  # noqa: BLE001
            print(f"     ✗ 涨跌停验证异常: {type(e).__name__}: {e}")

    print()
    print("=" * 70)
    print("完成")
    print("=" * 70)


if __name__ == "__main__":
    main()

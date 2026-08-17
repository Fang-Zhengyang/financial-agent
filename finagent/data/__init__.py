"""finagent.data — Data layer: providers, cache, schemas, and source adapters.

Exports
-------
- DataProvider : ABC that every adapter implements.
- AkshareCache  : SQLite cache with TTL (Ticket A1).
- EastmoneyAdapter : Eastmoney push2 adapter (Ticket A2.2).
- All Pydantic return schemas.
"""

# 东财 DNS 重定向必须在任何 adapter（间接 import akshare/requests）之前生效，
# 保证 CLI/Web/run.sh/bat 任一启动方式在进入数据层时自动 patch socket.getaddrinfo
# （默认启用，FINAGENT_EM_REDIRECT=0 可关闭）。
import os as _os  # noqa: E402

# 禁用 akshare 内部 tqdm 进度条（否则 Web 服务黑窗口被「45%|████ 26/58」刷屏）。
# 必须在 import tqdm（akshare 的 get_tqdm 首次调用）之前设置，才能被 tqdm 的
# envwrap 装饰器捕获。run.sh 也会 export TQDM_DISABLE=1，此处 setdefault 兜底，
# 保证直接 python -m finagent.cli 启动时同样禁用。
_os.environ.setdefault("TQDM_DISABLE", "1")

from finagent.data import _em_redirect as _em_redirect_module  # noqa: E402

_em_redirect_module.install()

from finagent.data.cache import AkshareCache
from finagent.data.fallback import (
    FALLBACK_CHAIN,
    DataBundle,
    DataUnavailableError,
    FallbackDataProvider,
    gather_bundle,
)
from finagent.data.provider import DataProvider
from finagent.data.schemas import (
    AnnouncementData,
    AnnouncementItem,
    CapitalFlow,
    DazongData,
    DazongItem,
    FinancialIndicators,
    HolderData,
    JiejinData,
    JiejinItem,
    KlineData,
    KlineRow,
    LHBData,
    LHBItem,
    NewsData,
    NewsItem,
    NorthData,
    NorthRow,
    PEPercentileData,
    RealTimeQuote,
    STRiskData,
    TradeCalendar,
    ValuationData,
)
from finagent.data.sources.eastmoney_adapter import EastmoneyAdapter
from finagent.data.sources.sina_adapter import SinaAdapter
from finagent.data.sources.tencent_adapter import TencentAdapter

__all__ = [
    # cache
    "AkshareCache",
    # provider
    "DataProvider",
    # fallback
    "DataBundle",
    "DataUnavailableError",
    "FALLBACK_CHAIN",
    "FallbackDataProvider",
    "gather_bundle",
    # adapters
    "EastmoneyAdapter",
    "SinaAdapter",
    "TencentAdapter",
    # schemas
    "KlineData",
    "KlineRow",
    "RealTimeQuote",
    "CapitalFlow",
    "FinancialIndicators",
    "ValuationData",
    "NewsData",
    "NewsItem",
    "AnnouncementData",
    "AnnouncementItem",
    "STRiskData",
    "TradeCalendar",
    "LHBData",
    "LHBItem",
    "JiejinData",
    "JiejinItem",
    "HolderData",
    "NorthData",
    "NorthRow",
    "PEPercentileData",
    "DazongData",
    "DazongItem",
]

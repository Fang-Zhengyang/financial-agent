import os
import finagent.data
import finagent.web.app
import finagent.orchestration.steps

print("IMPORT OK")
print("TQDM_DISABLE =", os.environ.get("TQDM_DISABLE"))

# Verify new schema fields
from finagent.data.schemas import RealTimeQuote, DazongData, DazongItem
q = RealTimeQuote(code="600519", name="x", price=1.0, prev_close=1.0, pct_chg=0.0,
                  limit_up=1.1, limit_down=0.9, volume_ratio=1.2, source="akshare")
print("turnover_rate default =", q.turnover_rate)

# Verify dazong registered in fallback chain
from finagent.data.fallback import FALLBACK_CHAIN, _METHOD_MAP, _EXTENDED_TYPES
print("dazong in chain:", FALLBACK_CHAIN.get("dazong"))
print("dazong method:", _METHOD_MAP.get("dazong"))
print("dazong extended:", "dazong" in _EXTENDED_TYPES)

# Verify get_dazong exists on adapter
from finagent.data.sources.akshare_adapter import AkshareAdapter
print("has get_dazong:", hasattr(AkshareAdapter, "get_dazong"))
print("has get_north:", hasattr(AkshareAdapter, "get_north"))

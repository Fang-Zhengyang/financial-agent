"""pytest 全局配置。

- 关闭 Web 启动预热（FINAGENT_PREHEAT=0）：测试不应触发真实网络请求或
  读写真实 data/akshare_cache.db；预热在测试中应静默禁用。
"""

import os

os.environ["FINAGENT_PREHEAT"] = "0"

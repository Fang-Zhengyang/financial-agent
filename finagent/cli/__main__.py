"""``python -m finagent.cli`` 入口。

使 ``python -m finagent.cli analyze ...`` 可直接运行。
"""

import sys

from finagent.cli.main import main

if __name__ == "__main__":
    sys.exit(main())

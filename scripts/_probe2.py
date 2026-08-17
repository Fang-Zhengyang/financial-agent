import inspect

import akshare as ak

src = inspect.getsource(ak.stock_individual_fund_flow)
print(src)

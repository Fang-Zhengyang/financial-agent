"""Raw eastmoney API check for 300403 northbound holdings."""
import json
import requests

url = "https://datacenter-web.eastmoney.com/api/data/v1/get"

def raw(code):
    params = {
        "sortColumns": "TRADE_DATE",
        "sortTypes": "-1",
        "pageSize": "5",
        "pageNumber": "1",
        "reportName": "RPT_MUTUAL_HOLDSTOCKNDATE_STA",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": f'(SECURITY_CODE="{code}")(INTERVAL_TYPE="1")',
    }
    r = requests.get(url, params=params, timeout=30)
    j = r.json()
    result = j.get("result")
    print(f"{code}: result is None={result is None}, success={j.get('success')}, message={j.get('message')}")
    if result:
        print("  pages=", result.get("pages"), "data_len=", len(result.get("data") or []))
    print("  raw(result)=", json.dumps(result, ensure_ascii=False)[:300] if result else None)

raw("600519")
raw("300403")
raw("300750")  # 宁德时代 (创业板龙头, known 深股通标的)
raw("300059")  # 东方财富 (创业板, 深股通标的)

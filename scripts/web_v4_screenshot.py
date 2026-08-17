"""Web v4 整页截图 — 用 Playwright 无头 Chromium 渲染 http://127.0.0.1:8082/。"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("/mnt/c/Users/70424/Desktop/financial-agent/test-reports/web_v4_screenshot.png")
OUT.parent.mkdir(parents=True, exist_ok=True)

URL = "http://127.0.0.1:8082/"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(URL, wait_until="networkidle", timeout=60000)

    # 等待 /analysis-data 渲染出指标卡片，并给 ECharts 一点渲染时间
    try:
        page.wait_for_selector(".metric-card", timeout=15000)
    except Exception as e:
        print("WARN: metric-card 未出现:", e)
    page.wait_for_timeout(3000)

    # 确认关键区块存在
    for sel in ["#fundamentals-grid", "#valuation-grid", "#rsi-chart canvas", "#macd-chart canvas", "#fundflow-chart canvas"]:
        try:
            page.wait_for_selector(sel, timeout=8000)
            print(f"OK  {sel}")
        except Exception as e:
            print(f"MISS {sel}: {e}")

    page.screenshot(path=str(OUT), full_page=True)
    print("screenshot saved:", OUT)

    browser.close()

print("done")

"""Web v4 渲染验证 — 检查指标卡片文本 + 图表 canvas 是否真的画了像素 + 控制台错误。"""
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8082/"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL, wait_until="networkidle", timeout=60000)
    try:
        page.wait_for_selector(".metric-card", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(2500)

    # 1. 指标卡片文本
    cards = page.eval_on_selector_all(
        ".metric-card",
        "els => els.map(e => e.querySelector('.metric-label').textContent + '=' + e.querySelector('.metric-value').textContent)"
    )
    print("指标卡片:")
    for c in cards:
        print("  ", c)

    # 2. 资金流摘要
    summary = page.eval_on_selector("#fundflow-summary", "e => e.textContent")
    print("资金流摘要:", summary)

    # 3. canvas 是否画了非透明像素
    def canvas_has_ink(sel):
        try:
            return page.eval_on_selector(
                sel,
                """el => {
                    const c = el.querySelector('canvas');
                    if (!c) return 'no-canvas';
                    const ctx = c.getContext('2d');
                    const d = ctx.getImageData(0, 0, c.width, c.height).data;
                    let ink = 0;
                    for (let i = 3; i < d.length; i += 4) if (d[i] > 0) ink++;
                    return ink;
                }""",
            )
        except Exception as e:
            return f"err:{e}"

    for sel in ["#rsi-chart", "#macd-chart", "#fundflow-chart", "#kline-chart"]:
        print(f"{sel} canvas 非透明像素数:", canvas_has_ink(sel))

    # 4. 加粗着色文字检查（报告正文中是否有 signal-word / num-pos / num-neg / num-hl / risk-item）
    counts = page.eval_on_selector(
        "body",
        """() => {
            const q = s => document.querySelectorAll(s).length;
            return {
                signal_word: q('.signal-word-buy') + q('.signal-word-sell') + q('.signal-word-hold'),
                num_hl: q('.num-hl'), num_pos: q('.num-pos'), num_neg: q('.num-neg'),
                risk_item: q('li.risk-item'),
            };
        }""",
    )
    print("样式增强计数:", counts)

    print("控制台错误:", errors if errors else "无")
    browser.close()

"""Web v3 整页截图脚本 — 用 Playwright 无头 Chromium 截取首页。

验收要求：
  - 均线完整段（MA120 连续线段，基于 250 点）
  - 历史选择器（GET /history 下拉）
  - 总分总分块报告（摘要/模块/结论折叠块）

输出：test-reports/web_v3_screenshot.png
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # financial-agent/（项目根）
OUT = PROJECT_ROOT / "test-reports" / "web_v3_screenshot.png"
URL = "http://127.0.0.1:8081/"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(URL, wait_until="networkidle", timeout=60000)

        # 等待 ECharts 渲染出 canvas（K 线图）
        try:
            page.wait_for_selector("#kline-chart canvas", timeout=30000)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] K线 canvas 未出现: {e}")

        # 再等一会儿让均线绘制完成
        page.wait_for_timeout(2500)

        # 展开两个模块块，让「分块报告」在截图里更直观
        page.evaluate(
            """
            () => {
                document.querySelectorAll('.report-block-toggle').forEach(function (btn) {
                    var block = btn.closest('.report-block');
                    var title = (block && block.querySelector('.report-block-title')) ? block.querySelector('.report-block-title').textContent : '';
                    if (title.indexOf('技术面') >= 0 || title.indexOf('决策结论') >= 0) {
                        btn.click();
                    }
                });
            }
            """
        )
        page.wait_for_timeout(800)

        # 校验页面关键元素存在
        has_history = page.locator("#history-select").count() > 0
        has_blocks = page.locator(".report-block").count() > 0
        kline_code = page.locator("#kline-code").text_content() if page.locator("#kline-code").count() else ""
        print(f"history-select: {has_history}")
        print(f"report-blocks: {has_blocks}")
        print(f"kline-code: {kline_code}")
        print(f"console errors: {errors[:5] if errors else '无'}")

        page.screenshot(path=str(OUT), full_page=True)
        print(f"截图已保存: {OUT}")

        browser.close()

    if not OUT.exists() or OUT.stat().st_size == 0:
        print("截图文件为空或不存在", file=sys.stderr)
        return 1
    print(f"文件大小: {OUT.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

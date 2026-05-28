from playwright.sync_api import sync_playwright

SCREENSHOT_PATH = "/Users/zack/.sculptor/workspaces/de62cc2c580c452ab563c8e0a3825d3c/attachments/hr_chart_screenshot.png"


def test_heart_rate_chart_renders(stack):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})

        page.goto(stack.url)
        page.wait_for_selector("#hr-main-wrap canvas", timeout=20000)
        page.wait_for_timeout(2000)

        debug = page.evaluate("""() => {
            const wrap = document.getElementById('hr-nav-wrap');
            const overlay = wrap ? wrap.querySelector('.nav-overlay') : null;
            const sel = overlay ? overlay.querySelector('.nav-sel') : null;
            const hL = overlay ? overlay.querySelector('.nav-handle-l') : null;
            const hR = overlay ? overlay.querySelector('.nav-handle-r') : null;
            const cL = overlay ? overlay.querySelector('.nav-curtain-l') : null;
            return {
                wrapExists: !!wrap,
                overlayExists: !!overlay,
                overlayStyle: overlay ? overlay.style.cssText : null,
                selStyle: sel ? sel.style.cssText : null,
                selComputed: sel ? {l: getComputedStyle(sel).left, w: getComputedStyle(sel).width, h: getComputedStyle(sel).height} : null,
                handleLExists: !!hL, handleRExists: !!hR,
                curtainLWidth: cL ? getComputedStyle(cL).width : null,
            };
        }""")
        print(f"\\nOverlay debug: {debug}")

        page.screenshot(path=SCREENSHOT_PATH, full_page=False)
        browser.close()

    print(f"Screenshot saved to: {SCREENSHOT_PATH}")

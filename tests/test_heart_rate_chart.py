from playwright.sync_api import sync_playwright

SCREENSHOT_PATH = "/Users/zack/.sculptor/workspaces/de62cc2c580c452ab563c8e0a3825d3c/attachments/hr_chart_screenshot.png"


def test_heart_rate_chart_renders(stack):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})

        page.goto(stack.url)
        page.wait_for_selector("#hr-main-wrap canvas", timeout=20000)
        page.wait_for_timeout(2000)

        # Debug: check overlay state
        debug = page.evaluate("""() => {
            const over = document.querySelector('#hr-nav-wrap .u-over');
            if (!over) return {error: 'no .u-over'};
            const cs = getComputedStyle(over);
            const children = over.children.length;
            const sel = over.querySelector('.nav-sel');
            const cL = over.querySelector('.nav-curtain-l');
            const hL = over.querySelector('.nav-handle-l');
            return {
                overDims: {w: over.clientWidth, h: over.clientHeight},
                overPos: cs.position, overOverflow: cs.overflow,
                childCount: children,
                selExists: !!sel,
                selDims: sel ? {l: sel.style.left, w: sel.style.width, h: getComputedStyle(sel).height} : null,
                curtainExists: !!cL,
                curtainDims: cL ? {w: cL.style.width, h: getComputedStyle(cL).height} : null,
                handleExists: !!hL,
                handleDims: hL ? {w: getComputedStyle(hL).width, h: getComputedStyle(hL).height, pos: getComputedStyle(hL).position} : null,
            };
        }""")
        print(f"\nOverlay debug: {debug}")

        page.screenshot(path=SCREENSHOT_PATH, full_page=False)
        browser.close()

    print(f"Screenshot saved to: {SCREENSHOT_PATH}")

from playwright.sync_api import sync_playwright


def test_workouts_list_is_summary_and_detail_loads(stack):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.goto(f"{stack.url}/workouts")

        # Calendar renders, and the most recent workout is auto-selected, which
        # lazily fetches detail and draws the HR chart from the fetched trace. (The
        # map is left to a separate check below — it depends on an external tile CDN.)
        page.wait_for_selector(".cal-chip", timeout=20000)
        page.wait_for_selector("#hr-chart canvas", timeout=20000)

        # The list payload is a summary: no per-sample trace, no route.
        summary = page.evaluate(
            "async () => (await (await fetch('/api/workouts?limit=50')).json()).data[0]"
        )
        assert "heart_rate" not in summary
        assert "route_gpx" not in summary

        # The detail payload carries the full trace and route.
        detail = page.evaluate(
            "async () => await (await fetch('/api/workouts/wk-mock-1')).json()"
        )
        assert len(detail["heart_rate"]["samples"]) == 2
        assert "<trkpt" in detail["route_gpx"]

        browser.close()

"""Optional JavaScript rendering. Raw HTML is always fetched; rendering is extra."""

from __future__ import annotations


class JsRenderer:
    name = "unavailable"
    available = False

    def render(self, url: str) -> str | None:
        return None


class PlaywrightJsRenderer(JsRenderer):
    name = "playwright"
    available = True

    def render(self, url: str) -> str | None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                return page.content()
            finally:
                browser.close()


def build_renderer(requested: bool) -> JsRenderer:
    if not requested:
        return JsRenderer()
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return JsRenderer()
    return PlaywrightJsRenderer()

"""Google Chrome website fetcher via Selenium (shared driver, thread-safe)."""
from __future__ import annotations

import atexit
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from selenium.webdriver.chrome.webdriver import WebDriver

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PAGE_LOAD_TIMEOUT = 30.0
IMPLICIT_WAIT = 3.0
SCROLL_PAUSE = 0.6
HEADLESS = True
CHROME_BINARY: str = ""

_driver: WebDriver | None = None
_driver_lock = threading.RLock()

_COOKIE_SELECTORS = (
    "button[id*='accept']",
    "button[id*='Accept']",
    "button[class*='accept']",
    "button[class*='Accept']",
    "button[aria-label*='Accept']",
    "button[aria-label*='agree']",
    "#onetrust-accept-btn-handler",
    ".cookie-accept",
    "a[class*='accept']",
)


@dataclass(frozen=True)
class SeleniumPageResult:
    url: str
    final_url: str
    alive: bool
    html: str
    title: str = ""
    visible_text: str = ""
    error: str = ""


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    return url


def build_chrome_options(
    *,
    headless: bool | None = None,
    user_data_dir: str | None = None,
) -> object:
    from selenium.webdriver.chrome.options import Options

    options = Options()
    use_headless = HEADLESS if headless is None else headless
    if use_headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f"--user-agent={USER_AGENT}")
    options.page_load_strategy = "normal"
    if CHROME_BINARY:
        options.binary_location = CHROME_BINARY
    if user_data_dir:
        options.add_argument(f"--user-data-dir={user_data_dir}")
    return options


def _chrome_options():
    return build_chrome_options()


def _create_driver() -> WebDriver:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
    except ImportError as e:
        raise RuntimeError(
            "selenium is required for website scraping: pip install selenium"
        ) from e

    options = _chrome_options()
    try:
        driver = webdriver.Chrome(options=options)
    except Exception:
        driver = webdriver.Chrome(service=Service(), options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    driver.implicitly_wait(IMPLICIT_WAIT)
    try:
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
    except Exception:
        pass
    return driver


def _get_driver() -> WebDriver:
    global _driver
    with _driver_lock:
        if _driver is None:
            _driver = _create_driver()
        return _driver


def shutdown_chrome_driver() -> None:
    global _driver
    with _driver_lock:
        if _driver is not None:
            try:
                _driver.quit()
            except Exception:
                pass
            _driver = None


def _dismiss_cookie_banners(driver: WebDriver) -> None:
    from selenium.common.exceptions import ElementNotInteractableException, NoSuchElementException
    from selenium.webdriver.common.by import By

    for selector in _COOKIE_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                if el.is_displayed() and el.is_enabled():
                    el.click()
                    time.sleep(0.3)
                    return
        except (NoSuchElementException, ElementNotInteractableException):
            continue


def _scroll_for_lazy_content(driver: WebDriver) -> None:
    pause = max(0.2, SCROLL_PAUSE)
    try:
        height = driver.execute_script(
            "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);"
        )
        height = int(height or 1200)
        for frac in (0.35, 0.7, 1.0):
            driver.execute_script(f"window.scrollTo(0, {int(height * frac)});")
            time.sleep(pause)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.2)
    except Exception:
        pass


def _visible_body_text(driver: WebDriver) -> str:
    from selenium.webdriver.common.by import By

    try:
        main = driver.find_elements(By.CSS_SELECTOR, "main, [role='main'], article")
        chunks: list[str] = []
        for el in main[:3]:
            t = (el.text or "").strip()
            if len(t) > 80:
                chunks.append(t)
        if chunks:
            return "\n\n".join(chunks)[:12000]
        return (driver.find_element(By.TAG_NAME, "body").text or "").strip()[:12000]
    except Exception:
        return ""


def fetch_page_html(url: str) -> SeleniumPageResult:
    """
    Load URL in Chrome, scroll, dismiss cookies, return rendered HTML + visible text.
    """
    from selenium.common.exceptions import TimeoutException, WebDriverException

    url = _normalize_url(url)
    with _driver_lock:
        driver = _get_driver()
        try:
            driver.get(url)
            _dismiss_cookie_banners(driver)
            _scroll_for_lazy_content(driver)
            html = driver.page_source or ""
            visible = _visible_body_text(driver)
            final = driver.current_url or url
            title = (driver.title or "").strip()
            alive = _looks_like_success(final, html, visible)
            return SeleniumPageResult(
                url=url,
                final_url=final,
                alive=alive,
                html=html,
                title=title,
                visible_text=visible,
            )
        except TimeoutException:
            _dismiss_cookie_banners(driver)
            _scroll_for_lazy_content(driver)
            html = driver.page_source or ""
            visible = _visible_body_text(driver)
            final = driver.current_url or url
            alive = bool((html and len(html) > 400) or len(visible) > 80)
            return SeleniumPageResult(
                url=url,
                final_url=final,
                alive=alive,
                html=html,
                title=(driver.title or "").strip(),
                visible_text=visible,
                error="timeout_partial",
            )
        except WebDriverException as exc:
            return SeleniumPageResult(
                url=url,
                final_url=url,
                alive=False,
                html="",
                error=str(exc)[:300],
            )


def check_url_alive(url: str) -> tuple[bool, str]:
    result = fetch_page_html(url)
    return result.alive, result.final_url


def _looks_like_success(final_url: str, html: str, visible: str = "") -> bool:
    low = final_url.lower()
    if any(x in low for x in ("chrome-error://", "about:blank")):
        return False
    if len(visible) >= 80:
        return True
    if len(html) < 200:
        return False
    host = urlparse(final_url).netloc.lower()
    if host and "error" in host:
        return False
    return True


def apply_selenium_env() -> None:
    import os

    global HEADLESS, PAGE_LOAD_TIMEOUT, IMPLICIT_WAIT, SCROLL_PAUSE, CHROME_BINARY
    raw = os.getenv("SELENIUM_HEADLESS", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        HEADLESS = False
    elif raw in ("1", "true", "yes", "on"):
        HEADLESS = True
    if v := os.getenv("SELENIUM_PAGE_LOAD_TIMEOUT", "").strip():
        try:
            PAGE_LOAD_TIMEOUT = float(v)
        except ValueError:
            pass
    if v := os.getenv("SELENIUM_IMPLICIT_WAIT", "").strip():
        try:
            IMPLICIT_WAIT = float(v)
        except ValueError:
            pass
    if v := os.getenv("SELENIUM_SCROLL_PAUSE", "").strip():
        try:
            SCROLL_PAUSE = float(v)
        except ValueError:
            pass
    if v := os.getenv("CHROME_BINARY_PATH", "").strip():
        CHROME_BINARY = v


atexit.register(shutdown_chrome_driver)

import time
import re
import random
from urllib.parse import quote_plus, unquote, urlparse, parse_qs
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout
from rich.console import Console
import config

console = Console()

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {}, app: {} };
"""

try:
    import browser_cookie3
    _BC3_AVAILABLE = True
except ImportError:
    _BC3_AVAILABLE = False


def is_chrome_debug_running() -> bool:
    try:
        with urlopen("http://localhost:9222/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _get_chrome_cookies() -> list[dict]:
    if config.BROWSER_HEADLESS:
        return []
    if not _BC3_AVAILABLE:
        console.print("[yellow]browser_cookie3 not installed — running without cookies.[/yellow]")
        return []
    try:
        jar = browser_cookie3.chrome(domain_name="google.com")
        cookies = []
        for c in jar:
            entry = {
                "name":   c.name,
                "value":  c.value,
                "domain": c.domain if c.domain.startswith(".") else "." + c.domain,
                "path":   c.path or "/",
                "secure": bool(c.secure),
            }
            if c.expires:
                entry["expires"] = float(c.expires)
            cookies.append(entry)
        console.print(f"[green]Loaded {len(cookies)} Chrome cookies for Google[/green]")
        return cookies
    except Exception as e:
        console.print(f"[yellow]Cookie extraction skipped: {e}[/yellow]")
        return []


def _get_browser(pw):
    browser = pw.chromium.launch(
        headless=config.BROWSER_HEADLESS,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--start-maximized",
        ],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        timezone_id="Asia/Kolkata",
    )
    context.add_init_script(_STEALTH_JS)

    cookies = _get_chrome_cookies()
    if cookies:
        try:
            context.add_cookies(cookies)
        except Exception as e:
            console.print(f"[yellow]Cookie injection warning: {e}[/yellow]")

    return browser, context


def build_search_query(user_input: str) -> str:
    cleaned = re.sub(r"site:\S+", "", user_input, flags=re.IGNORECASE).strip()
    if "linkedin" not in cleaned.lower():
        cleaned += " linkedin"
    return cleaned.strip()


def _search_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}&num=30"


def _extract_real_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("/url"):
        try:
            qs = parse_qs(urlparse(href).query)
            return unquote(qs.get("q", [""])[0]).split("?")[0]
        except Exception:
            return ""
    if href.startswith("http"):
        return href.split("?")[0]
    return ""


def _handle_captcha(page: Page) -> bool:
    for sig in ["text=unusual traffic", "text=Verify you are human", "#captcha-form"]:
        try:
            if page.locator(sig).first.is_visible(timeout=1500):
                console.print("[bold red]CAPTCHA — please solve it in the browser window, waiting 2 min...[/bold red]")
                page.wait_for_selector("div#search", timeout=120000)
                return True
        except Exception:
            continue
    return False


def _extract_links(page: Page) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for link in page.query_selector_all("a[href*='linkedin.com/in/']"):
        try:
            href = link.get_attribute("href") or ""
            url  = _extract_real_url(href)
            if url and re.search(r"linkedin\.com/in/[^/?\s]+", url) and url not in seen:
                seen.add(url)
                urls.append(url)
        except Exception:
            continue
    return urls


def _next_page_url(page: Page):
    try:
        nxt = page.locator("a#pnnext, a[aria-label='Next page']").first
        if nxt.is_visible(timeout=2000):
            href = nxt.get_attribute("href") or ""
            return f"https://www.google.com{href}" if href.startswith("/") else href
    except Exception:
        pass
    return None


def run_search(query: str) -> list[str]:
    all_urls: list[str] = []
    seen:     set[str]  = set()
    search_query = build_search_query(query)
    console.print(f"\n[bold cyan]Query:[/bold cyan] {search_query}\n")

    with sync_playwright() as pw:
        browser, context = _get_browser(pw)
        page = context.new_page()
        try:
            page.goto(_search_url(search_query), wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(2, 3))
            _handle_captcha(page)

            for page_num in range(1, config.MAX_PAGES + 1):
                console.print(f"[yellow]Page {page_num}...[/yellow]")
                found = _extract_links(page)
                new   = [u for u in found if u not in seen]
                seen.update(new)
                all_urls.extend(new)
                console.print(f"  +{len(new)} links (total: {len(all_urls)})")

                if page_num >= config.MAX_PAGES:
                    break
                next_url = _next_page_url(page)
                if not next_url:
                    break
                time.sleep(random.uniform(2, 4))
                page.goto(next_url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(random.uniform(2, 3))
                _handle_captcha(page)

        except PlaywrightTimeout:
            console.print("[red]Timeout.[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        finally:
            page.close()
            if browser:
                browser.close()

    return all_urls

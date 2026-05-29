import re
import threading
from urllib.request import urlopen
from ddgs import DDGS
from rich.console import Console
import config

console = Console()

_SEARCH_SEMAPHORE = threading.Semaphore(3)


def is_chrome_debug_running() -> bool:
    try:
        with urlopen("http://localhost:9222/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def build_search_query(user_input: str) -> str:
    cleaned = re.sub(r"site:\S+", "", user_input, flags=re.IGNORECASE).strip()
    if "linkedin" not in cleaned.lower():
        cleaned += " linkedin"
    return cleaned.strip()


def run_search(query: str, log_fn=None) -> list[str]:
    def log(msg: str):
        console.print(msg)
        if log_fn:
            clean = re.sub(r'\[/?[^\]]*\]', '', msg).strip()
            if clean:
                log_fn(clean)

    all_urls: list[str] = []
    seen: set[str] = set()
    search_query = build_search_query(query)
    log(f"Query: {search_query}")

    if not _SEARCH_SEMAPHORE.acquire(blocking=True, timeout=30):
        log("Server is busy — too many concurrent searches. Please try again shortly.")
        return all_urls

    try:
        log("Searching DuckDuckGo...")
        with DDGS() as ddgs:
            for r in ddgs.text(search_query, max_results=config.MAX_PAGES * 10):
                url = (r.get('href') or '').split('?')[0]
                if re.search(r'linkedin\.com/in/[^/?\s]+', url) and url not in seen:
                    seen.add(url)
                    all_urls.append(url)
        log(f"Done — {len(all_urls)} profiles collected.")
    except Exception as e:
        log(f"Search error: {e}")
    finally:
        _SEARCH_SEMAPHORE.release()

    return all_urls

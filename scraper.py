import os
import re
import threading
import requests as _http
from urllib.parse import urlparse
from urllib.request import urlopen
from ddgs import DDGS
from rich.console import Console
import config

_SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080")

console = Console()

_SEARCH_SEMAPHORE = threading.Semaphore(3)

# Common Indian city aliases — DDG may index either spelling
_CITY_ALIASES = {
    "bangalore":  ["bangalore", "bengaluru"],
    "bengaluru":  ["bengaluru", "bangalore"],
    "mumbai":     ["mumbai", "bombay"],
    "bombay":     ["bombay", "mumbai"],
    "chennai":    ["chennai", "madras"],
    "madras":     ["madras", "chennai"],
    "kolkata":    ["kolkata", "calcutta"],
    "calcutta":   ["calcutta", "kolkata"],
    "gurugram":   ["gurugram", "gurgaon"],
    "gurgaon":    ["gurgaon", "gurugram"],
}


def _fetch_results(query: str, max_results: int) -> list[dict]:
    """Query SearXNG (Bing + Brave + DDG); fall back to DDG directly if unavailable."""
    try:
        resp = _http.get(
            f"{_SEARXNG_URL}/search",
            params={
                "q":        query,
                "format":   "json",
                "engines":  "bing,brave,duckduckgo",
                "language": "en-IN",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            raw = resp.json().get("results", [])
            if raw:
                return [
                    {
                        "url":   r.get("url",     ""),
                        "title": r.get("title",   ""),
                        "body":  r.get("content", ""),
                    }
                    for r in raw[:max_results]
                ]
    except Exception:
        pass

    # Fallback: DDG directly
    try:
        with DDGS() as ddgs:
            raw = ddgs.text(query, max_results=max_results, region="in-en") or []
        return [
            {
                "url":   r.get("href",  ""),
                "title": r.get("title", ""),
                "body":  r.get("body",  ""),
            }
            for r in raw
        ]
    except Exception:
        return []


def is_chrome_debug_running() -> bool:
    try:
        with urlopen("http://localhost:9222/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _location_matches(location: str, snippet: str) -> bool:
    if not location:
        return True
    loc = location.lower().strip()
    s   = snippet.lower()
    aliases = _CITY_ALIASES.get(loc, [loc])
    return any(a in s for a in aliases)


def _company_matches(company: str, snippet: str) -> bool:
    if not company:
        return True
    comp = company.lower().strip()
    s    = snippet.lower()
    # exact phrase match
    if comp in s:
        return True
    # partial: all significant words must appear (handles "Tata AutoComp Systems" → "Tata"+"AutoComp")
    words = [w for w in comp.split() if len(w) > 2]  # skip short words like "of", "at"
    if words and all(w in s for w in words):
        return True
    # first two words match (common abbreviation pattern)
    if len(words) >= 2 and words[0] in s and words[1] in s:
        return True
    return False


def _build_query_variants(base_query: str) -> list[str]:
    """Return strict → medium → loose variants of the query."""
    # base_query already has site:linkedin.com/in/ prefix from frontend
    if 'site:linkedin.com/in/' in base_query.lower():
        medium = base_query.replace('site:linkedin.com/in/', 'site:linkedin.com').strip()
        loose  = re.sub(r'site:\S+', '', base_query, flags=re.IGNORECASE).strip() + ' linkedin'
        return [base_query, medium, loose]
    # fallback for plain queries
    return [base_query]


def build_search_query(user_input: str) -> str:
    if re.search(r'site:', user_input, re.IGNORECASE):
        return user_input.strip()
    cleaned = user_input.strip()
    if "linkedin" not in cleaned.lower():
        cleaned += " linkedin"
    return cleaned


# Domains to exclude from ICP results
_ICP_BLOCKED = {
    # Social / professional networks
    'linkedin.com','facebook.com','twitter.com','instagram.com','youtube.com',
    'quora.com','reddit.com','medium.com','pinterest.com','t.me','telegram.org',
    # Encyclopedias / wikis
    'wikipedia.org','wikihow.com','wikimedia.org',
    # Indian B2B directories & listing sites
    'indiamart.com','tradeindia.com','exportersindia.com','dir.indiamart.com',
    'justdial.com','sulekha.com','yellowpages.in','yellowpages.com',
    'bizongo.com','udaan.com','moglix.com','industrybuying.com',
    'power2sme.com','tradeford.com','b2bmart.in','gobid.in',
    'companiesinindia.com','hotfrog.in','grotal.com','asklaila.com',
    'cylex-india.com','brownbook.net','infoisinfo.co.in','allbiz.in',
    # Global B2B directories
    'alibaba.com','aliexpress.com','tradekey.com','globalsources.com',
    'made-in-china.com','dhgate.com','bizvibe.com','tradewheel.com',
    'kompass.com','dnb.com','thomasnet.com','europages.com',
    # Job boards
    'glassdoor.com','indeed.com','naukri.com','shine.com','monster.com',
    'foundit.in','timesjobs.com','freshersworld.com',
    # News / media
    'bloomberg.com','reuters.com','economictimes.com','moneycontrol.com',
    'timesofindia.com','businessstandard.com','livemint.com','thehindu.com',
    'ndtv.com','hindustantimes.com','financialexpress.com',
    # E-commerce
    'amazon.in','amazon.com','flipkart.com','snapdeal.com','meesho.com',
    # Company registry / government info
    'zaubacorp.com','tofler.in','mca.gov.in','cin.gov.in','roc.gov.in',
    # Blog / CMS hosting
    'wordpress.com','blogspot.com','blogger.com','wix.com','weebly.com',
    'squarespace.com','webnode.com','jimdo.com','strikingly.com',
    'tumblr.com','substack.com','ghost.io','typepad.com',
}

# Substrings that indicate a hosted/directory domain
_ICP_BLOCKED_PATTERNS = (
    '.wordpress.com', '.blogspot.com', '.wix.com',
    '.weebly.com', '.squarespace.com', '.webnode.',
)

# Domain keywords that indicate a directory/listing site
_DIRECTORY_DOMAIN_KEYWORDS = (
    'directory', 'listing', 'yellowpage', 'yellowpages',
    'justdial', 'indiamart', 'tradeindia', 'exporters',
    'suppliers', 'manufacturers', 'wholesale', 'catalogue',
    'catalog', 'b2bmart', 'bizmart',
)

# Preferred TLDs for genuine company websites (used for quality scoring)
_QUALITY_TLDS = ('.co.in', '.com', '.in', '.net', '.co')


def _enhance_icp_query(query: str) -> str:
    """Append business intent terms if not already present."""
    q = query.strip()
    business_terms = ['company','manufacturer','supplier','exporter','industry',
                      'industries','ltd','pvt','corporation','enterprise']
    if not any(t in q.lower() for t in business_terms):
        q += ' manufacturer OR supplier OR company'
    return q


def _icp_relevant(query: str, snippet: str) -> bool:
    """Return True if at least 50% of meaningful query words appear in snippet."""
    stop = {'in','at','of','the','a','an','and','or','for','to','with','by','near','around'}
    words = [w.lower() for w in re.split(r'\W+', query)
             if len(w) > 2 and w.lower() not in stop]
    if not words:
        return True
    s = snippet.lower()
    matched = sum(1 for w in words if w in s)
    return matched / len(words) >= 0.5


def _domain_ok(domain: str) -> bool:
    """Return False if the domain looks like a directory or CMS-hosted blog."""
    if any(p in domain for p in _ICP_BLOCKED_PATTERNS):
        return False
    if any(k in domain for k in _DIRECTORY_DOMAIN_KEYWORDS):
        return False
    return True


def _is_directory_snippet(snippet: str) -> bool:
    """Return True if the DDG snippet looks like a directory/listing page."""
    s = snippet.lower()
    # e.g. "Find 250 Steering Manufacturers in Pune" — number + plural business type
    if re.search(r'\b\d+[\s,]+\w+\s+(manufacturer|supplier|exporter|dealer|distributor|company|companies|vendor)', s):
        return True
    # e.g. "List of top steering manufacturers" — explicit list language
    if re.search(r'\b(list of|top \d+|directory of|database of|catalogue of)\b', s):
        return True
    # e.g. "Get quotes from verified suppliers"
    if re.search(r'\b(get quotes|verified supplier|compare price|send enquiry|post requirement)\b', s):
        return True
    return False


def run_icp_search(query: str, log_fn=None, location: str = "") -> list[dict]:
    def log(msg: str):
        console.print(msg)
        if log_fn:
            clean = re.sub(r'\[/?[^\]]*\]', '', msg).strip()
            if clean:
                log_fn(clean)

    results: list[dict] = []
    seen_domains: set[str] = set()
    enhanced = _enhance_icp_query(query)
    log(f"ICP Query: {enhanced}")

    if not _SEARCH_SEMAPHORE.acquire(blocking=True, timeout=30):
        log("Server is busy — too many concurrent searches. Please try again shortly.")
        return results

    try:
        log("Searching for companies (Bing + Brave + DDG)...")
        for r in _fetch_results(enhanced, max_results=config.MAX_PAGES * 10):
            url = (r.get('url') or '').split('?')[0]
            if not url.startswith('http'):
                continue
            try:
                domain = urlparse(url).netloc.lower().lstrip('www.')
            except Exception:
                continue
            if not domain:
                continue
            if any(b in domain for b in _ICP_BLOCKED):
                continue
            if not _domain_ok(domain):
                continue
            if domain in seen_domains:
                continue
            snippet = ((r.get('title') or '') + ' ' + (r.get('body') or '')).strip()
            if _is_directory_snippet(snippet):
                continue
            if not _icp_relevant(query, snippet):
                continue
            if location and not _location_matches(location, snippet):
                continue
            quality = any(domain.endswith(t) for t in _QUALITY_TLDS)
            seen_domains.add(domain)
            results.append({
                'url':         url,
                'title':       (r.get('title') or domain).strip(),
                'description': (r.get('body')  or '').strip(),
                'quality':     quality,
            })

        # sort: quality domains first
        results.sort(key=lambda x: not x['quality'])
        # strip internal quality flag before returning
        for res in results:
            res.pop('quality', None)

        log(f"Done — {len(results)} companies found.")
    except Exception as e:
        log(f"Search error: {e}")
    finally:
        _SEARCH_SEMAPHORE.release()

    return results


def run_search(query: str, log_fn=None, location: str = "", company: str = "") -> list[str]:
    def log(msg: str):
        console.print(msg)
        if log_fn:
            clean = re.sub(r'\[/?[^\]]*\]', '', msg).strip()
            if clean:
                log_fn(clean)

    all_urls: list[str] = []
    seen: set[str]      = set()
    base_query          = build_search_query(query)
    variants            = _build_query_variants(base_query)

    log(f"Query: {base_query}")

    if not _SEARCH_SEMAPHORE.acquire(blocking=True, timeout=30):
        log("Server is busy — too many concurrent searches. Please try again shortly.")
        return all_urls

    try:
        for attempt, q in enumerate(variants, 1):
            if attempt > 1:
                log(f"Few results — retrying with broader query (attempt {attempt})...")

            before = len(all_urls)
            for r in _fetch_results(q, max_results=config.MAX_PAGES * 15):
                url = (r.get('url') or '').split('?')[0]
                if not re.search(r'linkedin\.com/in/[^/?\s]+', url):
                    continue
                if url in seen:
                    continue
                snippet = ((r.get('title') or '') + ' ' + (r.get('body') or '')).lower()
                if not _location_matches(location, snippet):
                    continue
                if not _company_matches(company, snippet):
                    continue
                seen.add(url)
                all_urls.append(url)

            added = len(all_urls) - before
            log(f"Attempt {attempt}: +{added} profiles (total: {len(all_urls)})")

            # enough results — no need to try looser queries
            if len(all_urls) >= 5:
                break

        log(f"Done — {len(all_urls)} profiles collected.")
    except Exception as e:
        log(f"Search error: {e}")
    finally:
        _SEARCH_SEMAPHORE.release()

    return all_urls

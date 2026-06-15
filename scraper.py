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

# Indian state → major cities (so "Maharashtra" matches "Pune", "Mumbai", etc.)
_STATE_CITIES = {
    "maharashtra":    ["maharashtra", "mumbai", "pune", "nagpur", "nashik", "aurangabad", "thane", "navi mumbai", "solapur", "kolhapur"],
    "gujarat":        ["gujarat", "ahmedabad", "surat", "vadodara", "rajkot", "gandhinagar", "bhavnagar"],
    "karnataka":      ["karnataka", "bangalore", "bengaluru", "mysore", "hubli", "mangalore", "belgaum"],
    "tamil nadu":     ["tamil nadu", "chennai", "coimbatore", "madurai", "tiruchirappalli", "salem"],
    "telangana":      ["telangana", "hyderabad", "secunderabad", "warangal"],
    "andhra pradesh": ["andhra pradesh", "visakhapatnam", "vijayawada", "guntur", "tirupati"],
    "delhi":          ["delhi", "new delhi", "ncr", "noida", "gurugram", "gurgaon", "faridabad"],
    "haryana":        ["haryana", "gurugram", "gurgaon", "faridabad", "panipat", "ambala"],
    "rajasthan":      ["rajasthan", "jaipur", "jodhpur", "udaipur", "kota", "ajmer"],
    "punjab":         ["punjab", "ludhiana", "amritsar", "chandigarh", "jalandhar"],
    "uttar pradesh":  ["uttar pradesh", "noida", "lucknow", "kanpur", "agra", "ghaziabad", "meerut", "varanasi"],
    "west bengal":    ["west bengal", "kolkata", "calcutta", "durgapur", "howrah"],
    "madhya pradesh": ["madhya pradesh", "indore", "bhopal", "jabalpur", "gwalior"],
    "odisha":         ["odisha", "bhubaneswar", "cuttack", "rourkela"],
    "jharkhand":      ["jharkhand", "ranchi", "jamshedpur", "dhanbad"],
    "uttarakhand":    ["uttarakhand", "dehradun", "haridwar", "roorkee"],
    "himachal pradesh": ["himachal pradesh", "shimla", "manali", "baddi"],
    "goa":            ["goa", "panaji", "vasco"],
}


def _fetch_results(query: str, max_results: int, log_fn=None) -> list[dict]:
    """Query SearXNG (Bing + Brave + DDG); fall back to DDG directly if unavailable."""
    def _log(msg):
        console.print(msg)
        if log_fn:
            log_fn(re.sub(r'\[/?[^\]]*\]', '', msg).strip())

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
                _log(f"[green]SearXNG[/green]: {len(raw)} results (Bing + Brave + DDG)")
                return [
                    {
                        "url":   r.get("url",     ""),
                        "title": r.get("title",   ""),
                        "body":  r.get("content", ""),
                    }
                    for r in raw[:max_results]
                ]
            _log("[yellow]SearXNG returned 0 results — falling back to DDG[/yellow]")
        else:
            _log(f"[yellow]SearXNG HTTP {resp.status_code} — falling back to DDG[/yellow]")
    except Exception as e:
        _log(f"[yellow]SearXNG unavailable ({e}) — falling back to DDG[/yellow]")

    # Fallback: DDG directly
    try:
        with DDGS() as ddgs:
            raw = ddgs.text(query, max_results=max_results, region="in-en") or []
        _log(f"DDG fallback: {len(raw)} results")
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
    # city aliases (Bangalore ↔ Bengaluru etc.)
    if loc in _CITY_ALIASES:
        return any(a in s for a in _CITY_ALIASES[loc])
    # state → all major cities in that state
    if loc in _STATE_CITIES:
        return any(c in s for c in _STATE_CITIES[loc])
    # default: exact substring match
    return loc in s


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
        for r in _fetch_results(enhanced, max_results=config.MAX_PAGES * 10, log_fn=log):
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
            # no relevance or location post-filtering — query itself handles matching
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
            for r in _fetch_results(q, max_results=config.MAX_PAGES * 15, log_fn=log):
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

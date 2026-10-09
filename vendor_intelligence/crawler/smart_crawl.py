"""smart_crawl.py — Adaptive company intelligence crawler.

Phases:
  1. Homepage fetch (scrapling) -> extract all links
  2. LLM analyzes nav + user query -> prioritized crawl plan
  3. Parallel crawl: news sections (index->links->concurrent scrapling) +
     general sections (1-level scrapling, BFS fallback if blocked)
  4a. Direct article extraction (trafilatura) — raw full text, no LLM
  4b. LLM structured metadata extraction from general pages
"""
from __future__ import annotations
import asyncio, hashlib, heapq, json, os, re, time, warnings
warnings.filterwarnings("ignore", "unclosed transport", ResourceWarning)  # suppress Windows asyncio Playwright cleanup noise
from typing import Any
from urllib.parse import urljoin, urlparse

from openai import AsyncOpenAI
_client: Any = None
def _get_client() -> Any:
    global _client
    if _client is None:
        # Pydantic Settings reads OPENAI_API_KEY from .env into the Settings
        # object but does NOT export it to os.environ.  When smart_crawl is
        # imported by free_deep_research running inside a worker thread, the
        # AsyncOpenAI() default constructor sees no env var and blows up at
        # call time with: "The api_key client option must be set...".
        # Fall back to backend.config.get_settings() so the LLM extraction
        # phase works regardless of how this module was imported.
        api_key = os.environ.get("OPENAI_API_KEY") or ""
        if not api_key:
            try:
                from backend.config import get_settings
                api_key = get_settings().openai_api_key or ""
                if api_key:
                    os.environ["OPENAI_API_KEY"] = api_key  # propagate for any sub-clients
            except Exception:
                pass
        _client = AsyncOpenAI(api_key=api_key or None, max_retries=6)
    return _client
class _ClientProxy:
    def __getattr__(self, name): return getattr(_get_client(), name)
client = _ClientProxy()
def _get_llm_model() -> str:
    from backend.config import get_settings
    return get_settings().model_extract
LLM_MODEL = _get_llm_model()


def _llm_use_json_schema() -> bool:
    """OpenCode Zen / DeepSeek free tiers reject json_schema response_format."""
    flag = (os.getenv("SMART_CRAWL_USE_JSON_SCHEMA") or "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    model = (LLM_MODEL or "").lower()
    base = (os.getenv("OPENAI_BASE_URL") or "").lower()
    if "deepseek" in model or "nemotron" in model or "free" in model:
        return False
    if "opencode.ai" in base:
        return False
    return True


def _parse_llm_json_content(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


# Silence scrapling's verbose deprecation warning (fires on every AsyncFetcher() init)
# Must be set both before AND after scrapling import because setup_logger() adds a handler lazily
import logging as _logging
_logging.getLogger("scrapling").setLevel(_logging.CRITICAL)
log = _logging.getLogger(__name__)
try:
    from scrapling.fetchers import AsyncFetcher as _AF, DynamicFetcher as _DF
    AsyncFetcher = _AF
    DynamicFetcher = _DF
    _l = _logging.getLogger("scrapling")
    _l.setLevel(_logging.CRITICAL)
    for _h in _l.handlers:
        _h.setLevel(_logging.CRITICAL)
    _AF.configure(huge_tree=True)   # suppress the "use configure()" deprecation warning
except ImportError:
    AsyncFetcher = None
    DynamicFetcher = None

try:
    from scrapling.fetchers import StealthyFetcher as _SF
    StealthyFetcher = _SF
except ImportError:
    StealthyFetcher = None

# ── Browser concurrency cap + zombie reaper ──────────────────────────────────
# Why this exists: scrapling's StealthyFetcher.fetch()/DynamicFetcher.fetch()
# spawn Camoufox/Chromium child processes that can outlive the Python response
# object when the fetch times out or raises. On 2026-05-28 this leaked into a
# t3.medium OOM-kill of the uvicorn host. The cap bounds peak browser RAM, the
# reaper kills any child processes left over after each fetch.
_BROWSER_SEM = asyncio.Semaphore(int(os.environ.get("CRAWL_BROWSER_CONCURRENCY", "2")))
_BROWSER_PROC_PATTERNS = ("camoufox", "firefox", "chromium", "chrome", "headless_shell")


def _reap_browser_children() -> int:
    """Kill any browser subprocesses still parented to our PID. Returns count killed.

    Safe to call from any thread/coroutine — uses psutil's own waitpid-equivalent.
    No-op if psutil isn't installed (we don't make it a hard dep).
    """
    try:
        import psutil
    except ImportError:
        return 0
    killed = 0
    try:
        me = psutil.Process()
        for child in me.children(recursive=True):
            try:
                name = (child.name() or "").lower()
                if any(p in name for p in _BROWSER_PROC_PATTERNS):
                    child.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        # Reap any zombies our kill() just created
        try:
            psutil.wait_procs(me.children(recursive=True), timeout=0.5)
        except Exception:
            pass
    except Exception:
        pass
    return killed

# ── Constants / regexes ───────────────────────────────────────────────────────
CF_RE      = re.compile(r"just a moment|cf-browser-verification|enable javascript|human verification|attention required", re.I)
_SKIP_RE   = re.compile(
    r"/user/(login|register|password|logout|reset)(?:[/?]|$)"
    r"|/auth/(login|signup|register|logout)(?:[/?]|$)"
    r"|/signin(?:[/?]|$)|/signup(?:[/?]|$)"
    r"|/account/(login|register|logout|password|reset|verify)(?:[/?]|$)"
    r"|\.(zip|rar|7z|tar|gz|mp4|mp3|avi|mov|flv|wmv|jpg|jpeg|png|gif|bmp|webp|svg|ico|woff|woff2|ttf|eot)(\?.*)?$",
    re.I,
)
_DOC_RE    = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|csv)(\?.*)?$", re.I)
_H1_RE     = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAG_RE    = re.compile(r"<[^>]+>")
_DATE_RE   = re.compile(r"\b(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\b|\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b")
_A_HREF_RE    = re.compile(r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', re.I | re.S)
_CANONICAL_RE = re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.I)
_NEXT_PAGE_RE = re.compile(r'<link[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+)["\']', re.I)
_DATA_URL_RE  = re.compile(r'\bdata-(?:href|url|link|route|path)=["\']([^"\'#][^"\']*)["\']', re.I)
_JSONLD_RE    = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
_LOC_RE       = re.compile(r"<loc>(https?://[^<]+)</loc>")
_DATE_TEXT_RE = re.compile(
    r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?),?\s+(20\d{2})\b"
    r"|(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),?\s+(20\d{2})\b",
    re.I
)
_MONTH_MAP = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
# Hard cap on sitemap URL discovery — prevents OOM on e-commerce sitemaps with 100k+ SKU URLs.
# The seed cap in smart_crawl() only applies AFTER discovery; this caps the discovery list itself.
_SITEMAP_LOC_CAP = 2000

def _parse_text_date(text: str) -> str:
    """Extract ISO date from natural-language date in first 3000 chars of text."""
    for m in _DATE_TEXT_RE.finditer(text[:3000]):
        g = m.groups()
        try:
            if g[0]:  # DD Mon YYYY
                day, mon, yr = int(g[0]), _MONTH_MAP[g[1].lower()[:3]], int(g[2])
            else:      # Mon DD YYYY
                mon, day, yr = _MONTH_MAP[g[3].lower()[:3]], int(g[4]), int(g[5])
            return f"{yr:04d}-{mon:02d}-{day:02d}"
        except (ValueError, KeyError):
            continue
    return ""
_CAT_TYPE  = {"news":"news","press":"press_release","blog":"blog","events":"event","media":"news","other":"news"}
_NEWS_PATHS = ["/blog","/news","/newsroom","/press","/insights","/media","/updates","/announcements","/articles","/publications"]

# ── Crawl modes ───────────────────────────────────────────────────────────────
MODES: dict[str, dict] = {
    "full": {
        "paths":      [],
        "fields":     None,
        "max_pages":  80,
        "max_tokens": 5000,
        "label":      "full company intelligence",
        "include_docs": True,
    },
    "news": {
        "paths":      ["/blog", "/news", "/newsroom", "/press", "/insights",
                       "/media", "/updates", "/announcements", "/articles", "/publications"],
        "fields":     ["media"],
        "max_pages":  30,
        "max_tokens": 3000,
        "label":      "news articles and press coverage",
        "include_docs": False,
    },
    "investor": {
        "paths":      ["/investor", "/investors", "/ir", "/financials", "/annual",
                       "/ipo", "/sebi", "/bse", "/nse", "/disclosures", "/results", "/agm"],
        "fields":     ["media", "financials", "intel"],
        "max_pages":  40,
        "max_tokens": 6000,
        "label":      "investor relations, financial results, and disclosures",
        "include_docs": True,
    },
    "company": {
        "paths":      ["/about", "/about-us", "/company", "/team", "/leadership",
                       "/people", "/our-team", "/who-we-are", "/management",
                       "/products", "/product", "/solutions", "/services",
                       "/customers", "/case-studies", "/case-study",
                       "/contact", "/contact-us", "/locations", "/offices",
                       "/pricing", "/plans", "/why-us", "/why"],
        "fields":     ["company", "location", "business", "people", "contact", "intel"],
        "max_pages":  40,
        "max_tokens": 5000,
        "label":      "company profile, leadership, products and services",
        "include_docs": False,
    },
    "leads": {
        "paths":      ["/team", "/leadership", "/people", "/our-team", "/management",
                       "/customers", "/case-studies", "/partners", "/partnerships",
                       "/clients", "/contact", "/contact-us"],
        "fields":     ["people", "contact", "relationships"],
        "max_pages":  25,
        "max_tokens": 2500,
        "label":      "contacts, customers, partnerships and relationships",
        "include_docs": False,
    },
    "leadership": {
        "paths":      ["/about", "/team", "/management", "/leadership", "/board",
                       "/directors", "/founders", "/executive", "/our-people",
                       "/people", "/who-we-are", "/our-team", "/governance"],
        "fields":     ["company", "people"],
        "max_pages":  25,
        "max_tokens": 3000,
        "label":      "leadership, board of directors, key executives and team",
        "include_docs": False,
    },
    "business": {
        "paths":      ["/about", "/about-us", "/company", "/team", "/leadership",
                       "/products", "/product", "/solutions", "/services",
                       "/customers", "/case-studies", "/partners", "/partnerships",
                       "/blog", "/news", "/press", "/insights", "/resources",
                       "/investors", "/investor", "/financials", "/contact",
                       "/pricing", "/plans"],
        "fields":     ["company", "business", "people", "media", "financials", "intel"],
        "max_pages":  60,
        "max_tokens": 5000,
        "label":      "business intelligence: products, services, industries, investment data, news and people",
        "include_docs": True,
    },
}

# Universal valuable paths — boosted to priority 0 in EVERY mode (even "full"),
# because /about, /team, /contact, /pricing, /products are always high-signal
# pages for company intelligence regardless of what mode the caller picked.
_UNIVERSAL_VALUABLE_PATHS = (
    "/about", "/about-us", "/company", "/team", "/leadership", "/management",
    "/people", "/our-team", "/who-we-are", "/founders",
    "/products", "/product", "/solutions", "/services",
    "/customers", "/case-studies", "/case-study", "/clients",
    "/partners", "/partnerships",
    "/contact", "/contact-us", "/locations", "/offices",
    "/pricing", "/plans",
    "/careers", "/jobs",
)

def _mode_schema(fields: list[str] | None) -> dict:
    """Return a subset of INTEL_SCHEMA containing only the requested top-level fields."""
    if not fields:
        return INTEL_SCHEMA
    req   = [f for f in INTEL_SCHEMA["required"]    if f in fields]
    props = {k: v for k, v in INTEL_SCHEMA["properties"].items() if k in fields}
    return {**INTEL_SCHEMA, "required": req, "properties": props}

# ── Output schema (compact JSON Schema for OpenAI Structured Output) ──────────
def _obj(req: list[str], **props: Any) -> dict:
    return {"type":"object","additionalProperties":False,"required":req,"properties":props}
def _s() -> dict: return {"type":"string"}
def _ni() -> dict: return {"type":["integer","null"]}
def _arr(item: Any = None) -> dict: return {"type":"array","items":item or _s()}

INTEL_SCHEMA = _obj(
    ["company","location","business","financials","people","media","relationships","contact","intel"],
    company=_obj(
        ["name","legal_name","description","tagline","founded_year","website","ticker","type","stock_exchange","regulatory_status"],
        name=_s(), legal_name=_s(), description=_s(), tagline=_s(), founded_year=_ni(),
        website=_s(), ticker=_s(), type=_s(), stock_exchange=_s(), regulatory_status=_s(),
    ),
    location=_obj(
        ["headquarters","offices","countries","manufacturing_locations","regions_served"],
        headquarters=_s(), offices=_arr(), countries=_arr(),
        manufacturing_locations=_arr(), regions_served=_arr(),
    ),
    business=_obj(
        ["industries","key_markets","products","services","certifications","awards","technology","competitive_advantages","regulatory_compliance"],
        industries=_arr(), key_markets=_arr(),
        products=_arr(_obj(["name","category","description","features","target_customers","pricing"],
            name=_s(), category=_s(), description=_s(), features=_arr(), target_customers=_s(), pricing=_s())),
        services=_arr(_obj(["name","category","description","target_customers"],
            name=_s(), category=_s(), description=_s(), target_customers=_s())),
        certifications=_arr(), awards=_arr(), technology=_arr(),
        competitive_advantages=_arr(), regulatory_compliance=_arr(),
    ),
    financials=_obj(
        ["revenue","employee_count","funding","key_numbers","market_cap","fiscal_year","recent_fundraise","profitability"],
        revenue=_s(), employee_count=_s(), funding=_s(), key_numbers=_arr(),
        market_cap=_s(), fiscal_year=_s(), recent_fundraise=_s(), profitability=_s(),
    ),
    people=_obj(["leadership","board","advisors"],
        leadership=_arr(_obj(["name","title","bio","linkedin"], name=_s(), title=_s(), bio=_s(), linkedin=_s())),
        board=_arr(_obj(["name","title","bio"], name=_s(), title=_s(), bio=_s())),
        advisors=_arr(_obj(["name","title"], name=_s(), title=_s())),
    ),
    media=_obj(["articles","press_releases","investor_presentations"],
        articles=_arr(_obj(
            ["title","date","type","url","author","full_text","summary","entities","figures","quotes"],
            title=_s(), date=_s(), type=_s(), url=_s(), author=_s(), full_text=_s(),
            summary=_s(), entities=_arr(), figures=_arr(), quotes=_arr())),
        press_releases=_arr(_obj(["title","date","url","summary","highlights"],
            title=_s(), date=_s(), url=_s(), summary=_s(),
            highlights=_arr())),
        investor_presentations=_arr(_obj(["title","date","url","summary","highlights"],
            title=_s(), date=_s(), url=_s(), summary=_s(),
            highlights=_arr())),
    ),
    relationships=_obj(
        ["partnerships","customers","competitors","subsidiaries","parent_company","distributors","suppliers","investors"],
        partnerships=_arr(), customers=_arr(), competitors=_arr(), subsidiaries=_arr(),
        parent_company=_s(), distributors=_arr(), suppliers=_arr(), investors=_arr(),
    ),
    contact=_obj(
        ["email","phone","address","linkedin","twitter","facebook","instagram","youtube","other_social"],
        email=_s(), phone=_s(), address=_s(), linkedin=_s(), twitter=_s(),
        facebook=_s(), instagram=_s(), youtube=_s(), other_social=_arr(),
    ),
    intel=_obj(
        ["key_insights","recent_developments","strategic_focus","risks_challenges","opportunities","market_position","growth_drivers"],
        key_insights=_arr(), recent_developments=_arr(), strategic_focus=_arr(),
        risks_challenges=_arr(), opportunities=_arr(), market_position=_s(), growth_drivers=_arr(),
    ),
)
FINAL_SCHEMA = INTEL_SCHEMA

# ── Tier-1: scrapling AsyncFetcher (curl_cffi — real Chrome TLS fingerprint) ─────
_FETCHER: Any = None
def _get_fetcher() -> Any:
    global _FETCHER
    if _FETCHER is None and AsyncFetcher is not None:
        _FETCHER = AsyncFetcher()
    return _FETCHER

async def _scrapling_get_once(f: Any, url: str, timeout: int, verify: bool) -> str:
    # follow_redirects=False: SSRF defense. scrapling would otherwise follow
    # redirects without per-hop revalidation; an attacker-controlled domain
    # could 302 to an internal address. If a legitimate site requires a
    # redirect hop, the onboarding fallback (backend/utils/domain.safe_fetch)
    # handles it with per-hop validation.
    resp = await f.get(url, timeout=timeout, stealthy_headers=True, follow_redirects=False, verify=verify)
    for attr in ("html_content", "html", "content", "body"):
        val = getattr(resp, attr, None)
        if callable(val):
            try: val = val()
            except: val = None
        if val and len(str(val)) > 500:
            return str(val)
    if getattr(resp, "status", 200) in (403, 405, 429):
        return ""
    return ""


# Full browser headers — validated Tier-0 across 9 sites incl. Stripe, Shopify,
# Cloudflare, Tata, Siemens, Rakuten, Samsung, Notion. UA-only is NOT enough:
# Sec-Fetch-*, Accept-Language, Accept-Encoding matter for Cloudflare-protected sites.
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Upgrade-Insecure-Requests": "1",
}


# Anti-bot detection: catch Cloudflare/PerimeterX/Akamai challenge pages BEFORE
# wasting time parsing them for anchors. From AWS IPs, Cloudflare 403s a lot of
# B2B sites (volza.com, etc.) — those return a short HTML challenge with 0–1
# anchors, which used to silently degrade into "0 seed URLs → empty extraction".
_ANTIBOT_RE = re.compile(
    r"attention required\s*\|\s*cloudflare|"
    r"just a moment|"
    r"checking your browser|"
    r"enable javascript and cookies|"
    r"cf-(?:chl-bypass|browser-verification|chl-opt)|"
    r"_cf_chl_opt|"
    r"ddos protection by cloudflare|"
    r"<title>\s*403\b|"
    r"access denied|"
    r"managed challenge",
    re.IGNORECASE,
)


def _looks_blocked(status: int, html: str) -> bool:
    """Return True if status/body indicates an anti-bot challenge or block.

    The escalation cue used to be "anchor count < 8", but a Cloudflare 403
    typically returns a 5KB challenge page with exactly 1 anchor — the
    cascade missed it. Detect the block by content signature, not symptom.
    """
    if status in (403, 429, 503):
        return True
    return bool(html and _ANTIBOT_RE.search(html[:4000]))


async def stealthy_get(url: str, timeout_ms: int = 45000) -> str:
    """Tier-3: Camoufox (Firefox-based, anti-detection patched) via scrapling.
    Last-resort fetcher that defeats most Cloudflare managed-challenge pages
    where vanilla Playwright still gets 403'd. Costs ~5–15s per fetch.

    Cleanup contract (post-2026-05-28 OOM incident):
      - Bounded by _BROWSER_SEM so we never run more than N camoufox in parallel
      - Hard outer timeout (timeout_ms + 10s grace) via asyncio.wait_for
      - _reap_browser_children() in finally kills any subprocess still alive
    """
    if StealthyFetcher is None:
        return ""
    async def _do() -> str:
        try:
            resp = await asyncio.wait_for(
                StealthyFetcher.async_fetch(
                    url,
                    headless=True,
                    network_idle=True,
                    timeout=timeout_ms,
                    humanize=True,
                    os_randomize=True,
                ),
                timeout=(timeout_ms / 1000.0) + 10.0,
            )
            for attr in ("html_content", "html", "body"):
                val = getattr(resp, attr, None)
                if callable(val):
                    try: val = val()
                    except Exception: val = None
                if val and len(str(val)) > 500:
                    return str(val)
            return ""
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return ""
        except Exception:
            return ""
    async with _BROWSER_SEM:
        try:
            return await _do()
        finally:
            _reap_browser_children()


async def httpx_get(url: str, timeout: float = 12.0) -> str:
    """Tier-0: httpx + full browser headers. Fastest, works on most sites.

    Returns HTML on success (>500 bytes, no CF challenge), '' otherwise.
    Validated on 9 sites: beats scrapling on Stripe/Shopify/Cloudflare/Siemens/Zoho
    where scrapling returns 0 bytes due to curl_cffi TLS handshake issues.
    """
    from backend.utils.domain import safe_fetch
    try:
        r = await safe_fetch(url, headers=_BROWSER_HEADERS, timeout=timeout)
        if r.status_code != 200 or len(r.content) < 500:
            return ""
        html = r.text
        if CF_RE.search(html):  # Cloudflare challenge page — bail to scrapling
            return ""
        return html
    except Exception:
        return ""


async def scrapling_get(url: str, timeout: int = 15) -> str:
    """Tier-1: fast HTTP fetch via curl_cffi TLS fingerprint. Returns HTML or ''.

    Retries with TLS verification disabled when the server presents a cert
    chain curl_cffi can't validate (common for marketing sites with mis-served
    intermediates). This is acceptable here because we only scrape public
    HTML — no secrets are exchanged.
    """
    f = _get_fetcher()
    if not f:
        return ""
    try:
        return await _scrapling_get_once(f, url, timeout, verify=True)
    except Exception as e:
        msg = str(e).lower()
        if "ssl" in msg or "certificate" in msg or "tls" in msg:
            try:
                return await _scrapling_get_once(f, url, timeout, verify=False)
            except Exception as e2:
                if "DEBUG_SCRAPLING" in os.environ:
                    print(f"  [scrapling error after verify=False] {url[:80]} -> {type(e2).__name__}: {str(e2)[:100]}")
        elif "DEBUG_SCRAPLING" in os.environ:
            print(f"  [scrapling error] {url[:80]} -> {type(e).__name__}: {str(e)[:100]}")
    return ""

async def playwright_get(url: str) -> str:
    """Tier-2: full Playwright JS execution via scrapling DynamicFetcher.

    Same cleanup contract as stealthy_get — semaphore + hard timeout + reaper.
    Uses async_fetch where available; falls back to threaded fetch only if
    the running scrapling build doesn't expose it.
    """
    if DynamicFetcher is not None:
        async def _do_dynamic() -> str:
            try:
                fetch_fn = getattr(DynamicFetcher, "async_fetch", None)
                if fetch_fn is not None:
                    resp = await asyncio.wait_for(
                        fetch_fn(url, headless=True, network_idle=True, disable_resources=True),
                        timeout=60.0,
                    )
                else:
                    resp = await asyncio.wait_for(
                        asyncio.to_thread(
                            DynamicFetcher.fetch, url,
                            headless=True, network_idle=True, disable_resources=True,
                        ),
                        timeout=60.0,
                    )
                for attr in ("html_content", "html", "body"):
                    val = getattr(resp, attr, None)
                    if callable(val):
                        try: val = val()
                        except Exception: val = None
                    if val and len(str(val)) > 500:
                        return str(val)
                return ""
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return ""
            except Exception:
                return ""
        async with _BROWSER_SEM:
            try:
                html = await _do_dynamic()
                if html:
                    return html
            finally:
                _reap_browser_children()
    try:
        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_configs import CrawlerRunConfig, CacheMode
        async with AsyncWebCrawler(verbose=False) as cr:
            r = await cr.arun(url=url, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS))
            r = r if isinstance(r, list) else [r]
            if r and r[0].success:
                return r[0].html or ""
    except Exception:
        pass
    return ""

# ── URL utils ─────────────────────────────────────────────────────────────────
def _norm(u: str) -> str:
    p = urlparse(u)
    return p._replace(query="", fragment="").geturl().rstrip("/")

def _mark(url: str, seen: set) -> None:
    seen.add(_norm(url))
    seen.add(url)

def _txt(html: str) -> str:
    """Strip HTML tags, preserve paragraph breaks, collapse whitespace."""
    html = re.sub(r"<script\b[^>]*>.*?</script>",   " ", html, flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>",     " ", html, flags=re.I | re.S)
    html = re.sub(r"<noscript\b[^>]*>.*?</noscript>"," ", html, flags=re.I | re.S)
    html = re.sub(r"<(?:p|br|div|h[1-6]|li|tr|blockquote)(?:\s[^>]*)?>", "\n", html, flags=re.I)
    text = _TAG_RE.sub(" ", html)
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    cleaned = []
    for ln in lines:
        if not ln: cleaned.append(""); continue
        if len(ln) < 3: continue
        cleaned.append(ln)
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned))
    return result.strip()

_IMG_LINE_RE = re.compile(r"^\s*!?\[.*?\](?:\(.*?\))?\s*$", re.M)
_NAV_LINE_RE = re.compile(r"^\s*(?:Home|Menu|Skip to|Search|Share|Follow|Subscribe|Copyright|All rights|Privacy|Terms|Cookie|Accept|Reject|\d+\s+(?:min|read))\b.*$", re.I | re.M)
_SCRIPT_STRIP_RE = re.compile(r'<script\b[^>]*>.*?</script>', re.I | re.S)
_STYLE_STRIP_RE  = re.compile(r'<style\b[^>]*>.*?</style>',  re.I | re.S)
_JS_LINE_RE = re.compile(
    r'^\s*(?:\(function\b|window\.__|\{"@(?:context|type|graph)\b|\bvar\s+\w|\bjQuery\b|'
    r'document\.(?:write|createElement|querySelector)|c\[a\]=c\[a\]\|\|)', re.M)

def _clean_article_text(text: str, title: str) -> str:
    if not text: return ""
    lines = text.splitlines()
    title_norm = title.lower().strip()
    while lines and lines[0].strip().lower() == title_norm:
        lines.pop(0)
    text = "\n".join(lines)
    text = _JS_LINE_RE.sub("", text)
    text = _IMG_LINE_RE.sub("", text)
    text = _NAV_LINE_RE.sub("", text)
    text = re.sub(r"^\s*https?://\S+\s*$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _pre_clean_html(html: str) -> str:
    html = _SCRIPT_STRIP_RE.sub(' ', html)
    html = _STYLE_STRIP_RE.sub(' ', html)
    return html

def _extract_links(html: str, base: str) -> list[dict]:
    """Extract all crawlable same-domain links from an HTML page.

    Covers: <a href> anchors, <link rel=canonical>, <link rel=next> pagination,
    and SPA data-href/data-url/data-link/data-route/data-path attributes.
    """
    bh = urlparse(base).netloc.removeprefix("www.")
    base_norm = base.rstrip("/")
    seen: set[str] = set()
    links: list[dict] = []

    def _maybe_add(href: str, label: str) -> None:
        href = re.sub(r"[\r\n\t]", "", href).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            return
        full = urljoin(base, href)
        p = urlparse(full)
        if p.netloc.removeprefix("www.") != bh:
            return
        clean = p._replace(fragment="", query="").geturl()
        if clean in seen or clean == base_norm or _SKIP_RE.search(clean):
            return
        seen.add(clean)
        links.append({"url": clean, "text": label[:120]})

    for href, anchor in _A_HREF_RE.findall(html):
        _maybe_add(href, _TAG_RE.sub("", anchor).strip())
    for href in _CANONICAL_RE.findall(html):
        _maybe_add(href, "canonical")
    for href in _NEXT_PAGE_RE.findall(html):
        _maybe_add(href, "next page")
    for href in _DATA_URL_RE.findall(html):
        _maybe_add(href, "")
    return links

_SITEMAP_PATHS = [
    "/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz",
    "/news-sitemap.xml", "/sitemap_pages.xml",
    "/sitemap-images.xml", "/sitemap_news.xml",
    "/wp-sitemap.xml",
]

# Adaptive timeouts — bucketed by resource type (Fix 10a)
_TIMEOUT_FAST     = 8    # HTML pages: most respond < 3s, 8s is p99
_TIMEOUT_DOC      = 25   # PDFs / XLSX / DOCX: larger payloads
_TIMEOUT_SITEMAP  = 10   # sitemap.xml: mid-size XML
_TIMEOUT_PLAYWRIGHT = 20 # Playwright headless render ceiling

async def _sitemap_links(base_url: str, stats: dict | None = None) -> list[dict]:
    """Discover page URLs via robots.txt + 8 standard paths + trafilatura fallback.

    Recursively resolves sitemap indexes (sub-sitemap .xml URLs) one level deep.
    Writes per-source counts into `stats` dict if provided (for observability).
    """
    from backend.utils.domain import safe_fetch
    domain_url = base_url.rstrip("/")
    bh = urlparse(base_url).netloc.removeprefix("www.")

    # Tier 1: robots.txt Sitemap: directives (stdlib, no deps)
    # NB: RobotFileParser.read() is SYNC blocking I/O — must run in a thread
    # so it doesn't freeze the event loop and stall parallel discovery tasks.
    robot_sitemaps: list[str] = []
    crawl_delay = None
    def _robots_sync() -> tuple[list[str], int | None]:
        from urllib.robotparser import RobotFileParser
        rp = RobotFileParser()
        rp.set_url(f"{domain_url}/robots.txt")
        try:
            rp.read()
            sm  = list(rp.site_maps() or [])
            cd  = rp.crawl_delay("*")
            return sm, int(cd) if cd else None
        except Exception:
            return [], None
    try:
        robot_sitemaps, crawl_delay = await asyncio.wait_for(
            asyncio.to_thread(_robots_sync), timeout=5.0
        )
    except (asyncio.TimeoutError, Exception):
        pass

    if stats is not None:
        stats["robots_txt"] = {
            "fetched": bool(robot_sitemaps) or crawl_delay is not None,
            "sitemaps_declared": len(robot_sitemaps),
            "crawl_delay_seconds": int(crawl_delay) if crawl_delay else None,
        }

    async def _fetch_sitemap(sm_url: str) -> tuple[str, list[str]]:
        try:
            r = await safe_fetch(sm_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code != 200:
                return (sm_url, [])
            locs = _LOC_RE.findall(r.text)
            return (sm_url, [l.strip() for l in locs])
        except Exception:
            return (sm_url, [])

    # Tier 2: fetch all candidates concurrently (robots + 8 standard paths, deduplicated)
    candidates = list(dict.fromkeys(
        robot_sitemaps + [f"{domain_url}{p}" for p in _SITEMAP_PATHS]
    ))
    results = await asyncio.gather(*[_fetch_sitemap(c) for c in candidates])

    standard_paths_status: dict[str, int] = {}
    all_locs: list[str] = []
    indexes_found = 0
    sub_to_fetch: list[str] = []
    for sm_url, locs in results:
        path = urlparse(sm_url).path or sm_url
        standard_paths_status[path] = 200 if locs else 0
        if not locs:
            continue
        sub = [l for l in locs if l.rstrip("/").endswith((".xml", ".xml.gz"))]
        if sub:
            indexes_found += 1
        sub_to_fetch.extend(sub[:20])
        if len(all_locs) < _SITEMAP_LOC_CAP:
            all_locs.extend(l for l in locs if l not in sub)

    # Recurse into sub-sitemaps one level deep
    sub_fetched = 0
    if sub_to_fetch:
        # Deduplicate sub-sitemap URLs in case multiple indexes reference the same one
        sub_unique = list(dict.fromkeys(sub_to_fetch))[:50]
        sub_results = await asyncio.gather(*[_fetch_sitemap(s) for s in sub_unique], return_exceptions=True)
        for sr in sub_results:
            if isinstance(sr, tuple) and sr[1]:
                sub_fetched += 1
                remaining = _SITEMAP_LOC_CAP - len(all_locs)
                if remaining > 0:
                    all_locs.extend(sr[1][:remaining])

    # Tier 3: trafilatura as comprehensive fallback (handles recursive indexes natively)
    # NB: sitemap_search() is SYNC blocking I/O (network + parsing) — must run in
    # a thread with a hard timeout, otherwise it stalls the event loop for minutes
    # on slow sitemaps.
    trafilatura_count = 0
    def _traf_sitemap_sync() -> list[str]:
        try:
            from trafilatura.sitemaps import sitemap_search
            return list(sitemap_search(domain_url) or [])
        except Exception:
            return []
    try:
        fallback = await asyncio.wait_for(
            asyncio.to_thread(_traf_sitemap_sync), timeout=15.0
        )
        trafilatura_count = len(fallback)
        remaining = _SITEMAP_LOC_CAP - len(all_locs)
        if remaining > 0:
            all_locs.extend(fallback[:remaining])
    except (asyncio.TimeoutError, Exception):
        pass

    if stats is not None:
        stats["standard_paths"] = standard_paths_status
        stats["sitemap_index_recursion"] = {
            "indexes_found": indexes_found,
            "sub_sitemaps_fetched": sub_fetched,
            "total_leaf_urls": len(all_locs),
        }
        stats["trafilatura_fallback_urls"] = trafilatura_count

    # Dedupe + filter + format
    seen: set[str] = set()
    links: list[dict] = []
    for loc in all_locs:
        loc = loc.strip()
        if not loc.startswith("http"):
            continue
        if urlparse(loc).netloc.removeprefix("www.") != bh:
            continue
        if _SKIP_RE.search(loc) or _DOC_RE.search(loc):
            continue
        clean = urlparse(loc)._replace(fragment="", query="").geturl()
        if clean in seen:
            continue
        seen.add(clean)
        slug = urlparse(clean).path.rstrip("/").rsplit("/", 1)[-1]
        links.append({"url": clean, "text": slug.replace("-", " ").replace("_", " ")})
    return links


async def _feed_links(base_url: str) -> list[dict]:
    """Discover RSS/Atom feeds and extract their item URLs — no crawling required.

    `find_feed_urls()` is SYNC blocking network I/O — must run in a thread
    so it doesn't stall the event loop and block the parallel discovery fan-out.
    """
    def _find_feeds_sync() -> list[str]:
        try:
            from trafilatura.feeds import find_feed_urls
            return list(find_feed_urls(base_url) or [])
        except Exception:
            return []
    try:
        feed_urls = await asyncio.wait_for(
            asyncio.to_thread(_find_feeds_sync), timeout=12.0
        )
    except (asyncio.TimeoutError, Exception):
        return []
    if not feed_urls:
        return []
    from backend.utils.domain import safe_fetch
    bh = urlparse(base_url).netloc.removeprefix("www.")
    seen: set[str] = set()
    links: list[dict] = []
    for feed_url in feed_urls[:5]:
        try:
            r = await safe_fetch(feed_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            for href in re.findall(r'<link>\s*(https?://[^<\s]+)\s*</link>', r.text):
                href = href.strip()
                if urlparse(href).netloc.removeprefix("www.") != bh or href in seen:
                    continue
                if _SKIP_RE.search(href):
                    continue
                seen.add(href)
                slug = urlparse(href).path.rstrip("/").rsplit("/", 1)[-1]
                links.append({"url": href, "text": slug.replace("-", " ").replace("_", " ")})
            for href in re.findall(
                r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']alternate["\']',
                r.text, re.I,
            ):
                if urlparse(href).netloc.removeprefix("www.") != bh or href in seen:
                    continue
                if _SKIP_RE.search(href):
                    continue
                seen.add(href)
                links.append({"url": href, "text": ""})
        except Exception:
            continue
    return links


async def _wp_rest_links(base_url: str, stats: dict | None = None) -> list[dict]:
    """WordPress REST API enumeration. Returns all post+page URLs via /wp-json/wp/v2.

    Detection via /wp-json/wp/v2/posts?per_page=1 probe. Paginated up to 2000
    posts and 2000 pages (20 pages × 100 per_page). Writes detection flags
    and per-endpoint counts into `stats` dict if provided.
    """
    from backend.utils.domain import safe_fetch
    base_url = base_url.rstrip("/")
    probe = f"{base_url}/wp-json/wp/v2/posts?per_page=1"
    try:
        r = await safe_fetch(probe, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        # Accept either json content-type OR a body that parses as a JSON list
        # (some WP installs return application/octet-stream or text/plain on
        # behind-CDN edges, but the body is still valid JSON.)
        ok = False
        if r.status_code == 200:
            ct = r.headers.get("content-type", "").lower()
            if "json" in ct:
                ok = True
            else:
                try:
                    body = r.json()
                    ok = isinstance(body, list)
                except Exception:
                    ok = False
        if not ok:
            if stats is not None:
                stats["wp_rest_detected"] = False
                stats["wp_rest_posts"] = 0
                stats["wp_rest_pages"] = 0
            return []
    except Exception:
        if stats is not None:
            stats["wp_rest_detected"] = False
            stats["wp_rest_posts"] = 0
            stats["wp_rest_pages"] = 0
        return []

    bh = urlparse(base_url).netloc.removeprefix("www.")
    links: list[dict] = []
    counts = {"posts": 0, "pages": 0}
    _wp_deadline = time.perf_counter() + 45  # hard 45 s cap across all paginated requests
    for endpoint, key in (("/wp-json/wp/v2/posts", "posts"), ("/wp-json/wp/v2/pages", "pages")):
        page = 1
        while page <= 20:
            if time.perf_counter() > _wp_deadline:
                log.debug("_wp_rest_links: 45s budget exceeded, stopping pagination for %s", base_url)
                break
            url = f"{base_url}{endpoint}?per_page=100&page={page}&_fields=link,title"
            try:
                r = await safe_fetch(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code != 200:
                    break
                items = r.json()
                if not isinstance(items, list) or not items:
                    break
                for item in items:
                    link = item.get("link", "")
                    if not link or urlparse(link).netloc.removeprefix("www.") != bh:
                        continue
                    title_rendered = ""
                    t = item.get("title")
                    if isinstance(t, dict):
                        title_rendered = t.get("rendered", "") or ""
                    title = _TAG_RE.sub("", title_rendered).strip()
                    links.append({"url": link, "text": title[:120]})
                    counts[key] += 1
                if len(items) < 100:
                    break
                page += 1
            except Exception:
                break

    if stats is not None:
        stats["wp_rest_detected"] = True
        stats["wp_rest_posts"] = counts["posts"]
        stats["wp_rest_pages"] = counts["pages"]
    return links


def _jsonld_urls(html: str, bh: str) -> list[str]:
    """Extract same-domain URLs embedded in JSON-LD structured data blocks."""
    urls: list[str] = []
    for block in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block)
        except Exception:
            continue
        if isinstance(data, dict):
            items = data.get("@graph", [data])
        elif isinstance(data, list):
            items = data
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for field in ("url", "mainEntityOfPage", "@id"):
                val = item.get(field)
                if isinstance(val, dict):
                    val = val.get("@id") or val.get("url")
                if isinstance(val, str) and val.startswith("http"):
                    if urlparse(val).netloc.removeprefix("www.") == bh:
                        urls.append(val)
    return urls

# ── Phase 1: Homepage ─────────────────────────────────────────────────────────
async def fetch_homepage(base_url: str) -> tuple[str, list[dict], str, bool]:
    """Fetch homepage and extract nav links. Returns (text, links, html, escalated).

    Four-tier cascade:
      Tier 0: httpx + full browser headers — fastest, works on most sites
      Tier 1: scrapling (curl_cffi TLS fingerprint) — bot-protected sites
      Tier 2: Playwright (Chromium) — true SPA shells with <30KB SSR HTML
      Tier 3: StealthyFetcher (Camoufox) — Cloudflare managed-challenge bypass

    Escalation criterion: anchor-count < 8 OR `_looks_blocked()` is true (anti-bot
    signal). Without the blocked-check, a Cloudflare 403 page (~5KB, 1 anchor)
    used to slip past the cascade silently.
    """
    SHELL_THRESHOLD = 30_000
    html = await httpx_get(base_url, timeout=_TIMEOUT_FAST)
    links = _extract_links(html, base_url) if html else []
    blocked = _looks_blocked(0, html) if html else True
    if html and not blocked and (len(links) >= 8 or len(html) > SHELL_THRESHOLD):
        return _txt(html), links, html, False

    # Tier 1: scrapling fallback (bot-protected sites)
    s_html = await scrapling_get(base_url)
    s_links = _extract_links(s_html, base_url) if s_html else []
    s_blocked = _looks_blocked(0, s_html) if s_html else True
    if s_html and not s_blocked and (len(s_links) > len(links) or len(s_html) > len(html)):
        html, links, blocked = s_html, s_links, False
    if html and not blocked and (len(links) >= 8 or len(html) > SHELL_THRESHOLD):
        return _txt(html), links, html, False

    # Tier 2: True SPA shell — escalate to Playwright (20s cap)
    escalated = False
    try:
        js_html = await asyncio.wait_for(playwright_get(base_url), timeout=_TIMEOUT_PLAYWRIGHT)
    except (asyncio.TimeoutError, Exception):
        js_html = ""
    js_blocked = _looks_blocked(0, js_html) if js_html else True
    if js_html and not js_blocked and len(js_html) > len(html or ""):
        js_links = _extract_links(js_html, base_url)
        if len(js_links) > len(links):
            escalated = True
            return _txt(js_html), js_links, js_html, escalated

    # Tier 3: Cloudflare managed-challenge bypass via Camoufox (StealthyFetcher).
    # Only fires when the previous tiers either failed entirely OR returned an
    # anti-bot challenge page. Camoufox is heavy (~5-15s) so we don't make it
    # part of the default path.
    if blocked or _looks_blocked(0, html or "") or not (links or html):
        st_html = await stealthy_get(base_url, timeout_ms=int(_TIMEOUT_PLAYWRIGHT * 1500))
        st_links = _extract_links(st_html, base_url) if st_html else []
        if st_html and not _looks_blocked(0, st_html) and len(st_links) > len(links):
            escalated = True
            return _txt(st_html), st_links, st_html, escalated

    # Final fallback: crawl4ai (has built-in JS rendering + markdown output)
    async def _crawl4ai_homepage() -> tuple[str, str, list[dict]] | None:
        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_configs import CrawlerRunConfig, CacheMode
        async with AsyncWebCrawler(verbose=False) as cr:
            r = await cr.arun(url=base_url, config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS))
            r = r if isinstance(r, list) else [r]
            if r and r[0].success:
                c4_html = r[0].html or ""
                return (r[0].markdown or _txt(c4_html), c4_html, _extract_links(c4_html, base_url))
        return None
    try:
        out = await asyncio.wait_for(_crawl4ai_homepage(), timeout=_TIMEOUT_PLAYWRIGHT)
        if out:
            text, c4_html, c4_links = out
            if len(c4_links) > len(links):
                escalated = True
                return text, c4_links, c4_html, escalated
    except (asyncio.TimeoutError, Exception):
        pass

    return (_txt(html) if html else "", links, html or "", escalated)

# ── Document fetch + parse ────────────────────────────────────────────────────
async def _fetch_document(url: str) -> tuple[bytes, str]:
    from backend.utils.domain import safe_fetch
    ext = urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else ""
    try:
        r = await safe_fetch(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if r.status_code == 200:
            return r.content, ext
    except Exception:
        pass
    return b"", ext

def _parse_doc(data: bytes, ext: str) -> str:
    ext = ext.lower().lstrip(".")
    try:
        if ext == "pdf":
            import pymupdf
            doc = pymupdf.open(stream=data, filetype="pdf")
            pages_text: list[str] = []
            for page in doc:
                parts: list[str] = []
                try:
                    tabs = page.find_tables()
                    table_rects = [t.bbox for t in tabs.tables]
                    for blk in page.get_text("blocks"):
                        bx0, by0, bx1, by1, txt = blk[0], blk[1], blk[2], blk[3], blk[4].strip()
                        if not txt: continue
                        in_table = any(bx0 >= r[0]-5 and by0 >= r[1]-5 and bx1 <= r[2]+5 and by1 <= r[3]+5
                                       for r in table_rects)
                        if not in_table:
                            parts.append(txt)
                    for tab in tabs.tables:
                        rows = tab.extract()
                        if rows:
                            parts.append("\n".join(" | ".join(str(c or "").strip() for c in row) for row in rows))
                except Exception:
                    parts = [page.get_text()]
                pages_text.append("\n".join(parts))
            return "\n\n".join(pages_text).strip()
        if ext in ("doc", "docx"):
            import io; from docx import Document
            return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs if p.text.strip())
        if ext in ("xls", "xlsx"):
            import io, openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            lines = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    row_text = " | ".join(str(c) for c in row if c is not None)
                    if row_text.strip(): lines.append(row_text)
            return "\n".join(lines)
        if ext in ("ppt", "pptx"):
            import io; from pptx import Presentation
            prs = Presentation(io.BytesIO(data))
            return "\n".join(s.text.strip() for sl in prs.slides for s in sl.shapes if hasattr(s, "text") and s.text.strip())
        if ext == "csv":
            return data.decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""

def extract_articles(pages: list[dict]) -> list[dict]:
    import trafilatura
    _EMPTY = {"summary": "", "entities": [], "figures": [], "quotes": []}
    arts: list[dict] = []
    for p in pages:
        url, cat = p["url"], p.get("category", "news")
        if p.get("doc_bytes"):
            text = _parse_doc(p["doc_bytes"], p.get("doc_ext", ""))
            if not text or len(text) < 100: continue
            fname = urlparse(url).path.rsplit("/", 1)[-1]
            title = re.sub(r"\s+", " ", re.sub(r"[_%-]", " ", fname.rsplit(".", 1)[0])).strip().title()
            dm = re.search(r"(\d{2})(\d{2})(\d{4})", fname)
            arts.append({"title": title or fname, "date": f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}" if dm else "",
                         "type": _CAT_TYPE.get(cat, "document"), "url": url, "author": "", "full_text": text, **_EMPTY})
            continue
        html = p.get("html", "")
        if not html: continue
        html_clean = _pre_clean_html(html)
        text = trafilatura.extract(html_clean, url=url, include_tables=True, favor_precision=True, no_fallback=False)
        if not text or len(text) < 150:
            try:
                from scrapling.parser import Adaptor
                text = Adaptor(html_clean, url=url).get_all_text(ignore_tags=("script", "style", "nav", "footer"))
                if not text or len(text) < 150: continue
            except Exception as e:
                log.warning("smart_crawl: scrapling fallback failed for %s: %s", url, e)
                continue
        meta = trafilatura.extract_metadata(html, default_url=url)
        mt = (meta.title if meta else None) or ""
        title = ""
        m = _H1_RE.search(html)
        if m:
            h1 = _TAG_RE.sub("", m.group(1)).strip()
            if h1 and len(h1) > 5 and h1.lower() not in {"news","blog","press","events","media"}: title = h1
        if not title and mt and len(mt) > 10 and " " in mt: title = mt
        if not title:
            slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
            title = re.sub(r"[-_]", " ", slug).strip().title() if len(slug) > 5 else ""
        if not title: continue
        clean_text = _clean_article_text(text, title)
        if len(clean_text) < 100: continue
        date_str = str(meta.date)[:10] if meta and meta.date else ""
        if not date_str:
            date_str = _parse_text_date(clean_text)
        arts.append({"title": title, "date": date_str,
                     "type": _CAT_TYPE.get(cat, "news"), "url": url,
                     "author": str(meta.author) if meta and meta.author else "",
                     "full_text": clean_text, **_EMPTY})
    arts.sort(key=lambda a: a.get("date", ""), reverse=True)
    return arts

# ── Phase 4b: LLM metadata extraction (chunked + merged) ─────────────────────
def _norm_name(s: str) -> str:
    s = re.sub(r"\b(?:mr|mrs|ms|dr|shri|smt|late|former|prof)\.?\s*", "", s.lower())
    return re.sub(r"[^a-z0-9]", "", s)

def _merge_intel(base: dict, patch: dict) -> dict:
    if not base: return patch
    if not patch: return base
    for key, val in patch.items():
        if key not in base:
            base[key] = val
        elif isinstance(val, dict):
            base[key] = _merge_intel(base.get(key, {}), val)
        elif isinstance(val, list):
            existing_names: set[str] = set()
            existing_exact = {json.dumps(e, sort_keys=True) for e in base.get(key, [])}
            for e in base.get(key, []):
                if isinstance(e, dict) and e.get("name"):
                    existing_names.add(_norm_name(e["name"]))
            for item in val:
                exact_key = json.dumps(item, sort_keys=True)
                if exact_key in existing_exact:
                    continue
                if isinstance(item, dict) and item.get("name") and "title" in item:
                    nm = _norm_name(item["name"])
                    if nm in existing_names:
                        continue
                    existing_names.add(nm)
                base.setdefault(key, []).append(item)
                existing_exact.add(exact_key)
        elif isinstance(val, str) and val and not base.get(key):
            base[key] = val
        elif isinstance(val, (int, float)) and val and not base.get(key):
            base[key] = val
    return base

_SOURCE_PLATFORMS = {
    "wikipedia", "wikimedia", "linkedin", "crunchbase", "bloomberg", "reuters",
    "forbes", "zoominfo", "owler", "pitchbook", "tracxn", "moneycontrol",
    "yourstory", "inc42", "techcrunch", "venturebeat", "google", "google news",
    "bing", "duckduckgo", "facebook", "twitter", "x.com", "youtube", "instagram",
    "glassdoor", "indeed", "ambitionbox", "businesswire", "prnewswire",
    "the economic times", "economic times", "livemint", "mint", "business standard",
    "hindu business line", "the hindu", "ndtv", "cnbc", "ft", "financial times",
    "wsj", "wall street journal", "nyt", "new york times", "medium", "substack",
    "quora", "reddit", "github", "yelp", "trustpilot", "g2", "capterra",
}
_SOURCE_CATEGORIES = {
    "online encyclopedia", "encyclopedia", "reference site", "reference",
    "news aggregator", "news outlet", "news website", "news",
    "business directory", "directory", "search engine", "social network",
    "social media", "professional network", "blogging platform", "blog",
    "video platform", "review site", "data provider",
}

def _strip_source_artifacts(merged: dict) -> None:
    """Drop product/service entries that name the SOURCE platform rather than the
    focal company's own offering. Belt-and-suspenders for the prompt rule."""
    biz = merged.get("business") or {}
    for key in ("products", "services"):
        items = biz.get(key)
        if not isinstance(items, list):
            continue
        cleaned: list = []
        for it in items:
            if not isinstance(it, dict):
                cleaned.append(it)
                continue
            name = (it.get("name") or "").strip().lower()
            cat  = (it.get("category") or "").strip().lower()
            if name in _SOURCE_PLATFORMS or cat in _SOURCE_CATEGORIES:
                continue
            cleaned.append(it)
        biz[key] = cleaned

_LLM_SEM: asyncio.Semaphore | None = None

async def _extract_chunk(domain: str, chunk: str, until: str | None,
                         schema: dict, mode_label: str, max_tokens: int = 5000
                         ) -> tuple[dict, int, int, float]:
    """Returns (intel_dict, tokens_in, tokens_out, cost_inr).

    Tokens and cost surface into extraction_stats so callers can show real
    LLM spend per crawl without having to query the cost meter DB.
    """
    date_note = f"\nOnly include media.articles published on or before {until}." if until else ""
    prompt = (
        f"Extract structured company metadata from the web content below.\n"
        f"Focal company domain: {domain}  Focus: {mode_label}{date_note}\n\n"
        "Rules:\n"
        "- Extract ONLY what is explicitly stated — never invent or guess.\n"
        "- The CONTENT below may be sourced from multiple pages (the focal company's\n"
        "  own site, Wikipedia, news articles, third-party profiles, etc.).\n"
        "  Every field you extract must describe the FOCAL COMPANY (domain above),\n"
        "  NEVER the source page or its publisher.\n"
        "- business.products / business.services — STRICT:\n"
        "    * Only list products/services that the focal company itself manufactures,\n"
        "      sells, builds, or operates as its own offering.\n"
        "    * NEVER list the source platform (Wikipedia, LinkedIn, Crunchbase, Bloomberg,\n"
        "      Reuters, ZoomInfo, Owler, PitchBook, news outlets, encyclopedias, directories)\n"
        "      as a product — they are sources of information, not products of the focal company.\n"
        "    * If the chunk only describes the focal company in third-person prose without\n"
        "      naming concrete offerings, return [] — do NOT invent products from source metadata.\n"
        "    * Categories like 'Online Encyclopedia', 'News Aggregator', 'Business Directory',\n"
        "      'Reference Site' are red flags that you have extracted the source, not a product.\n"
        "- media.articles: leave as [] — articles are filled separately via trafilatura.\n"
        "- Include ALL products/services with full detail (name, category, features, pricing).\n"
        "- List ALL named executives, directors, board members, advisors (include Late/former).\n"
        "- financials.key_numbers: capture EVERY exact figure (revenue, margins, headcount, CAGR).\n"
        "- financials.employee_count + scale signals: aggressively extract company-size cues from\n"
        "  hero stats, trust strips, counter widgets, 'about us' badges, and footers. Patterns:\n"
        "    * 'Trusted by 500+ enterprises', '10,000+ customers', '1M+ users' → mention in key_numbers\n"
        "      AND, if it refers to client/customer count, treat it as scale evidence.\n"
        "    * '100+ employees', 'team of 250', 'over 1,000 professionals' → employee_count (verbatim).\n"
        "    * '25 years of experience', 'since 1995', 'established 1980' → company.founded_year + key_numbers.\n"
        "    * 'Offices in Mumbai, Delhi, Bangalore', 'present in 30 countries' → location.offices /\n"
        "      location.countries (list every named city / country verbatim).\n"
        "    * 'Pan-India presence', 'global reach across APAC' → location.regions_served.\n"
        "  Copy numbers/phrases VERBATIM — '500+', '10,000+ customers', 'over 1,000'. Do NOT invent.\n"
        "- media.press_releases / investor_presentations: write a detailed multi-sentence summary;\n"
        "  populate highlights with ALL key findings (financial tables, milestones, decisions).\n"
        "  ONLY add entries whose URL appears verbatim in this content chunk — no hallucination.\n"
        "- contact: extract LinkedIn, Twitter, Facebook, Instagram, YouTube URLs if present.\n"
        "- relationships.customers: ALL named customers / client logos / brands this company works with.\n"
        "  This is critical — every brand name in a 'Our clients' / 'Trusted by' / logo strip / case study\n"
        "  / testimonial belongs here. Examples to look for: Unilever, HSBC, Amazon, Nestle, Walmart, etc.\n"
        "- relationships.investors: all named investors.\n"
        "- business.industries — CRITICAL DISAMBIGUATION:\n"
        "  Populate with the CUSTOMER verticals this company SELLS TO or SERVES, NOT the industry the\n"
        "  company itself operates in. For vendors, agencies, SaaS, consultancies, service businesses the\n"
        "  target industries are the CLIENT verticals, not the company's own sector.\n"
        "  Examples:\n"
        "    * A digital-ad agency: industries = ['Retail E-commerce', 'Financial Services', 'CPG',\n"
        "      'Gaming'] — NOT ['Digital Advertising', 'Marketing', 'Media'].\n"
        "    * A B2B SaaS for warehouses: industries = ['Logistics', 'Manufacturing', 'Retail'] —\n"
        "      NOT ['Software', 'SaaS'].\n"
        "    * A market-research firm: industries = its clients' sectors — NOT ['Research', 'Consulting'].\n"
        "  Derivation rules (in order):\n"
        "    1. If the page has an explicit 'Industries we serve' / 'Industries' / 'Sectors' / 'Verticals'\n"
        "       section, copy the labels verbatim.\n"
        "    2. Otherwise, roll up relationships.customers: each unique customer's industry becomes an\n"
        "       entry in business.industries (e.g. Amazon → Retail E-commerce, HSBC → Financial Services,\n"
        "       Nestle → CPG).\n"
        "    3. If the company is a consumer brand / D2C / publisher with no B2B clients, use the\n"
        "       consumer channel it sells through ('Retail E-commerce', 'Grocery Retail'), NOT its product\n"
        "       category.\n"
        "  3-6 entries max. If there's genuinely no signal, leave empty.\n"
        "- business.key_markets: geographic markets ('India', 'GCC', 'APAC', 'North America'), NOT\n"
        "  industries. Keep this separate from business.industries.\n"
        "- intel: synthesis and actionable insights only.\n\n"
        f"Content:\n{chunk}"
    )
    global _LLM_SEM
    # Re-create the semaphore if it was created in a different event loop.
    # _smart_crawl_isolated runs in a thread with its own ProactorEventLoop;
    # if _LLM_SEM was created in the main (uvicorn) loop it's the wrong object
    # here and async with will deadlock. Checking the loop id handles this.
    try:
        cur_loop = asyncio.get_event_loop()
        if _LLM_SEM is None or getattr(_LLM_SEM, "_loop", cur_loop) is not cur_loop:
            _LLM_SEM = asyncio.Semaphore(3)
    except RuntimeError:
        _LLM_SEM = asyncio.Semaphore(3)
    json_note = (
        "\n\nReturn ONLY valid JSON matching the requested company_intel schema. "
        "No markdown fences or commentary."
    )
    attempts: list[dict[str, Any]] = []
    if _llm_use_json_schema():
        attempts.append(
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "company_intel",
                        "strict": True,
                        "schema": schema,
                    },
                }
            }
        )
    attempts.append({"response_format": {"type": "json_object"}})
    attempts.append({})

    last_err = ""
    for extra in attempts:
        try:
            async with _LLM_SEM:
                body: dict[str, Any] = {
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt + json_note}],
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "timeout": 30,
                }
                body.update(extra)
                resp = await client.chat.completions.create(**body)
            tin = tout = 0
            cost = 0.0
            try:
                from backend.cost.meter import record, estimate_llm_cost_inr

                tin = resp.usage.prompt_tokens if resp.usage else 0
                tout = resp.usage.completion_tokens if resp.usage else 0
                cost = estimate_llm_cost_inr(LLM_MODEL, tin, tout)
                record(
                    source="openai",
                    operation="smart_crawl_extraction",
                    cost_inr=cost,
                    tokens_in=tin,
                    tokens_out=tout,
                )
            except Exception as _ce:
                log.warning("smart_crawl cost record failed for %s: %s", domain, _ce)
            parsed = _parse_llm_json_content(resp.choices[0].message.content or "")
            if parsed:
                return parsed, tin, tout, cost
        except Exception as e:
            last_err = str(e)[:120]
            if extra and ("response_format" in last_err.lower() or "400" in last_err):
                continue
            print(f"  [LLM error chunk] {last_err}")
            return {}, 0, 0, 0.0
    if last_err:
        print(f"  [LLM error chunk] {last_err}")
    return {}, 0, 0, 0.0


async def final_extract(domain: str, pages: list[dict],
                        until: str | None = None, mode: str = "full",
                        ) -> tuple[dict, dict]:
    """Returns (merged_intel, llm_stats). llm_stats has chunk/token/cost totals."""
    CHUNK      = 50_000
    mode_cfg   = MODES.get(mode, MODES["full"])
    schema     = _mode_schema(mode_cfg["fields"])
    mode_label = mode_cfg["label"]

    HTML_CAP = {"full": 8_000,  "news": 5_000, "investor": 8_000,  "company": 8_000, "leads": 5_000}
    DOC_CAP  = {"full": 15_000, "news": 8_000, "investor": 20_000, "company": 12_000, "leads": 8_000}
    html_cap = HTML_CAP.get(mode, 8_000)
    doc_cap  = DOC_CAP.get(mode, 15_000)

    chunks: list[str] = []
    current = ""
    for p in pages:
        cap  = doc_cap if p.get("is_doc") else html_cap
        text = p["text"][:cap]
        header = f"=== PAGE: {p['url']} ===\n"
        body   = text + "\n\n"
        if len(header) + len(body) > CHUNK:
            if current:
                chunks.append(current); current = ""
            pos = 0
            while pos < len(body):
                chunks.append(header + body[pos:pos + CHUNK])
                pos += CHUNK
        elif current and len(current) + len(header) + len(body) > CHUNK:
            chunks.append(current)
            current = header + body
        else:
            current += header + body
    if current:
        chunks.append(current)

    # SHA-256 chunk dedup — skip identical chunks (header/footer boilerplate
    # repeated across pages). Per-call set; doesn't leak across crawls.
    seen_hashes: set[str] = set()
    deduped_chunks: list[str] = []
    skipped_dup = 0
    for c in chunks:
        h = hashlib.sha256(c[:2000].encode("utf-8", errors="ignore")).hexdigest()
        if h in seen_hashes:
            skipped_dup += 1
            continue
        seen_hashes.add(h)
        deduped_chunks.append(c)

    print(f"  -> {len(deduped_chunks)} LLM chunks "
          f"({sum(len(c) for c in deduped_chunks):,} chars total; "
          f"{skipped_dup} dup-skipped)")
    results = await asyncio.gather(
        *[_extract_chunk(domain, c, until, schema, mode_label, mode_cfg["max_tokens"])
          for c in deduped_chunks]
    )
    merged: dict = {}
    tot_in = tot_out = 0
    tot_cost = 0.0
    for intel, tin, tout, cost in results:
        merged = _merge_intel(merged, intel)
        tot_in += tin
        tot_out += tout
        tot_cost += cost
    _strip_source_artifacts(merged)
    llm_stats = {
        "chunks_total":   len(chunks),
        "chunks_deduped": skipped_dup,
        "chunks_sent":    len(deduped_chunks),
        "tokens_in":      tot_in,
        "tokens_out":     tot_out,
        "cost_inr":       round(tot_cost, 4),
    }
    return merged, llm_stats

# ── Main orchestrator ─────────────────────────────────────────────────────────
async def smart_crawl(
    domain: str,
    until_date: str | None = None,
    max_pages: int = 0,
    max_news_articles: int = 200,
    mode: str = "full",
) -> dict[str, Any]:
    t0 = time.perf_counter()
    domain   = domain.strip().removeprefix("http://").removeprefix("https://").rstrip("/")
    bh       = domain.removeprefix("www.")
    base_url = f"https://{domain}"

    mode_cfg   = MODES.get(mode, MODES["full"])
    mode_paths = mode_cfg["paths"]
    if max_pages == 0:
        max_pages = mode_cfg["max_pages"]

    print(f"\n[{domain}] Mode: {mode}")

    # ── Phase 1: Homepage + parallel discovery (sitemap + feed + WP REST) ──
    t_phase = time.perf_counter()
    discovery_stats: dict[str, Any] = {}
    wp_stats: dict[str, Any] = {}

    sitemap_task = asyncio.create_task(_sitemap_links(base_url, stats=discovery_stats))  # rls-allow: pure HTTP fetcher; no DB
    feed_task    = asyncio.create_task(_feed_links(base_url))  # rls-allow: pure HTTP fetcher; no DB
    wp_task      = asyncio.create_task(_wp_rest_links(base_url, stats=wp_stats))  # rls-allow: pure HTTP fetcher; no DB

    print(f"[{domain}] Phase 1: Homepage + parallel discovery (sitemap/feed/wp_rest)")
    t_hp = time.perf_counter()
    try:
        hp_text, nav_links, hp_html, nav_escalated = await fetch_homepage(base_url)
        homepage_ms = int((time.perf_counter() - t_hp) * 1000)

        sitemap_links, feed_links, wp_links = await asyncio.gather(
            sitemap_task, feed_task, wp_task, return_exceptions=True
        )
    finally:
        # Cancel any tasks that didn't complete (e.g. if fetch_homepage raised
        # or smart_crawl was cancelled mid-fetch). cancel() on done tasks is a no-op.
        for _disc_task in (sitemap_task, feed_task, wp_task):
            if not _disc_task.done():
                _disc_task.cancel()
    if isinstance(sitemap_links, BaseException): sitemap_links = []
    if isinstance(feed_links,    BaseException): feed_links    = []
    if isinstance(wp_links,      BaseException): wp_links      = []
    discovery_ms = int((time.perf_counter() - t_phase) * 1000)

    # Record discovery counts
    discovery_stats["nav_urls"] = len(nav_links)
    discovery_stats["nav_urls_playwright_escalated"] = nav_escalated
    discovery_stats["feed_urls"] = len(feed_links)
    discovery_stats["jsonld_urls"] = 0  # filled incrementally in BFS
    discovery_stats["discovery_elapsed_ms"] = discovery_ms
    discovery_stats.setdefault("wp_rest_detected", wp_stats.get("wp_rest_detected", False))
    discovery_stats.setdefault("wp_rest_posts", wp_stats.get("wp_rest_posts", 0))
    discovery_stats.setdefault("wp_rest_pages", wp_stats.get("wp_rest_pages", 0))

    # Seed union with per-URL source tracking
    seed_with_source: list[tuple[str, dict]] = []
    for l in nav_links:      seed_with_source.append(("nav",     l))
    for l in sitemap_links:  seed_with_source.append(("sitemap", l))
    for l in feed_links:     seed_with_source.append(("feed",    l))
    for l in wp_links:       seed_with_source.append(("wp_rest", l))

    seen_seeds: set[str] = set()
    seed: list[tuple[str, dict]] = []
    for src, l in seed_with_source:
        u = _norm(l["url"])
        if u in seen_seeds:
            continue
        seen_seeds.add(u)
        seed.append((src, l))

    # ── OOM guard (post-2026-05-29 mass-orphan incident) ──────────────────
    # A large sitemap (e.g. 553+ URLs) produced a seed list big enough that the
    # priority-queue scoring + per-URL state pinned RAM and OOM-killed the whole
    # uvicorn host mid-autochain — leaving 122 companies frozen at 'researching'.
    # We only ever crawl `max_pages` of these, so cap the seed at a generous
    # multiple of max_pages (nav links first — highest signal — already lead the
    # list). This bounds peak memory without losing crawl quality, since the BFS
    # would never reach the tail anyway.
    _SEED_CAP = max(60, max_pages * 8)
    if len(seed) > _SEED_CAP:
        print(f"[{domain}] seed capped {len(seed)} -> {_SEED_CAP} (OOM guard)")
        seed = seed[:_SEED_CAP]
    discovery_stats["total_seed"] = len(seed)

    print(f"[{domain}] nav={len(nav_links)} sitemap={len(sitemap_links)} "
          f"feed={len(feed_links)} wp_rest={len(wp_links)} -> seed={len(seed)}")
    if not seed and not hp_text:
        return {
            "domain": domain, "mode": mode, "error": "homepage_fetch_failed",
            "elapsed_s": round(time.perf_counter() - t0, 1),
            "discovery": discovery_stats, "data": {},
        }

    # ── Phase 2 setup: priority queue + observability state ──────────────
    news_sp = set(_NEWS_PATHS)
    seen: set[str] = {base_url, base_url.rstrip("/"), base_url + "/"}
    raw_pages: list[dict] = [{
        "url": base_url, "text": hp_text, "is_doc": False, "html": hp_html,
    }]
    source_of: dict[str, str] = {_norm(base_url): "homepage"}
    crawl_trace: list[dict] = []

    def _in_mode(url: str) -> bool:
        is_doc = bool(_DOC_RE.search(url))
        if is_doc:
            return mode_cfg.get("include_docs", True)
        if not mode_paths:
            return True
        p = urlparse(url).path.lower()
        return any(p == mp or p.startswith(mp + "/") or p.startswith(mp + "-") for mp in mode_paths)

    def _score(url: str) -> int:
        """heapq priority: 0 = high-signal path, 1 = shallow, 2 = deep.

        Priority 0 covers BOTH mode-specific paths (e.g. /investor for investor
        mode) AND universal valuable paths (/about, /team, /pricing, etc.) that
        are always high-signal regardless of mode. Without this, company-mode
        crawls of product-heavy nav (Vercel-style) skip the about/team pages.
        """
        p = urlparse(url).path.lower()
        if mode_paths and any(p == mp or p.startswith(mp + "/") for mp in mode_paths):
            return 0
        if any(p == vp or p.startswith(vp + "/") for vp in _UNIVERSAL_VALUABLE_PATHS):
            return 0
        if p.count("/") <= 2:
            return 1
        return 2

    # heapq of (priority, monotonic_counter, url) — counter breaks ties for stability
    pq: list[tuple[int, int, str]] = []
    _counter = 0
    for src, l in seed:
        url = l["url"]
        nm = _norm(url)
        if nm in seen or not _in_mode(url):
            continue
        seen.add(nm)
        source_of[nm] = src
        heapq.heappush(pq, (_score(url), _counter, url))
        _counter += 1

    print(f"[{domain}] Phase 2: BFS crawl (mode={mode}, budget={max_pages} pages, pq={len(pq)})")

    # ── Phase 2: priority BFS with full per-URL tracing ───────────────────
    t_phase = time.perf_counter()
    fetch_sem        = asyncio.Semaphore(24)  # bumped 12 -> 24 (Fix 10)
    playwright_calls = 0
    PLAYWRIGHT_CAP   = 15
    jsonld_total     = 0

    # Aggregate counters
    cs_fetched = cs_attempted = 0
    cs_pw_fb = cs_cf = cs_skip_re = cs_too_small = cs_dup = cs_docs = 0
    cs_bytes = 0
    cs_fetch_ms_list: list[int] = []
    cs_by_priority: dict[int, int] = {0: 0, 1: 0, 2: 0}
    cs_by_source: dict[str, int] = {}
    cs_docs_by_type: dict[str, int] = {}

    async def _fetch_one(url: str, priority: int, source: str) -> dict | None:
        nonlocal playwright_calls, jsonld_total
        nonlocal cs_fetched, cs_attempted, cs_pw_fb, cs_cf, cs_skip_re
        nonlocal cs_too_small, cs_dup, cs_docs, cs_bytes
        trace_row: dict[str, Any] = {
            "url": url, "source": source, "priority": priority,
            "status_code": None, "fetch_method": "skipped",
            "bytes": 0, "fetch_ms": 0, "cf_blocked": False,
            "skip_reason": None, "text_chars": 0, "used_for_llm": False,
        }
        if _SKIP_RE.search(url):
            trace_row["skip_reason"] = "skip_re"
            cs_skip_re += 1
            crawl_trace.append(trace_row)
            return None
        cs_attempted += 1
        cs_by_priority[priority] = cs_by_priority.get(priority, 0) + 1
        cs_by_source[source] = cs_by_source.get(source, 0) + 1

        async with fetch_sem:
            t_f = time.perf_counter()
            if _DOC_RE.search(url):
                data, ext = await _fetch_document(url)
                trace_row["fetch_method"] = "doc_fetch"
                trace_row["fetch_ms"] = int((time.perf_counter() - t_f) * 1000)
                trace_row["bytes"] = len(data) if data else 0
                cs_fetch_ms_list.append(trace_row["fetch_ms"])
                if not data:
                    trace_row["skip_reason"] = "doc_fetch_failed"
                    crawl_trace.append(trace_row)
                    return None
                text = _parse_doc(data, ext)
                if not text:
                    trace_row["skip_reason"] = "doc_parse_failed"
                    crawl_trace.append(trace_row)
                    return None
                trace_row["status_code"] = 200
                trace_row["text_chars"] = len(text)
                cs_docs += 1
                cs_docs_by_type[ext] = cs_docs_by_type.get(ext, 0) + 1
                cs_bytes += len(data)
                cs_fetched += 1
                crawl_trace.append(trace_row)
                return {"url": url, "text": text, "is_doc": True, "child_links": [], "_trace": trace_row}

            # HTML fetch — adaptive timeout
            html = await scrapling_get(url, timeout=_TIMEOUT_FAST)
            trace_row["fetch_method"] = "scrapling"
            if (not html or len(html) < 2000) and not CF_RE.search((html or "")[:500]):
                if playwright_calls < PLAYWRIGHT_CAP:
                    playwright_calls += 1
                    try:
                        js_html = await asyncio.wait_for(
                            playwright_get(url), timeout=_TIMEOUT_PLAYWRIGHT
                        )
                    except (asyncio.TimeoutError, Exception):
                        js_html = ""
                    if js_html and len(js_html) > len(html or ""):
                        html = js_html
                        trace_row["fetch_method"] = "playwright"
                        cs_pw_fb += 1

            trace_row["fetch_ms"] = int((time.perf_counter() - t_f) * 1000)
            cs_fetch_ms_list.append(trace_row["fetch_ms"])

            if html and CF_RE.search(html[:2000]):
                trace_row["cf_blocked"] = True
                trace_row["skip_reason"] = "cf_blocked"
                cs_cf += 1
                crawl_trace.append(trace_row)
                return None
            if not html or len(html) < 300:
                trace_row["skip_reason"] = "too_small"
                cs_too_small += 1
                crawl_trace.append(trace_row)
                return None

            trace_row["status_code"] = 200
            trace_row["bytes"] = len(html)
            cs_bytes += len(html)

            text = _txt(html)
            trace_row["text_chars"] = len(text)
            cs_fetched += 1

            # Child links: <a href>, canonical, pagination, data-* + JSON-LD URLs
            dom_children = _extract_links(html, url)
            jsonld_here = _jsonld_urls(html, bh)
            if jsonld_here:
                jsonld_total += len(jsonld_here)
                known_urls = {lk["url"] for lk in dom_children}
                for ju in jsonld_here:
                    if ju not in known_urls:
                        dom_children.append({"url": ju, "text": "jsonld"})

            child_links = [
                lk["url"] for lk in dom_children
                if urlparse(lk["url"]).netloc.removeprefix("www.") == bh
                and not _SKIP_RE.search(lk["url"])
                and _norm(lk["url"]) not in seen
                and _in_mode(lk["url"])
            ]
            crawl_trace.append(trace_row)
            return {
                "url": url, "text": text, "is_doc": False,
                "child_links": child_links, "html": html, "_trace": trace_row,
            }

    # Priority-BFS: pop highest-priority URLs in batches of up to 24
    BATCH = 24
    while pq and len(raw_pages) < max_pages:
        remaining = max_pages - len(raw_pages)
        batch_size = min(BATCH, remaining)
        batch: list[tuple[int, int, str]] = []
        while pq and len(batch) < batch_size:
            batch.append(heapq.heappop(pq))
        results = await asyncio.gather(*[
            _fetch_one(url, prio, source_of.get(_norm(url), "child_link"))
            for prio, _, url in batch
        ])
        for (prio, _, url), r in zip(batch, results):
            if not r:
                continue
            url_path   = urlparse(url).path.lower()
            keep_html  = mode in ("news", "investor") or any(url_path.startswith(sp) for sp in news_sp)
            raw_pages.append({
                "url": url, "text": r["text"], "is_doc": r["is_doc"],
                "html": r.get("html", "") if keep_html else "",
                "_trace": r.get("_trace"),
            })
            for cl in r.get("child_links", []):
                nm = _norm(cl)
                if nm in seen:
                    continue
                seen.add(nm)
                source_of[nm] = "child_link" if "jsonld" not in source_of.get(nm, "") else "jsonld"
                heapq.heappush(pq, (_score(cl), _counter, cl))
                _counter += 1
        # Early-termination for news: bail if remaining pq has zero mode-path hits
        if mode == "news" and len(raw_pages) >= max(20, max_pages // 2):
            if not any(p == 0 for p, _, _ in pq):
                print(f"  [early-exit] news mode — no more mode-path URLs in queue")
                break
        print(f"  fetched {len(raw_pages)}/{max_pages} pages, {len(pq)} queued...")

    bfs_ms = int((time.perf_counter() - t_phase) * 1000)
    discovery_stats["jsonld_urls"] = jsonld_total

    total      = len(raw_pages)
    blob_chars = sum(len(p["text"]) for p in raw_pages)

    crawl_stats = {
        "pages_fetched": cs_fetched,
        "pages_attempted": cs_attempted,
        "fetch_success_rate": round(cs_fetched / cs_attempted, 3) if cs_attempted else 0.0,
        "playwright_fallbacks": cs_pw_fb,
        "playwright_budget_remaining": PLAYWRIGHT_CAP - playwright_calls,
        "cf_blocked": cs_cf,
        "skipped_skip_re": cs_skip_re,
        "skipped_too_small": cs_too_small,
        "skipped_duplicate": cs_dup,
        "docs_fetched": cs_docs,
        "docs_by_type": cs_docs_by_type,
        "by_priority": cs_by_priority,
        "by_source": cs_by_source,
        "avg_fetch_ms": int(sum(cs_fetch_ms_list) / len(cs_fetch_ms_list)) if cs_fetch_ms_list else 0,
        "p95_fetch_ms": int(sorted(cs_fetch_ms_list)[int(len(cs_fetch_ms_list) * 0.95)]) if cs_fetch_ms_list else 0,
        "total_bytes": cs_bytes,
    }

    # ── Phase 3: Article extraction ───────────────────────────────────────
    t_phase = time.perf_counter()
    if mode in ("news", "investor"):
        news_raw = [{"url": p["url"], "html": p.get("html") or p["text"], "category": "news"}
                    for p in raw_pages if not p["is_doc"]]
    else:
        news_raw = [{"url": p["url"], "html": p.get("html") or p["text"], "category": "news"}
                    for p in raw_pages
                    if any(urlparse(p["url"]).path.lower().rstrip("/").startswith(sp) for sp in news_sp)]

    print(f"\n[{domain}] {total} pages, {blob_chars:,} chars ({len(news_raw)} for article extraction)")
    print(f"[{domain}] Phase 3: Article extraction")
    articles = extract_articles(news_raw)
    if until_date and articles:
        articles = [a for a in articles if not a["date"] or a["date"] >= until_date]
    print(f"  -> {len(articles)} articles")
    article_ms = int((time.perf_counter() - t_phase) * 1000)

    # ── Phase 4: LLM extraction ───────────────────────────────────────────
    t_phase = time.perf_counter()
    llm_pages = [p for p in raw_pages if p["text"] and len(p["text"]) >= 400]  # raised 100 -> 400 (Fix 8c)
    # Mark which pages made it to LLM in trace
    llm_urls = {p["url"] for p in llm_pages}
    for row in crawl_trace:
        if row["url"] in llm_urls:
            row["used_for_llm"] = True
    print(f"[{domain}] Phase 4: LLM metadata ({len(llm_pages)} pages, mode={mode})")
    data, llm_stats = await final_extract(domain, llm_pages, until_date, mode)
    data.setdefault("media", {})["articles"] = articles
    llm_ms = int((time.perf_counter() - t_phase) * 1000)

    # De-hallucinate press_releases / investor_presentations
    fetched_norms = {_norm(p["url"]) for p in raw_pages}
    for section in ("press_releases", "investor_presentations"):
        entries = data.get("media", {}).get(section, [])
        if not entries:
            continue
        valid = [e for e in entries if not e.get("url") or _norm(e["url"]) in fetched_norms]
        removed = len(entries) - len(valid)
        if removed:
            print(f"  [dehallu] {section}: removed {removed} unverified URL entries")
        data["media"][section] = valid

    elapsed = round(time.perf_counter() - t0, 1)
    print(f"[{domain}] Done in {elapsed}s")

    # ── Extraction / quality / diagnostic aggregates ──────────────────────
    ex_articles = articles
    extraction_stats = {
        "articles_found":        len(ex_articles),
        "articles_with_date":    sum(1 for a in ex_articles if a.get("date")),
        "articles_with_author":  sum(1 for a in ex_articles if a.get("author")),
        "llm_pages":             len(llm_pages),
        "llm_chunks_total":      llm_stats["chunks_total"],
        "llm_chunks_deduped":    llm_stats["chunks_deduped"],
        "llm_chunks_sent":       llm_stats["chunks_sent"],
        "llm_tokens_in":         llm_stats["tokens_in"],
        "llm_tokens_out":        llm_stats["tokens_out"],
        "llm_cost_inr":          llm_stats["cost_inr"],
        "extraction_elapsed_ms": article_ms + llm_ms,
    }

    # Quality: completeness over INTEL_SCHEMA top-level fields + entity counts
    def _non_empty(v: Any) -> bool:
        if v is None or v == "" or v == [] or v == {}:
            return False
        return True
    top_fields = list(INTEL_SCHEMA["properties"].keys())
    populated = 0
    fields_total = 0
    empty_sections: list[str] = []
    for f in top_fields:
        val = data.get(f)
        if isinstance(val, dict):
            if any(_non_empty(x) for x in val.values()):
                populated += sum(1 for x in val.values() if _non_empty(x))
            else:
                empty_sections.append(f)
            fields_total += len(val) if val else len(INTEL_SCHEMA["properties"][f].get("properties", {}))
        else:
            fields_total += 1
            if _non_empty(val):
                populated += 1
    quality = {
        "fields_populated": populated,
        "fields_total": fields_total,
        "completeness": round(populated / fields_total, 3) if fields_total else 0.0,
        "entity_counts": {
            "products":       len((data.get("business") or {}).get("products", []) or []),
            "services":       len((data.get("business") or {}).get("services", []) or []),
            "leadership":     len((data.get("people")   or {}).get("leadership", []) or []),
            "board":          len((data.get("people")   or {}).get("board", []) or []),
            "articles":       len((data.get("media")    or {}).get("articles", []) or []),
            "press_releases": len((data.get("media")    or {}).get("press_releases", []) or []),
            "partnerships":   len((data.get("relationships") or {}).get("partnerships", []) or []),
            "customers":      len((data.get("relationships") or {}).get("customers", []) or []),
        },
        "empty_sections": empty_sections,
    }

    diagnostics = {
        "had_robots_txt": discovery_stats.get("robots_txt", {}).get("fetched", False),
        "had_sitemap":    len(sitemap_links) > 0,
        "had_feed":       len(feed_links) > 0,
        "is_wordpress":   discovery_stats.get("wp_rest_detected", False),
        "uses_cloudflare": cs_cf > 0,
        "is_spa":         nav_escalated,
        "warnings":       [],
    }
    if len(nav_links) < 8:  diagnostics["warnings"].append("sparse nav")
    if not sitemap_links:   diagnostics["warnings"].append("no sitemap found")
    if cs_cf > 0:           diagnostics["warnings"].append(f"{cs_cf} pages CF-blocked")

    phases_ms = {
        "discovery": discovery_ms,
        "homepage_fetch": homepage_ms,
        "bfs_crawl": bfs_ms,
        "article_extraction": article_ms,
        "llm_extraction": llm_ms,
        "total": int((time.perf_counter() - t0) * 1000),
    }

    raw_summary = [{"url": p["url"], "chars": len(p["text"]), "is_doc": p["is_doc"]} for p in raw_pages]
    return {
        "domain": domain, "mode": mode, "elapsed_s": elapsed,
        "pages_fetched": total, "blob_chars": blob_chars, "until_date": until_date,
        "raw_pages": raw_summary,
        "discovery": discovery_stats,
        "crawl_stats": crawl_stats,
        "crawl_trace": crawl_trace,
        "extraction_stats": extraction_stats,
        "quality": quality,
        "diagnostics": diagnostics,
        "phases_ms": phases_ms,
        "data": data,
    }

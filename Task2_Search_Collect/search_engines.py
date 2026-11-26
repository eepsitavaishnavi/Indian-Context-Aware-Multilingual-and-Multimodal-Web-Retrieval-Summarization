"""
search_engines.py
Multi-engine search with Hindi/Devanagari/Roman/English support.

Cascade: Brave Search API (primary) -> ddgs auto backend -> ddgs Bing
backend -> ddgs worldwide region (last resort).

`search_web()` returns a TUPLE `(results, engine_name)` so the caller can
record which engine actually served each query. `engine_name` is one of:
    'brave' | 'ddgs-auto' | 'ddgs-bing' | 'ddgs-worldwide' | 'none'

For Devanagari queries, the ddgs cascade tries Bing FIRST.

Search results are cached in-memory for the lifetime of the Python process
(CACHE_TTL_SECONDS = 1 hour). Cache is keyed by the full call signature,
so different regions / max_results / language hints are separate entries.
Restarting the script clears the cache.
"""

import os
import re
import time
from urllib.parse import urlsplit

import requests

# The ddgs package was renamed from `duckduckgo_search` in 2024. Support both
# so the pipeline imports cleanly in environments that have either installed.
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

# Read from the environment. Never hardcode a real key here.
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# Results requested per search, for Brave and every ddgs fallback.
# Brave's web search accepts at most 20 per request.
TOP_K = 15

_DDGS_TO_BRAVE_COUNTRY = {
    "in-en": "IN",
    "wt-wt": "ALL",
    "us-en": "US",
    "uk-en": "GB",
}

_DEVANAGARI_RE = re.compile(r'[\u0900-\u097F]')


# ═══════════════════════════════════════════════════════════
# SEARCH RESULT CACHE
# ═══════════════════════════════════════════════════════════
# In-memory cache, keyed by the full call signature that produced the
# results. Lives for the duration of the Python process — restarting the
# script clears it.
#
# What this protects against:
#   * Task 1 sending the same query string twice in one batch
#   * Calling search_web() repeatedly from a notebook
#   * Development-time re-runs within a single interpreter session
#
# What it does NOT do:
#   * Persist across separate `python main.py` invocations
#   * Serve stale results beyond CACHE_TTL_SECONDS
#
# Cache key includes every argument that changes the results, so
# ("test", "in-en", 10, "en", True) and ("test", "in-en", 15, "en", True)
# are distinct entries and don't clobber each other.

_search_cache = {}

CACHE_TTL_SECONDS = 3600  # 1 hour


def _make_cache_key(query, region, max_results, search_lang, using_brave):
    """Build a hashable key that captures everything that affects the result."""
    return (
        query.strip().lower(),
        region or '',
        int(max_results),
        (search_lang or 'en').lower(),
        bool(using_brave),
    )


def _cache_get(key):
    now = time.time()
    entry = _search_cache.get(key)
    if entry is None:
        return None
    results, engine_name, ts = entry
    if now - ts >= CACHE_TTL_SECONDS:
        del _search_cache[key]
        return None
    return results, engine_name


def _cache_put(key, results, engine_name):
    _search_cache[key] = (results, engine_name, time.time())


def clear_search_cache():
    """Public helper — useful for testing or long-lived processes."""
    _search_cache.clear()


def search_cache_stats():
    """Return a small dict describing the cache state."""
    return {'entries': len(_search_cache)}


# ═══════════════════════════════════════════════════════════
# BRAVE
# ═══════════════════════════════════════════════════════════
def _is_real_brave_key(key):
    """A key is 'real' only if non-empty and not the placeholder main.py ships with."""
    if not key:
        return False
    if key.startswith("PASTE_") or key.startswith("PASTE_YOUR"):
        return False
    return True


def set_brave_api_key(api_key):
    global BRAVE_API_KEY
    BRAVE_API_KEY = api_key


def search_brave(query, region="in-en", max_results=TOP_K, api_key=None, search_lang=None):
    """
    Query the official Brave Search API.
    Returns a list of results (possibly empty). Never raises.
    """
    key = api_key or BRAVE_API_KEY
    if not _is_real_brave_key(key):
        return []

    country = _DDGS_TO_BRAVE_COUNTRY.get(region, "IN")
    brave_search_lang = search_lang or "en"
    if brave_search_lang in {"roman_hindi", "mixed"}:
        brave_search_lang = "hi"
    elif brave_search_lang not in {"en", "hi"}:
        brave_search_lang = "en"

    try:
        response = requests.get(
            BRAVE_ENDPOINT,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": key,
            },
            params={
                "q": query,
                "count": max_results,
                "country": country,
                "search_lang": brave_search_lang,
            },
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            web_results = data.get("web", {}).get("results", [])
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "text": r.get("description", ""),
                }
                for r in web_results[:max_results]
            ]
        elif response.status_code == 401:
            print("[ERROR] Brave API key rejected (401) — check the env var")
        elif response.status_code == 429:
            print("[WARNING] Brave API rate/quota limit hit (429)")
            time.sleep(2)
        else:
            print(f"[WARNING] Brave API returned HTTP {response.status_code}")

    except Exception as e:
        print(f"[WARNING] Brave search failed: {e}")

    return []


# ═══════════════════════════════════════════════════════════
# DDGS
# ═══════════════════════════════════════════════════════════
def _try_ddgs_backend(query, region, max_results, backend):
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, region=region, max_results=max_results, backend=backend):
                url = r.get("href", "")
                if not isinstance(url, str):
                    continue
                try:
                    parsed = urlsplit(url)
                    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                        continue
                except ValueError:
                    continue
                results.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "text": r.get("body", ""),
                })
    except Exception as e:
        print(f"[WARNING] ddgs '{backend}' backend failed: {e}")
    return results


def search_ddgs(query, region="in-en", max_results=TOP_K, prefer_bing=False):
    """Returns (results, backend_name)."""
    backends = ["bing", "auto"] if prefer_bing else ["auto", "bing"]
    for backend in backends:
        results = _try_ddgs_backend(query, region, max_results, backend)
        if results:
            return results, f"ddgs-{backend}"
    return [], "ddgs-none"


# ═══════════════════════════════════════════════════════════
# MAIN ENTRY
# ═══════════════════════════════════════════════════════════
def search_web(query, region="in-en", max_results=TOP_K, api_key=None, search_lang=None):
    """
    Main search function: Brave -> ddgs (Bing-first for Devanagari) -> worldwide.
    Returns (results, engine_name).

    Cached by (query, region, max_results, search_lang, using_brave).
    Cache is process-local; restarting the script clears it.
    """
    using_brave = _is_real_brave_key(api_key or BRAVE_API_KEY)
    cache_key = _make_cache_key(query, region, max_results, search_lang, using_brave)

    cached = _cache_get(cache_key)
    if cached is not None:
        results, engine_name = cached
        print(f"[CACHE] Hit for: {query}  (engine was {engine_name}, {len(results)} results)")
        return results, engine_name

    has_devanagari = bool(_DEVANAGARI_RE.search(query))
    engine_hint = "bing-first" if has_devanagari else "auto-first"
    print(f"[INFO] Searching for: {query} (region={region}, lang={search_lang}, ddgs-order={engine_hint})")

    # Step 1: Brave (primary, official)
    results = search_brave(query, region, max_results,
                           api_key=api_key, search_lang=search_lang)
    if results:
        print(f"[INFO] Got {len(results)} results from Brave")
        _cache_put(cache_key, results, "brave")
        return results, "brave"

    # Step 2: ddgs (Bing-first for Devanagari queries)
    results, backend = search_ddgs(query, region, max_results, prefer_bing=has_devanagari)
    if results:
        print(f"[INFO] Got {len(results)} results from {backend}")
        _cache_put(cache_key, results, backend)
        return results, backend

    # Step 3: Final fallback — worldwide region
    print("[WARNING] No results, trying worldwide region...")
    results, backend = search_ddgs(query, "wt-wt", max_results, prefer_bing=has_devanagari)
    if results:
        print(f"[INFO] Got {len(results)} results from worldwide via {backend}")
        _cache_put(cache_key, results, "ddgs-worldwide")
        return results, "ddgs-worldwide"

    # Even empty results get cached — prevents re-querying a query that
    # legitimately returned nothing during this run.
    print(f"[ERROR] No results found for: {query}")
    _cache_put(cache_key, [], "none")
    return [], "none"

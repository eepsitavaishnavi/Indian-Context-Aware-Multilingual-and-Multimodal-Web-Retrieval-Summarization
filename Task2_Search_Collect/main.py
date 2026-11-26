"""
main.py - Task 2: On-the-Fly Web Search, Scraping & Multimodal Content Acquisition

Full compliance with Task 2 requirements:
  - Retrieve top-k (TOP_K = 15) per multilingual query
  - Merge duplicate URLs (with provenance preservation)
  - Study licences (CC0/CC BY/CC BY-SA/open government/institutional)
  - Respect robots.txt + meta robots + X-Robots-Tag + rate limits + access restrictions
  - Collect HTML, linked PDFs, linked images (when permitted)
  - Store metadata: URL, title, author/org, pub_date, domain, language, content_type
  - Accurately identify direct-extraction vs OCR content

Downloads are organised per-query:
    downloads/html_pages/<query_slug>/<uid>_<slug>.html
    downloads/pdfs/<query_slug>/<uid>_<slug>.pdf
    downloads/photos/<query_slug>/<uid>_<slug>.<ext>
where <query_slug> is derived from `original_query` and (in a batch run)
prefixed with a 1-based run index so folders sort in the order asked.

Brave Search API key is read from the environment variable BRAVE_API_KEY.
If unset, the search cascade falls through to DuckDuckGo automatically.

Licence study:
  `licence_category`   — what the page declared (cc-by-4.0, proprietary, unknown…)
  `source_type`        — what kind of institution hosts it (government, academic…)
  `licence_candidates` — if no licence tag was found, a candidate suggested by
                         source_type (e.g. government_open). This is a SIGNAL,
                         not a reclassification — `licence_category` stays 'unknown'.
"""

import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.robotparser
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

import requests

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    import PyPDF2
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

from search_engines import TOP_K, search_web

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

OUTPUT_FILE = "task3_input.json"
DOWNLOAD_DIR = "downloads"
TASK1_INPUT_FILE = "task1_input.json"

TASK2_SCHEMA_VERSION = "1.0"

ALLOWED_CONTENT_TYPES = {"pdf", "image", "html"}
DOWNLOAD_SUBDIRS = {
    "html": "html_pages",
    "pdf": "pdfs",
    "image": "photos",
}
for _sub in DOWNLOAD_SUBDIRS.values():
    os.makedirs(os.path.join(DOWNLOAD_DIR, _sub), exist_ok=True)

USER_AGENT = "ResearchBot/1.0 (+https://example.org/bot; contact: research@example.org)"

GLOBAL_QUERY_DELAY = 1.0
DEFAULT_DOMAIN_DELAY = 1.5
HTTP_TIMEOUT = 15
MAX_RETRIES = 2

IMAGE_MIN_BYTES = 10_000

# Linked resources followed from a collected HTML page. None = no limit.
MAX_LINKED_PDFS_PER_PAGE = None
MAX_LINKED_IMAGES_PER_PAGE = 5
MAX_LINKED_FILES_PER_QUESTION = None   # optional global safety valve

PDF_FUNNEL_COUNTERS = (
    'pdf_links_seen', 'pdf_skipped_outside_content', 'pdf_links_after_dedup',
    'pdf_skipped_by_cap',
    'pdf_fetched_ok', 'pdf_fetch_failed',
)


# ═══════════════════════════════════════════════════════════
# SOURCE TYPE (domain-based)
# ═══════════════════════════════════════════════════════════
_KNOWN_SOURCE_OVERRIDES = {
    'rbi.org.in': 'government',
    'rbi.in':     'government',
    'mygov.in':   'government',
    'constitutionofindia.net': 'non_profit',
}


def classify_source_type(domain):
    """
    Classify a domain's institutional character from its TLD suffix.
    This is a SIGNAL, not a licence claim.
    """
    if not domain:
        return 'other'
    d = domain.lower().rstrip('.')

    if d in _KNOWN_SOURCE_OVERRIDES:
        return _KNOWN_SOURCE_OVERRIDES[d]

    if d.endswith(('.gov.in', '.nic.in', '.gov')):
        return 'government'
    if d.endswith(('.ac.in', '.edu.in', '.res.in', '.ernet.in',
                   '.edu', '.ac.uk')):
        return 'academic'
    if d.endswith(('.org.in', '.org')):
        return 'non_profit'
    if d.endswith(('.com', '.net', '.co.in', '.in', '.io', '.live')):
        return 'commercial'
    return 'other'


def licence_candidates(source_type, licence_category):
    """If no licence was declared, return a candidate implied by source_type."""
    if licence_category != 'unknown':
        return []
    if source_type == 'government':
        return ['government_open']
    if source_type == 'academic':
        return ['institutional_open']
    return []


# ═══════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════
_robots_cache = {}
_last_request_time = {}
_global_last_request = [0.0]


# ═══════════════════════════════════════════════════════════
# ROBOTS.TXT
# ═══════════════════════════════════════════════════════════
def _normalize_for_robots(url):
    p = urllib.parse.urlparse(url)
    decoded = urllib.parse.unquote(p.path)
    reencoded = urllib.parse.quote(decoded, safe="/")
    return p._replace(path=reencoded).geturl()


def _load_robots(domain, scheme='https'):
    if domain in _robots_cache:
        return _robots_cache[domain]

    entry = {'parser': None, 'crawl_delay': None}
    robots_url = f"{scheme}://{domain}/robots.txt"

    try:
        resp = requests.get(
            robots_url,
            headers={'User-Agent': USER_AGENT},
            timeout=8,
            allow_redirects=True,
        )
        if resp.status_code == 200:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            entry['parser'] = rp

            cd = None
            try:
                rate = rp.crawl_delay(USER_AGENT) or rp.crawl_delay('*')
                if rate:
                    cd = float(rate)
            except Exception:
                pass
            if cd is None:
                try:
                    rr = rp.request_rate(USER_AGENT) or rp.request_rate('*')
                    if rr and getattr(rr, 'seconds', 0):
                        cd = rr.seconds / max(rr.requests, 1)
                except Exception:
                    pass
            entry['crawl_delay'] = cd
    except Exception:
        pass

    _robots_cache[domain] = entry
    return entry


def check_robots_allowed(url):
    """Returns (allowed, crawl_delay)."""
    try:
        parsed = urllib.parse.urlparse(url)
        entry = _load_robots(parsed.netloc, parsed.scheme)
        if entry['parser'] is None:
            return True, entry['crawl_delay']
        clean = _normalize_for_robots(url)
        allowed = (entry['parser'].can_fetch(USER_AGENT, clean) or
                   entry['parser'].can_fetch('*', clean))
        return allowed, entry['crawl_delay']
    except Exception:
        return True, None


# ═══════════════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════════════
def _wait_global():
    elapsed = time.time() - _global_last_request[0]
    if elapsed < GLOBAL_QUERY_DELAY:
        time.sleep(GLOBAL_QUERY_DELAY - elapsed)
    _global_last_request[0] = time.time()


def _wait_domain(domain, crawl_delay=None):
    required = max(DEFAULT_DOMAIN_DELAY, crawl_delay or 0.0)
    last = _last_request_time.get(domain, 0.0)
    elapsed = time.time() - last
    if elapsed < required:
        time.sleep(required - elapsed)
    _last_request_time[domain] = time.time()


# ═══════════════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════════════
def _parse_retry_after(header_val):
    if not header_val:
        return None
    header_val = header_val.strip()
    if header_val.isdigit():
        return float(header_val)
    try:
        dt = parsedate_to_datetime(header_val)
        delta = (dt - datetime.now(dt.tzinfo)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def http_get(url, stream=False, crawl_delay=None, max_retries=MAX_RETRIES):
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc

    _wait_global()
    _wait_domain(domain, crawl_delay)

    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(
                url,
                headers={
                    'User-Agent': USER_AGENT,
                    'Accept': 'text/html,application/xhtml+xml,'
                              'application/pdf,image/*,*/*;q=0.8',
                    'Accept-Language': 'en,hi;q=0.9',
                    'From': 'research@example.org',
                },
                timeout=HTTP_TIMEOUT,
                stream=stream,
                allow_redirects=True,
            )

            if resp.status_code in (401, 403):
                return resp, f"HTTP {resp.status_code} (access restricted)"

            if resp.status_code == 429:
                retry_after = resp.headers.get('Retry-After')
                wait = _parse_retry_after(retry_after) or (2 ** attempt * 2)
                wait = min(wait, 20)
                if attempt < max_retries:
                    print(f"  [429] Waiting {wait:.1f}s before retry...")
                    time.sleep(wait)
                    _last_request_time[domain] = time.time()
                    continue
                return resp, "HTTP 429 (rate limited)"

            if 500 <= resp.status_code < 600:
                if attempt < max_retries:
                    time.sleep(1.5 ** attempt)
                    continue
                return resp, f"HTTP {resp.status_code}"

            return resp, None

        except requests.exceptions.RequestException as e:
            if attempt == max_retries:
                return None, str(e)
            time.sleep(1.5 ** attempt)

    return None, "exhausted retries"


# ═══════════════════════════════════════════════════════════
# WEBSITE TERMS — meta robots + X-Robots-Tag
# ═══════════════════════════════════════════════════════════
def check_meta_robots(html_text):
    if not html_text:
        return True, None
    head = html_text[:5000]
    m = re.search(
        r'<meta[^>]+name\s*=\s*["\']robots["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        head, re.IGNORECASE
    )
    if not m:
        return True, None
    directives = m.group(1).lower()
    for b in ['noindex', 'nofollow', 'none', 'noarchive']:
        if b in directives:
            return False, f"meta robots='{b}'"
    return True, None


def check_x_robots_tag(headers):
    """
    Read X-Robots-Tag from HTTP response headers. Modern sites use this
    instead of (or in addition to) the <meta name="robots"> tag.
    Returns (allowed, reason).
    """
    if not headers:
        return True, None
    xrt = headers.get('X-Robots-Tag') or headers.get('x-robots-tag')
    if not xrt:
        return True, None
    directives = xrt.lower()
    for b in ['noindex', 'nofollow', 'none', 'noarchive']:
        if b in directives:
            return False, f"X-Robots-Tag='{b}'"
    return True, None


# ═══════════════════════════════════════════════════════════
# LICENCE
# ═══════════════════════════════════════════════════════════
def classify_licence(licence_str):
    if not licence_str or licence_str.lower() == 'unknown':
        return 'unknown'
    s = licence_str.lower()

    if any(k in s for k in ['cc0', 'public domain', 'pddl', 'no rights reserved']):
        return 'public_domain'
    if 'creativecommons.org/licenses/' in s or 'cc by' in s or 'cc-by' in s:
        m = re.search(r'licenses/([a-z\-]+)/(\d\.\d)', s)
        if m:
            return f"cc-{m.group(1)}-{m.group(2)}"
        return 'cc-other'
    if any(k in s for k in ['ogl', 'open government licence', 'government open data']):
        return 'government_open'
    if any(k in s for k in ['all rights reserved', 'proprietary', '©', 'copyright']):
        return 'proprietary'
    return 'unknown'


def licence_permits_processing(category):
    if category in ('public_domain', 'government_open'):
        return True
    if category.startswith('cc-'):
        return True
    if category == 'unknown':
        return True
    if category == 'proprietary':
        return False
    return False


# ═══════════════════════════════════════════════════════════
# CONTENT TYPE
# ═══════════════════════════════════════════════════════════
def content_type_from_url(url):
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith('.pdf'):                      return 'pdf'
    if any(path.endswith(e) for e in
           ['.jpg','.jpeg','.png','.gif','.bmp','.tiff','.tif','.webp']):
        return 'image'
    if any(path.endswith(e) for e in ['.mp4','.avi','.mov','.mkv']):
        return 'video'
    if any(path.endswith(e) for e in ['.doc','.docx','.txt','.rtf']):
        return 'document'
    return 'html'


def content_type_from_header(ct):
    ct = (ct or '').lower()
    if 'pdf' in ct:                  return 'pdf'
    if ct.startswith('image/'):      return 'image'
    if ct.startswith('video/'):      return 'video'
    if 'html' in ct or 'xml' in ct:  return 'html'
    if ct.startswith('text/'):       return 'document'
    return None


# ═══════════════════════════════════════════════════════════
# HTML PARSING
# ═══════════════════════════════════════════════════════════
# HTML elements that hold site menus, footers and sidebars, not the page's content.
_NON_CONTENT_TAGS = ('nav', 'footer', 'aside')


class _PdfLinkParser(HTMLParser):
    """Collect every <a href> whose URL path ends in .pdf, with its anchor text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._link = None
        self._text = []
        self._outside = dict.fromkeys(_NON_CONTENT_TAGS, 0)

    def handle_starttag(self, tag, attrs):
        if tag in self._outside:
            self._outside[tag] += 1
        if tag != 'a':
            return
        self._close_link()
        href = (dict(attrs).get('href') or '').strip()
        try:
            is_pdf = urllib.parse.urlparse(href).path.lower().endswith('.pdf')
        except ValueError:
            return
        if is_pdf:
            self._link = {'url': href, 'anchor_text': None,
                          'outside_content': any(self._outside.values())}

    def handle_data(self, data):
        if self._link is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if self._outside.get(tag):
            self._outside[tag] -= 1
        if tag == 'a':
            self._close_link()

    def _close_link(self):
        if self._link is not None:
            text = ' '.join(''.join(self._text).split())
            self._link['anchor_text'] = text or None
            self.links.append(self._link)
        self._link = None
        self._text = []

    def close(self):
        super().close()
        self._close_link()


def extract_pdf_links(html_text):
    """All PDF links in document order, duplicates included."""
    parser = _PdfLinkParser()
    try:
        parser.feed(html_text or '')
        parser.close()
    except Exception as e:
        print(f"[WARNING] PDF link parsing stopped early: {e}")
    return parser.links


def parse_html_metadata(html_text):
    out = {
        'title': None, 'author': None, 'pub_date': None,
        'licence': None, 'publisher': None,
        'linked_pdfs': [], 'linked_images': [],
    }
    if not html_text:
        return out
    head = html_text[:100000]

    def meta(names):
        for n in names:
            pat = (r'<meta[^>]+(?:name|property)\s*=\s*["\']' + re.escape(n) +
                   r'["\'][^>]*content\s*=\s*["\']([^"\']+)["\']')
            m = re.search(pat, head, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    out['title'] = meta(['og:title', 'twitter:title'])
    if not out['title']:
        m = re.search(r'<title[^>]*>(.*?)</title>', head, re.IGNORECASE | re.DOTALL)
        if m:
            out['title'] = m.group(1).strip()

    out['author'] = meta([
        'author', 'article:author', 'og:article:author',
        'twitter:creator', 'dc.creator', 'citation_author',
    ])
    out['publisher'] = meta(['og:site_name', 'application-name',
                              'publisher', 'dc.publisher'])

    date_raw = meta([
        'article:published_time', 'og:article:published_time',
        'datePublished', 'date', 'dc.date', 'dc.date.issued',
        'citation_publication_date', 'pubdate',
    ])
    if date_raw:
        try:
            out['pub_date'] = parsedate_to_datetime(date_raw).date().isoformat()
        except Exception:
            m = re.match(r'(\d{4}-\d{2}-\d{2})', date_raw)
            out['pub_date'] = m.group(1) if m else date_raw

    m = re.search(
        r'<link[^>]+rel\s*=\s*["\']license["\'][^>]+href\s*=\s*["\']([^"\']+)["\']',
        head, re.IGNORECASE
    )
    if m:
        out['licence'] = m.group(1)
    else:
        out['licence'] = meta(['license', 'dc.rights', 'copyright'])

    out['linked_pdfs'] = extract_pdf_links(html_text)

    image_links = []
    seen_images = set()
    for tag in re.findall(r'<img\b[^>]*>', html_text, re.IGNORECASE):
        attrs = dict(re.findall(
            r'([:\w-]+)\s*=\s*["\']([^"\']*)["\']', tag, re.IGNORECASE
        ))
        image_url = attrs.get('src', '').strip()
        if not image_url or image_url in seen_images:
            continue
        if not _looks_like_content_image(image_url):
            continue
        seen_images.add(image_url)
        image_links.append({
            'url': image_url,
            'alt': attrs.get('alt', '').strip(),
            'title': attrs.get('title', '').strip(),
        })
    out['linked_images'] = image_links

    return out


_IMG_DENYLIST = (
    'logo', 'icon', 'favicon', 'sprite', 'badge', 'button',
    'social', 'share', 'instagram', 'insta', 'facebook', 'twitter',
    'linkedin', 'youtube', 'whatsapp', 'telegram', 'pinterest',
    'weixin', 'wechat',
    'app-store', 'appstore', 'play-store', 'playstore', 'googleplay',
    'androidtv', 'android-tv', 'ios-app', 'app-ios',
    'partner', 'sponsor', 'advert', 'banner-ad',
    '-banner', 'banner-', 'side-banner', 'cover_bottom', '-cover-',
    'pendrive', 'promo',
    'placeholder', 'default', 'spacer', 'blank', 'pixel', 'tracking', '1x1',
    'avatar',
    'header', 'footer', 'menu', 'authorphoto', 'track-your-order',
    '5star', 'star.gif', 'shopify', '/products/', '/thumbnails/',
    'freepik', 'after-sales', 'after_sales', 'taro-pumps', 'wpparpadhe',
    'qrcode', 'qr-', '-qr', 'contact-us', 'contact_us',
    'outlook.webp', 'oli-subscription', 'download.jpg',
    '88x31', '128.png', '256.png', '512.png',
)


def _looks_like_content_image(img_url):
    low = img_url.lower()
    if '/vi/ID/' in low or '/vi/VIDEOID/' in low or '/vi/$' in low:
        return False
    if re.search(r'-\d{2,4}x\d{2,4}\.(?:jpe?g|png|webp|gif)$', low):
        return False
    return not any(bad in low for bad in _IMG_DENYLIST)


def _link_is_relevant(link_url, key_terms, metadata_text=None):
    """Check linked-resource relevance using metadata when explicitly supplied."""
    if not key_terms:
        return True
    if metadata_text == '':
        return False
    # Image URLs are often hashes or CDN IDs. Use alt/title metadata when
    # supplied; retain the URL only as a fallback for PDFs and old callers.
    candidate = metadata_text if metadata_text is not None else link_url
    low = urllib.parse.unquote(candidate).lower()
    alnum = re.sub(r'[^a-z0-9\u0900-\u097F]+', '', low)
    for t in key_terms:
        t_alnum = re.sub(r'[^a-z0-9\u0900-\u097F]+', '', t.lower())
        if t_alnum and t_alnum in alnum:
            return True
    return False


def _matched_key_terms(url, anchor_text, key_terms):
    """Key terms found in the decoded URL or anchor text. Information only."""
    return [
        term for term in (key_terms or [])
        if term and (
            _link_is_relevant(url, [term])
            or (anchor_text and _link_is_relevant(url, [term], anchor_text))
        )
    ]


def _new_pdf_funnel():
    funnel = {name: 0 for name in PDF_FUNNEL_COUNTERS}
    funnel['pdf_excluded_by_check'] = {}
    return funnel


def merge_pdf_funnels(first, second):
    """Add two {'run': counters, 'pages': [...]} funnels (multi-question runs)."""
    run = _new_pdf_funnel()
    pages = []
    for funnel in (first, second):
        if not funnel:
            continue
        for name in PDF_FUNNEL_COUNTERS:
            run[name] += funnel['run'][name]
        for status, count in funnel['run']['pdf_excluded_by_check'].items():
            run['pdf_excluded_by_check'][status] = (
                run['pdf_excluded_by_check'].get(status, 0) + count)
        pages.extend(funnel['pages'])
    return {'run': run, 'pages': pages}


# ═══════════════════════════════════════════════════════════
# DIRECT EXTRACTION vs OCR
# ═══════════════════════════════════════════════════════════
def pdf_has_text_layer(path, max_pages=None, min_chars=100):
    if not HAS_PYPDF or not path or not os.path.exists(path):
        return None, 0
    try:
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            total_chars = 0
            pages_to_check = len(reader.pages) if max_pages is None else min(max_pages, len(reader.pages))
            for i in range(pages_to_check):
                text = reader.pages[i].extract_text() or ''
                total_chars += len(text.strip())
                if total_chars >= min_chars:
                    return True, total_chars
            return (total_chars >= min_chars), total_chars
    except Exception:
        return None, 0


def inspect_pdf_text_pages(path, min_chars_per_page=50):
    """Inspect every PDF page and report pages that may need OCR."""
    if not HAS_PYPDF or not path or not os.path.exists(path):
        return None, []
    try:
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text_pages = []
            ocr_pages = []
            for index, page in enumerate(reader.pages, start=1):
                try:
                    text = page.extract_text() or ''
                except Exception:
                    text = ''
                if len(text.strip()) >= min_chars_per_page:
                    text_pages.append(index)
                else:
                    ocr_pages.append(index)
            return text_pages, ocr_pages
    except Exception:
        return None, []


def extract_html_text(html_text):
    if not html_text:
        return ''
    if HAS_BS4:
        soup = BeautifulSoup(html_text, 'html.parser')
        for element in soup(['script', 'style', 'noscript', 'header', 'footer', 'nav']):
            element.decompose()
        return re.sub(r'\s+', ' ', soup.get_text(' ', strip=True)).strip()
    text = re.sub(r'<[^>]+>', ' ', html_text)
    return re.sub(r'\s+', ' ', text).strip()


def decide_ocr(url, content_type, text, saved_path=None):
    if content_type == 'pdf':
        if saved_path:
            text_pages, ocr_pages = inspect_pdf_text_pages(saved_path)
            if text_pages is not None:
                if not ocr_pages:
                    return False, f"PDF text found on all {len(text_pages)} pages"
                if not text_pages:
                    return True, f"PDF appears scanned — text missing on all pages"
                return True, (
                    f"PDF is mixed: {len(text_pages)} text pages and "
                    f"{len(ocr_pages)} pages may need OCR"
                )
        return True, "PDF — text layer unknown, assume OCR needed"
    if content_type == 'image':
        return True, "Image file — requires OCR"
    if content_type == 'video':
        return True, "Video content — OCR of frames/thumbnails"
    if content_type == 'html':
        if saved_path:
            text_len = len((text or '').strip())
            if text_len > 0:
                return False, f"Downloaded HTML contains {text_len} chars of extracted text"
            return False, "Downloaded HTML has no extracted text; review embedded images, do not infer OCR from search snippet"
        return False, "HTML without a downloaded file cannot be classified by search snippet; no OCR inferred"
    text_len = len((text or '').strip())
    if text_len > 0:
        return False, f"Direct text extraction possible ({text_len} chars)"
    return False, "No downloaded text available; OCR not inferred"


# ═══════════════════════════════════════════════════════════
# DOWNLOAD
# ═══════════════════════════════════════════════════════════
def _query_slug(original_query, max_len=60):
    if not original_query:
        return "query"
    slug = original_query.lower().strip()
    slug = unicodedata.normalize('NFKC', slug)
    parts = []
    in_separator = False
    for character in slug:
        category = unicodedata.category(character)
        if category.startswith(('L', 'M', 'N')):
            parts.append(character)
            in_separator = False
        elif parts and not in_separator:
            parts.append('-')
            in_separator = True
    slug = ''.join(parts).strip('-')
    if not slug:
        return f"q-{abs(hash(original_query)) & 0xffffffff:08x}"
    return slug[:max_len].rstrip('-') or "query"


def safe_filename(url, ext):
    p = urllib.parse.urlparse(url)
    parts = [x for x in p.path.split('/') if x]
    if parts:
        base = parts[-1]
    else:
        base = (p.netloc or 'index').replace('.', '_')
    if not os.path.splitext(base)[1]:
        base = base + '.' + ext
    base = re.sub(r'[^A-Za-z0-9._-]', '_', base)[:80]
    stem = os.path.splitext(base)[0] or 'index'
    uid = f"{abs(hash(url)) & 0xffffffff:08x}"
    return f"{uid}_{stem}.{ext}"


def download_to_disk(url, fallback_ext='html', crawl_delay=None, query_slug=None,
                     download_dir=None):
    """
    Fetch a URL, save it to disk under downloads/<type>/<query_slug>/, and
    return a result dict. The HTTP response is guaranteed closed on every
    path — success, HTTP error, unsupported format, or write failure —
    via the enclosing try/finally.
    """
    resp, err = http_get(url, stream=True, crawl_delay=crawl_delay)
    if err:
        return {'success': False, 'path': None, 'status_code': None,
                'error': err, 'headers': {}, 'content_type': None}

    try:
        if resp.status_code >= 400:
            return {'success': False, 'path': None, 'status_code': resp.status_code,
                    'error': f'HTTP {resp.status_code}',
                    'headers': dict(resp.headers), 'content_type': None}

        ct_header = resp.headers.get('Content-Type', '')
        real_type = content_type_from_header(ct_header) or fallback_ext

        if real_type not in ALLOWED_CONTENT_TYPES:
            return {'success': False, 'path': None, 'status_code': resp.status_code,
                    'error': f'unsupported_format:{real_type}',
                    'headers': dict(resp.headers), 'content_type': real_type}

        ext = fallback_ext
        if real_type == 'pdf':
            ext = 'pdf'
        elif real_type == 'image':
            sub = ct_header.split('/')[1].split(';')[0].strip() if '/' in ct_header else 'img'
            ext = sub if sub.isalnum() else 'img'
        elif real_type == 'html':
            ext = 'html'

        root_dir = download_dir or DOWNLOAD_DIR
        subdir = os.path.join(root_dir, DOWNLOAD_SUBDIRS[real_type])
        if query_slug:
            subdir = os.path.join(subdir, query_slug)
        os.makedirs(subdir, exist_ok=True)

        filepath = os.path.join(subdir, safe_filename(url, ext))
        try:
            with open(filepath, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        except Exception as e:
            return {'success': False, 'path': None, 'status_code': resp.status_code,
                    'error': f'Write failed: {e}',
                    'headers': dict(resp.headers), 'content_type': real_type}

        return {'success': True, 'path': filepath, 'status_code': resp.status_code,
                'error': None, 'headers': dict(resp.headers), 'content_type': real_type}
    finally:
        try:
            resp.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# URL NORMALIZATION + DEDUP
# ═══════════════════════════════════════════════════════════
_WAYBACK_RE = re.compile(r'web\.archive\.org/web/\d+[a-z_]*/(https?://.+)$', re.IGNORECASE)

_STRIP_QUERY_PARAMS = frozenset({
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'fbclid', 'gclid', 'hl',
})


def _unwrap_wayback(url):
    m = _WAYBACK_RE.search(url)
    return m.group(1) if m else url


def normalize_url(url):
    """
    Canonical form of a URL for deduplication.

    Transforms applied:
      1. Wayback Machine wrapper unwrapped
      2. Fragment (#...) removed
      3. Tracking params (utm_*, fbclid, gclid, hl) stripped
      4. Query params sorted (so ?a=1&b=2 == ?b=2&a=1)
      5. Trailing slash removed (except for "/" itself)
      6. Leading "www." removed from host
      7. Scheme and host lowercased
      8. URL path PRESERVED with original case (many servers are
         case-sensitive; lowercasing paths would falsely merge
         distinct pages)
    """
    try:
        url = _unwrap_wayback(url.strip())
        p = urllib.parse.urlparse(url)
        p = p._replace(fragment='')
        qs = urllib.parse.parse_qsl(p.query)
        clean = [(k, v) for k, v in qs if k not in _STRIP_QUERY_PARAMS]
        clean.sort()
        p = p._replace(query=urllib.parse.urlencode(clean))
        path = p.path.rstrip('/') if p.path != '/' else '/'
        netloc = p.netloc.lower()
        if netloc.startswith('www.'):
            netloc = netloc[4:]
        p = p._replace(path=path, scheme=p.scheme.lower(), netloc=netloc)
        return p.geturl()
    except Exception:
        return url.strip()


def merge_duplicates(docs):
    """
    Collapse documents that normalize to the same URL.

    When two documents share a canonical URL, merge their provenance:
      * source_queries         — union of plain strings
      * source_query_details   — union of dicts, deduped by (query, kind)
      * origin_score           — recomputed as the MAX of all scores
                                 across the merged details

    The duplicate's saved file is deleted from disk unless it is the
    same file as the one being kept.
    """
    merged = {}
    for d in docs:
        key = normalize_url(d['url'])
        if key in merged:
            kept = merged[key]

            # Merge source_queries (strings)
            existing_strings = kept.get('source_queries', []) or []
            new_strings = d.get('source_queries', []) or []
            kept['source_queries'] = list(set(existing_strings + new_strings))

            # Merge source_query_details (dicts) — dedup by (query, kind)
            existing_details = list(kept.get('source_query_details', []) or [])
            new_details = d.get('source_query_details', []) or []
            seen = {(x.get('query'), x.get('kind')) for x in existing_details}
            for x in new_details:
                sig = (x.get('query'), x.get('kind'))
                if sig not in seen:
                    existing_details.append(x)
                    seen.add(sig)
            kept['source_query_details'] = existing_details

            # Recompute origin_score from the merged details
            scored = [x.get('score') for x in existing_details
                      if isinstance(x, dict) and x.get('score') is not None]
            kept['origin_score'] = max(scored) if scored else None

            print(f"  [DEDUP] {d['url'][:70]}...")

            # Delete the duplicate's saved file if it's not the one being kept
            dup_path = d.get('saved_path')
            kept_path = kept.get('saved_path')
            if dup_path and dup_path != kept_path and os.path.exists(dup_path):
                try:
                    os.remove(dup_path)
                except OSError:
                    pass
        else:
            merged[key] = d
    return list(merged.values())


# ═══════════════════════════════════════════════════════════
# TASK 1 INPUT HANDLING
# ═══════════════════════════════════════════════════════════
def load_task1_input(path):
    with open(path, encoding='utf-8-sig') as f:
        data = json.load(f)
    return normalize_task1_objects(data, source=str(path))


def normalize_task1_objects(data, source="request"):
    """Normalize compact/full Task 1 shapes for in-process and API callers."""
    objects = data if isinstance(data, list) else [data]
    for i, obj in enumerate(objects):
        if not isinstance(obj, dict):
            raise ValueError(
                f"Task 1 object #{i} in {source} must be a JSON object."
            )

        # Accept both Task 1's full response (`query`) and its compact API
        # contract (`text`), while keeping one canonical shape for retrieval.
        if 'search_queries' not in obj and 'queries' in obj:
            obj['search_queries'] = obj['queries']
        if not obj.get('original_query'):
            obj['original_query'] = obj.get('normalized_query', '')
        if not obj.get('detected_language'):
            obj['detected_language'] = obj.get('language', 'unknown')

        if 'search_queries' not in obj or not obj['search_queries']:
            raise ValueError(
                f"Task 1 object #{i} in {source} has no non-empty 'search_queries' "
                f"list. Got keys: {list(obj.keys())}")
        for j, sq in enumerate(obj['search_queries']):
            if not isinstance(sq, dict):
                raise ValueError(
                    f"Task 1 object #{i}, search_queries[{j}] in {source} "
                    "must be an object.")
            if not sq.get('query') and sq.get('text'):
                sq['query'] = sq['text']
            if not sq.get('query'):
                raise ValueError(
                    f"Task 1 object #{i}, search_queries[{j}] in {path} has no "
                    f"'query' or 'text' string.")
    return objects


_DEVANAGARI_RE = re.compile(r'[\u0900-\u097F]')


def _detect_script(text):
    return 'hi' if _DEVANAGARI_RE.search(text or '') else 'en'


def build_keyword_boost_query(key_terms):
    terms = [t for t in (key_terms or []) if t and t.strip()]
    if not terms:
        return None
    quoted = ' '.join(f'"{t.strip()}"' for t in terms)
    language = _detect_script(quoted)
    return {'query': quoted, 'language': language, 'kind': 'keyword_boost', 'score': None}


def search_and_collect(search_queries, top_k=TOP_K, download=True, query_index=None,
                       download_dir=None):
    t_start = time.time()

    task1_object = search_queries
    original_query = task1_object.get('original_query')

    _base_slug = _query_slug(original_query)
    query_slug = f"{query_index:02d}_{_base_slug}" if query_index else _base_slug
    print(f"[INFO] Download folder for this query: {query_slug}")

    normalized_query = task1_object.get('normalized_query')
    key_terms = task1_object.get('key_terms', [])
    detected_language = task1_object.get('detected_language')
    task1_schema_version = task1_object.get('schema_version')

    variants = [dict(sq) for sq in task1_object['search_queries']]
    boost = build_keyword_boost_query(key_terms)
    if boost:
        variants.append(boost)
        print(f"[INFO] Added keyword_boost variant from key_terms: {boost['query']}")

    url_to_origin = defaultdict(list)
    url_to_snippet = {}
    raw_count = 0

    for item in variants:
        q = item['query']
        lang = item.get('language') or 'en'
        kind = item.get('kind', 'translation')
        score = item.get('score')
        region = item.get('region', 'in-en')
        item['original_query'] = original_query
        print(f"\n{'='*60}\n[QUERY] {q}  (lang={lang}, kind={kind})\n{'='*60}")

        results, engine_name = search_web(q, region=region, max_results=top_k,
                                           api_key=BRAVE_API_KEY, search_lang=lang)
        item['engine_used'] = engine_name
        item['results_returned'] = len(results)
        item['ranked_urls'] = [
            {
                'rank': rank,
                'url': r.get('url', ''),
                'title': r.get('title', ''),
                'canonical_url': normalize_url(r.get('url', '')),
            }
            for rank, r in enumerate(results, start=1)
            if r.get('url')
        ]
        raw_count += len(results)

        for rank, r in enumerate(results, start=1):
            url = r.get('url', '')
            if not url:
                continue
            url_to_origin[url].append({
                'query': q,
                'kind': kind,
                'score': score,
                'language': lang,
                'search_rank': rank,
                'rank': rank,
            })
            if len(r.get('text', '')) > len(url_to_snippet.get(url, {}).get('text', '')):
                url_to_snippet[url] = r

        time.sleep(GLOBAL_QUERY_DELAY)

    unique_top_level_urls = len(set(normalize_url(u) for u in url_to_origin))
    print(f"\n[STATS] Raw: {raw_count} | Unique top-level URLs: {unique_top_level_urls}")

    documents = []
    processed_urls = set()

    def process_url(url, origin_queries, is_linked=False, parent_id=None):
        norm = normalize_url(url)
        if norm in processed_urls:
            return None
        processed_urls.add(norm)

        robots_ok, crawl_delay = check_robots_allowed(url)
        url_type = content_type_from_url(url)
        snippet = url_to_snippet.get(url, {}).get('text', '') if not is_linked else ''

        downloaded, saved_path, status_code, error = False, None, None, None
        http_headers, html_meta = {}, {}
        final_type = url_type
        meta_robots_ok = True
        meta_robots_reason = None
        too_small = False
        extracted_text = ''

        if download and robots_ok and url_type not in ALLOWED_CONTENT_TYPES:
            print(f"  [SKIP] {url_type:5s} {url[:60]}... (unsupported format)")
            final_type = url_type
        elif download and robots_ok:
            print(f"  [DL] {url_type:5s} {url[:65]}...")
            dl = download_to_disk(url, url_type,
                                  crawl_delay=crawl_delay,
                                  query_slug=query_slug,
                                  download_dir=download_dir)
            downloaded = dl['success']
            saved_path = dl['path']
            status_code = dl['status_code']
            error = dl['error']
            http_headers = dl['headers']
            if dl['content_type']:
                final_type = dl['content_type']

            if downloaded and final_type == 'image' and saved_path:
                try:
                    size = os.path.getsize(saved_path)
                except OSError:
                    size = 0
                if size < IMAGE_MIN_BYTES:
                    try:
                        os.remove(saved_path)
                    except OSError:
                        pass
                    saved_path = None
                    downloaded = False
                    too_small = True
                    print(f"  [SMALL] {url[:65]} — {size} bytes (< {IMAGE_MIN_BYTES})")

            if downloaded and final_type == 'html' and saved_path:
                try:
                    with open(saved_path, 'r', encoding='utf-8', errors='ignore') as f:
                        body = f.read()
                    meta_robots_ok, meta_robots_reason = check_meta_robots(body)
                    html_meta = parse_html_metadata(body)
                    extracted_text = extract_html_text(body)
                except Exception:
                    html_meta = {}

            if not meta_robots_ok and saved_path and os.path.exists(saved_path):
                try:
                    os.remove(saved_path)
                except Exception:
                    pass
                saved_path = None
                downloaded = False
                print(f"  [META] {url[:65]} — {meta_robots_reason}")
        elif not robots_ok:
            print(f"  [ROB] {url[:70]}")

        # X-Robots-Tag from HTTP response headers
        xrt_ok, xrt_reason = check_x_robots_tag(http_headers)
        if not xrt_ok:
            if saved_path and os.path.exists(saved_path):
                try:
                    os.remove(saved_path)
                except Exception:
                    pass
                saved_path = None
                downloaded = False
            meta_robots_ok = False
            meta_robots_reason = xrt_reason
            print(f"  [XRT] {url[:65]} — {xrt_reason}")

        title = html_meta.get('title') or (url_to_snippet.get(url, {}).get('title') if not is_linked else None)
        author = html_meta.get('author') or html_meta.get('publisher')
        pub_date = html_meta.get('pub_date')
        licence = html_meta.get('licence') or 'unknown'

        licence_category = classify_licence(licence)
        licence_ok = licence_permits_processing(licence_category)
        licence_uncertain = (licence_category == 'unknown')

        if not licence_ok and saved_path and os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except Exception:
                pass
            saved_path = None
            downloaded = False
            print(f"  [LIC] {url[:70]} — licence={licence_category}")

        # Source-type classification (domain-based, universal rules)
        _domain = urllib.parse.urlparse(url).netloc.lower()
        source_type = classify_source_type(_domain)
        lic_candidates = licence_candidates(source_type, licence_category)

        ocr_text = extracted_text if final_type == 'html' else snippet
        needs_ocr, ocr_reason = decide_ocr(url, final_type, ocr_text, saved_path=saved_path)
        ocr_pages = []
        if final_type == 'pdf' and saved_path:
            _, ocr_pages = inspect_pdf_text_pages(saved_path)
            ocr_pages = ocr_pages or []

        if not robots_ok:
            status = 'excluded_robots'
        elif not meta_robots_ok:
            status = 'excluded_meta_robots'
        elif not licence_ok:
            status = 'excluded_licence'
        elif final_type not in ALLOWED_CONTENT_TYPES:
            status = 'excluded_unsupported_format'
        elif too_small:
            status = 'excluded_too_small'
        elif download and not downloaded:
            status = 'fetch_failed'
        elif not download:
            status = 'snippet_only'
        else:
            status = 'success'

        doc_id = f"{abs(hash(norm)) & 0xffffffff:08x}"

        source_query_strings = [o['query'] for o in origin_queries]
        scored = [o['score'] for o in origin_queries if o.get('score') is not None]
        origin_language = origin_queries[0]['language'] if origin_queries else 'unknown'

        if final_type == 'pdf':
            has_text_field = None
        else:
            has_text_field = bool(snippet) and not needs_ocr

        http_ct = http_headers.get('Content-Type') or http_headers.get('content-type')

        doc = {
            'doc_id': doc_id,
            'url': url,
            'canonical_url': norm,
            'domain': urllib.parse.urlparse(url).netloc.lower(),
            'title': title,
            'author': author,
            'pub_date': pub_date,
            'source_queries': source_query_strings,
            'source_query_details': origin_queries,
            'origin_score': max(scored) if scored else None,
            'original_query': original_query,
            'query_slug': query_slug,
            'key_terms': key_terms,
            'content_type': final_type,
            'needs_ocr': needs_ocr,
            'extraction_status': status,
            'text': snippet if not needs_ocr else '',
            'saved_path': saved_path,
            'fetch_status_code': status_code,
            'robots_allowed': robots_ok,
            'meta_robots_allowed': meta_robots_ok,
            'licence': licence,
            'licence_category': licence_category,
            'licence_permits_processing': licence_ok,
            'licence_uncertain': licence_uncertain,
            'licence_candidates': lic_candidates,
            'source_type': source_type,
            'http_content_type': http_ct,
            'is_linked_resource': is_linked,
            'parent_doc_id': parent_id,
            'ocr_instructions': {
                'required': needs_ocr,
                'priority': 'high' if needs_ocr else 'low',
                'expected_scripts': ['devanagari', 'english'],
                'pages_needing_ocr': ocr_pages,
            },
            'source_metadata': {
                'language': origin_language,
                'detected_language': detected_language,
                'ocr_reason': ocr_reason if needs_ocr else None,
            },
            'provenance': {
                'collection_timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'search_source': 'multi-engine-cascade',
            },
            'content_metadata': {
                'size_bytes': (os.path.getsize(saved_path)
                               if saved_path and os.path.exists(saved_path)
                               else len(snippet.encode('utf-8'))),
                'has_text': has_text_field,
            },
            'error': error,
        }

        tag = ('[ROB]' if not robots_ok else
               '[META]' if not meta_robots_ok else
               '[LIC]' if not licence_ok else
               '[SMALL]' if too_small else
               '[ERR]' if status == 'fetch_failed' else
               '[OCR]' if needs_ocr else '[OK] ')
        print(f"  {tag} {url[:70]}")
        return doc, html_meta

    # Search results are processed on their own turn (keeping their search
    # provenance), so a link to one of them is not queued a second time.
    queued_urls = {normalize_url(u) for u in url_to_origin}
    linked_queued = 0
    pdf_pages = []

    def within_question_limit():
        return (MAX_LINKED_FILES_PER_QUESTION is None
                or linked_queued < MAX_LINKED_FILES_PER_QUESTION)

    def add_linked(abs_url, anchor_text, origin, parent_id):
        nonlocal linked_queued
        r = process_url(abs_url, origin, is_linked=True, parent_id=parent_id)
        if not r:
            return None
        linked_queued += 1
        doc = r[0]
        doc['matched_key_terms'] = _matched_key_terms(abs_url, anchor_text, key_terms)
        doc['link_anchor_text'] = anchor_text
        documents.append(doc)
        return doc

    def follow_pdf_links(page_url, links, origin, parent_id):
        counts = _new_pdf_funnel()
        counts['pdf_links_seen'] = len(links)

        candidates = {}
        for link in links:
            if link.get('outside_content'):
                counts['pdf_skipped_outside_content'] += 1
                continue
            try:
                abs_url = urllib.parse.urljoin(page_url, link['url'])
                if urllib.parse.urlparse(abs_url).scheme.lower() not in ('http', 'https'):
                    continue
            except ValueError:
                continue
            norm = normalize_url(abs_url)
            if norm in processed_urls or norm in queued_urls:
                continue
            entry = candidates.setdefault(norm, {'url': abs_url, 'anchor_text': None})
            entry['anchor_text'] = entry['anchor_text'] or link['anchor_text']
        counts['pdf_links_after_dedup'] = len(candidates)

        for position, link in enumerate(candidates.values()):
            page_full = (MAX_LINKED_PDFS_PER_PAGE is not None
                         and position >= MAX_LINKED_PDFS_PER_PAGE)
            if page_full or not within_question_limit():
                counts['pdf_skipped_by_cap'] += 1
                continue
            doc = add_linked(link['url'], link['anchor_text'], origin, parent_id)
            if not doc:
                continue
            status = doc['extraction_status']
            if status == 'success':
                counts['pdf_fetched_ok'] += 1
            elif status == 'fetch_failed':
                counts['pdf_fetch_failed'] += 1
            else:
                excluded = counts['pdf_excluded_by_check']
                excluded[status] = excluded.get(status, 0) + 1

        if counts['pdf_skipped_by_cap']:
            print(f"  [CAP] skipped {counts['pdf_skipped_by_cap']} PDF link(s) "
                  f"on {page_url[:65]}")
        if counts['pdf_links_seen']:
            print(f"  [PDFS] {page_url[:65]} — {counts}")
            pdf_pages.append({'page_url': page_url, 'parent_doc_id': parent_id, **counts})

    # One entry per canonical page: spellings such as with and without "www."
    # are fetched once, and every search that found either one is kept.
    pages = {}
    for url, origins in url_to_origin.items():
        _, known = pages.setdefault(normalize_url(url), (url, []))
        for origin in origins:
            if (origin['query'], origin['kind']) not in {(o['query'], o['kind']) for o in known}:
                known.append(origin)

    for url, origin in pages.values():
        result = process_url(url, origin)
        if not result:
            continue
        doc, html_meta = result
        documents.append(doc)

        if (download and html_meta
                and doc['robots_allowed']
                and doc['meta_robots_allowed']
                and doc['licence_permits_processing']):
            parent_id = doc['doc_id']

            follow_pdf_links(url, html_meta.get('linked_pdfs', []), origin, parent_id)

            images = html_meta.get('linked_images', [])
            if (MAX_LINKED_IMAGES_PER_PAGE is not None
                    and len(images) > MAX_LINKED_IMAGES_PER_PAGE):
                print(f"  [CAP] skipped {len(images) - MAX_LINKED_IMAGES_PER_PAGE} "
                      f"image link(s) on {url[:65]}")
                images = images[:MAX_LINKED_IMAGES_PER_PAGE]

            for position, image in enumerate(images):
                if isinstance(image, dict):
                    img_url = image.get('url', '')
                    image_metadata = ' '.join(
                        filter(None, (image.get('alt'), image.get('title')))
                    )
                else:
                    img_url = image
                    image_metadata = ''
                abs_url = urllib.parse.urljoin(url, img_url)
                if not image_metadata or not _link_is_relevant(
                        abs_url, key_terms, image_metadata):
                    print(f"  [IRREL] image {abs_url[:65]}")
                    continue
                if normalize_url(abs_url) in queued_urls:
                    continue
                if not within_question_limit():
                    print(f"  [CAP] question limit reached, skipped up to "
                          f"{len(images) - position} image link(s) on {url[:65]}")
                    break
                add_linked(abs_url, None, origin, parent_id)

    print("\n" + "="*60 + "\nMERGE DUPLICATES\n" + "="*60)
    documents = merge_duplicates(documents)

    stats = {
        'raw_count': raw_count,
        'unique_top_level_urls': unique_top_level_urls,
        'unique_top_level_url_strings': list(
            dict.fromkeys(normalize_url(url) for url in url_to_origin)
        ),
        'total_documents': len(documents),
        'ocr_count': sum(1 for d in documents if d['needs_ocr']),
        'direct_count': sum(1 for d in documents if not d['needs_ocr']),
        'robots_excluded': sum(1 for d in documents
                               if d['extraction_status'] == 'excluded_robots'),
        'meta_robots_excluded': sum(1 for d in documents
                                    if d['extraction_status'] == 'excluded_meta_robots'),
        'licence_excluded': sum(1 for d in documents
                                if d['extraction_status'] == 'excluded_licence'),
        'unsupported_format_excluded': sum(1 for d in documents
                                if d['extraction_status'] == 'excluded_unsupported_format'),
        'too_small_excluded': sum(1 for d in documents
                                  if d['extraction_status'] == 'excluded_too_small'),
        'download_success': sum(1 for d in documents if d['saved_path']),
        'download_failed': sum(1 for d in documents
                               if d['extraction_status'] == 'fetch_failed'),
        'linked_resources': sum(1 for d in documents if d['is_linked_resource']),
        'licence_uncertain': sum(1 for d in documents if d['licence_uncertain']),
        'variants_searched': variants,
        'original_query': original_query,
        'normalized_query': normalized_query,
        'query_slug': query_slug,
        'key_terms': key_terms,
        'detected_language': detected_language,
        'task1_schema_version': task1_schema_version,
        'collection_time_seconds': round(time.time() - t_start, 2),
    }
    funnel = merge_pdf_funnels(None, None)
    for page in pdf_pages:
        funnel = merge_pdf_funnels(funnel, {'run': page, 'pages': [page]})
    stats['linked_pdf_funnel'] = funnel
    return documents, stats


# ═══════════════════════════════════════════════════════════
# SAVE OUTPUT
# ═══════════════════════════════════════════════════════════
def save_output(documents, stats, top_k):
    ocr_docs = [
        d for d in documents
        if d['needs_ocr']
        and d.get('extraction_status') == 'success'
        and d.get('saved_path')
    ]
    text_docs = [
        d for d in documents
        if not d['needs_ocr']
        and d.get('extraction_status') == 'success'
        and d.get('saved_path')
    ]

    ctypes = defaultdict(int)
    lic_dist = defaultdict(int)
    lic_cat_dist = defaultdict(int)
    source_type_dist = defaultdict(int)
    lic_cand_dist = defaultdict(int)
    engine_counts = defaultdict(int)

    for d in documents:
        ctypes[d['content_type']] += 1
        lic_dist[d['licence']] += 1
        lic_cat_dist[d['licence_category']] += 1
        source_type_dist[d.get('source_type', 'other')] += 1
        for c in d.get('licence_candidates', []) or []:
            lic_cand_dist[c] += 1

    raw = stats['raw_count']
    uniq_top_level = stats['unique_top_level_urls']
    variants = stats.get('variants_searched', [])
    usable_unique = min(raw, uniq_top_level)
    dup_rate = round((raw - usable_unique) / raw, 3) if raw else 0.0

    for v in variants:
        engine_counts[v.get('engine_used', 'unknown')] += 1
    fallback_used = any(v.get('engine_used') != 'brave' for v in variants)

    search_stats = {}
    for q in variants:
        key = f"{q['query']}  [{q.get('kind', 'translation')}]"
        search_stats[key] = {
            'original_query': q.get('original_query', ''),
            'language': q['language'],
            'kind': q.get('kind', 'translation'),
            'score': q.get('score'),
            'engine_used': q.get('engine_used', 'unknown'),
            'results_returned': q.get('results_returned', 0),
            'top_k_requested': top_k,
            'ranked_urls': q.get('ranked_urls', []),
        }

    output = {
        'task3_input': {
            'schema_version': TASK2_SCHEMA_VERSION,
            'documents': documents,
            'metadata': {
                'original_query': stats.get('original_query'),
                'normalized_query': stats.get('normalized_query'),
                'query_slug': stats.get('query_slug'),
                'detected_language': stats.get('detected_language'),
                'key_terms': stats.get('key_terms'),
                'task1_schema_version': stats.get('task1_schema_version'),
                'linked_pdf_funnel': stats.get('linked_pdf_funnel'),

                'total_urls_found': raw,
                'unique_top_level_urls': uniq_top_level,
                'documents_collected': len(documents),
                'document_types': dict(ctypes),
                'collection_time': stats.get('collection_time_seconds', 0),
                'search_stats': search_stats,
                'engines_used': dict(engine_counts),
                'fallback_used': fallback_used,
                'brave_remaining_queries': None,
                'config': {
                    'top_k_per_query': top_k,
                    'max_documents_total': None,
                    'image_min_bytes': IMAGE_MIN_BYTES,
                },
                'retrieval_quality': {
                    'num_queries': len(variants),
                    'raw_result_count': raw,
                    'unique_url_count': uniq_top_level,
                    'url_duplicate_rate': dup_rate,
                    'content_type_coverage': dict(ctypes),
                    'ocr_needed_count': len(ocr_docs),
                    'direct_extraction_count': len(text_docs),
                    'robots_excluded_count': stats['robots_excluded'],
                    'meta_robots_excluded_count': stats['meta_robots_excluded'],
                    'licence_excluded_count': stats['licence_excluded'],
                    'too_small_excluded_count': stats['too_small_excluded'],
                    'fetch_failed_count': stats['download_failed'],
                    'linked_resource_count': stats['linked_resources'],
                    'licence_distribution': dict(lic_dist),
                    'licence_category_distribution': dict(lic_cat_dist),
                    'licence_uncertain_count': stats['licence_uncertain'],
                    'source_type_distribution': dict(source_type_dist),
                    'licence_candidates_distribution': dict(lic_cand_dist),
                },
            },
            'processing_requirements': {
                'ocr_required': [d['doc_id'] for d in ocr_docs if d['saved_path']],
                'text_extracted': [d['doc_id'] for d in text_docs],
            },
        }
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[SAVED] {OUTPUT_FILE}")
    print(f"[STATS] docs={len(documents)} "
          f"ocr_needed={len(ocr_docs)} "
          f"direct_extraction={len(text_docs)} "
          f"robots_excluded={stats.get('robots_excluded', 0)} "
          f"meta_robots_excluded={stats.get('meta_robots_excluded', 0)} "
          f"licence_excluded={stats.get('licence_excluded', 0)} "
          f"unsupported_format_excluded={stats.get('unsupported_format_excluded', 0)} "
          f"too_small_excluded={stats.get('too_small_excluded', 0)} "
          f"downloaded={stats.get('download_success', 0)} "
          f"linked={stats['linked_resources']} "
          f"engines={dict(engine_counts)} "
          f"source_types={dict(source_type_dist)} "
          f"time={stats.get('collection_time_seconds', 0)}s")
    if stats.get('linked_pdf_funnel'):
        print(f"[PDF FUNNEL] {stats['linked_pdf_funnel']['run']}")


# ═══════════════════════════════════════════════════════════
# DEMO
# ═══════════════════════════════════════════════════════════
if __name__ == '__main__':
    input_path = sys.argv[1] if len(sys.argv) > 1 else TASK1_INPUT_FILE

    print("="*60)
    print("TASK 2 — search_and_collect(task1_object, top_k)")
    print(f"Loading Task 1 input from: {input_path}")
    print("="*60)

    task1_objects = load_task1_input(input_path)

    all_docs = []
    combined_stats = None
    unique_top_level_urls = set()
    for i, task1_obj in enumerate(task1_objects):
        print(f"\n### Question {i+1}/{len(task1_objects)}: {task1_obj.get('original_query')}")
        docs, stats = search_and_collect(task1_obj, top_k=TOP_K,
                                         download=True, query_index=i + 1)
        all_docs.extend(docs)
        unique_top_level_urls.update(
            stats.get('unique_top_level_url_strings', [])
        )

        if combined_stats is None:
            combined_stats = stats
        else:
            for k in ('raw_count', 'total_documents', 'ocr_count', 'direct_count',
                      'robots_excluded', 'meta_robots_excluded', 'licence_excluded',
                      'unsupported_format_excluded', 'too_small_excluded',
                      'download_success', 'download_failed', 'linked_resources',
                      'licence_uncertain', 'collection_time_seconds'):
                combined_stats[k] = combined_stats.get(k, 0) + stats.get(k, 0)
            combined_stats['variants_searched'] = (
                combined_stats.get('variants_searched', []) + stats.get('variants_searched', []))
            combined_stats['linked_pdf_funnel'] = merge_pdf_funnels(
                combined_stats.get('linked_pdf_funnel'), stats.get('linked_pdf_funnel'))
            for k in ('original_query', 'normalized_query', 'query_slug', 'key_terms',
                      'detected_language', 'task1_schema_version'):
                existing = combined_stats.get(k)
                if isinstance(existing, list):
                    existing = list(existing)
                else:
                    existing = [existing]
                existing.append(stats.get(k))
                combined_stats[k] = existing

    if len(task1_objects) > 1:
        all_docs = merge_duplicates(all_docs)
        combined_stats['unique_top_level_url_strings'] = sorted(unique_top_level_urls)
        combined_stats['unique_top_level_urls'] = len(unique_top_level_urls)
        combined_stats['total_documents'] = len(all_docs)

    save_output(all_docs, combined_stats, TOP_K)
    print("\nDone.")

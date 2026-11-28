"""
Offline controlled testbed for Task 2.

Starts five local HTTP "sites" with known ground truth (robots rules, noindex,
X-Robots-Tag, proprietary licence, 403, 404, 429, 500, scanned/mixed/text PDFs,
content images, tiny images, logos, mislabelled content types, a video link),
mocks the search engine so results point at those sites, runs the unmodified
main.search_and_collect() + save_output(), and scores every decision.

"""
import io, json, os, random, sys, threading, time, shutil
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T2 = os.path.abspath(sys.argv[1]); OUT = os.path.abspath(sys.argv[2])
os.makedirs(OUT, exist_ok=True)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"; os.environ["no_proxy"] = "127.0.0.1,localhost"
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)
os.environ.pop("BRAVE_API_KEY", None)
WORK = os.path.join(OUT, "work"); shutil.rmtree(WORK, ignore_errors=True); os.makedirs(WORK)
os.chdir(WORK)
sys.path.insert(0, T2)

from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw
import PyPDF2

def text_pdf(pages):
    b = io.BytesIO(); c = canvas.Canvas(b)
    for i in range(pages):
        y = 780
        for line in range(18):
            c.drawString(60, y, f"Green Revolution report page {i+1}: high-yielding varieties, irrigation and fertiliser use, line {line}.")
            y -= 20
        c.showPage()
    c.save(); return b.getvalue()

def scan_pdf(pages):
    imgs = []
    for i in range(pages):
        im = Image.new("RGB", (850, 1100), "white"); d = ImageDraw.Draw(im)
        for line in range(30):
            d.text((60, 60 + line * 32), f"Scanned page {i+1} line {line}: wheat, rice, Punjab, 1966-1970", fill="black")
        imgs.append(im)
    b = io.BytesIO(); imgs[0].save(b, "PDF", save_all=True, append_images=imgs[1:], resolution=100); return b.getvalue()

def mixed_pdf():
    w = PyPDF2.PdfWriter()
    for blob in (text_pdf(2), scan_pdf(1)):
        for p in PyPDF2.PdfReader(io.BytesIO(blob)).pages:
            w.add_page(p)
    b = io.BytesIO(); w.write(b); return b.getvalue()

def noisy_jpeg(w=320, h=240, seed=1):
    rnd = random.Random(seed); im = Image.new("RGB", (w, h))
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(w * h)])
    b = io.BytesIO(); im.save(b, "JPEG", quality=90); return b.getvalue()

def tiny_png():
    b = io.BytesIO(); Image.new("RGB", (16, 16), "green").save(b, "PNG"); return b.getvalue()

BODY = "<p>" + ("The Green Revolution in India introduced high-yielding varieties of wheat and rice. " * 12) + "</p>"

def page(title, extra_head="", extra_body=""):
    return (f"<html><head><title>{title}</title>{extra_head}</head><body><header>Site header</header>"
            f"<nav>menu</nav><h1>{title}</h1>{BODY}{extra_body}<footer>footer</footer></body></html>").encode()

PDF_TEXT, PDF_SCAN, PDF_MIXED, PDF_SCAN_B = text_pdf(3), scan_pdf(2), mixed_pdf(), scan_pdf(1)
JPG_A, JPG_B, PNG_TINY = noisy_jpeg(seed=1), noisy_jpeg(seed=2), tiny_png()

A_MAIN = page("Green Revolution in India - Encyclopedia",
              '<link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">',
              '<a href="/files/green-revolution-report.pdf">report</a>'
              '<a href="/files/green-revolution-scan.pdf">scan</a>'
              '<a href="/files/green-revolution-mixed.pdf">mixed</a>'
              '<a href="/files/annual-budget-2019.pdf">budget</a>'
              '<img src="/media/9f2c8d.jpg" alt="Green Revolution farmers in Punjab">'
              '<img src="/media/a1b2.png" alt="Green Revolution wheat icon-sized">'
              '<img src="/static/logo.png" alt="Green Revolution logo">'
              '<img src="/media/77ee.jpg">'
              '<img src="/media/c3d4.jpg" alt="Campus map">')

ROUTES = {  
    8101: {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /\n", {}),
        "/wiki/Green_Revolution_in_India": (200, "text/html; charset=utf-8", A_MAIN, {}),
        "/wiki/Norman_Borlaug": (200, "text/html", page("Norman Borlaug"), {}),
        "/files/green-revolution-report.pdf": (200, "application/pdf", PDF_TEXT, {}),
        "/files/green-revolution-scan.pdf": (200, "application/pdf", PDF_SCAN, {}),
        "/files/green-revolution-mixed.pdf": (200, "application/pdf", PDF_MIXED, {}),
        "/files/annual-budget-2019.pdf": (200, "application/pdf", PDF_TEXT, {}),
        "/media/9f2c8d.jpg": (200, "image/jpeg", JPG_A, {}),
        "/media/a1b2.png": (200, "image/png", PNG_TINY, {}),
    },
    8102: {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /private/\nCrawl-delay: 2\n", {}),
        "/schemes/green-revolution": (200, "text/html", page("Scheme page: Green Revolution"), {}),
        "/private/minutes": (200, "text/html", page("Private minutes"), {}),
        "/docs/hyv-seeds.pdf": (200, "application/pdf", PDF_SCAN_B, {}),
    },
    8103: {},  
    8104: {
        "/robots.txt": (404, "text/plain", b"", {}),
        "/missing": (404, "text/html", b"nope", {}),
    },
    8105: {
        "/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /\n", {}),
        "/noindex": (200, "text/html", page("No index", '<meta name="robots" content="noindex, follow">'), {}),
        "/xrt": (200, "text/html", page("Header noindex"), {"X-Robots-Tag": "noindex"}),
        "/copyright": (200, "text/html", page("Copyrighted", '<meta name="copyright" content="All rights reserved">'), {}),
        "/article.html": (200, "application/pdf", PDF_TEXT, {}),
        "/photo-of-board": (200, "image/jpeg", JPG_B, {}),
        "/js-app": (200, "text/html", b"<html><head><title>App</title><script>render()</script></head><body><div id='root'></div></body></html>", {}),
        "/hindi-page": (200, "text/html; charset=utf-8", page("हरित क्रांति - भारत"), {}),
    },
}
HITS = Counter(); STATE = Counter()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        port = self.server.server_address[1]; path = self.path.split("?")[0]
        HITS[(port, path)] += 1
        if port == 8103:
            return self._send(403, "text/html", b"Forbidden", {})
        if port == 8104 and path == "/flaky-500":
            STATE["f"] += 1
            if STATE["f"] == 1: return self._send(500, "text/html", b"err", {})
            return self._send(200, "text/html", page("Recovered after a 500"), {})
        if port == 8104 and path == "/rate-limited":
            STATE["r"] += 1
            if STATE["r"] == 1: return self._send(429, "text/html", b"slow down", {"Retry-After": "1"})
            return self._send(200, "text/html", page("Recovered after a 429"), {})
        if port == 8104 and path == "/always-500":
            return self._send(503, "text/html", b"down", {})
        r = ROUTES.get(port, {}).get(path)
        if not r: return self._send(404, "text/html", b"not found", {})
        self._send(*r)
    def _send(self, code, ct, body, extra):
        self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body)))
        for k, v in extra.items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

servers = []
for p in ROUTES:
    s = ThreadingHTTPServer(("127.0.0.1", p), H); servers.append(s)
    threading.Thread(target=s.serve_forever, daemon=True).start()

A, B, C, D, E = (f"http://127.0.0.1:{p}" for p in (8101, 8102, 8103, 8104, 8105))


SEARCH = {
    "What is the Green Revolution in India?": [
        A + "/wiki/Green_Revolution_in_India", B + "/schemes/green-revolution", C + "/blocked-article",
        D + "/flaky-500", E + "/noindex", E + "/article.html", A + "/wiki/Norman_Borlaug",
        E + "/lecture.mp4", D + "/missing", E + "/js-app"],
    "भारत में हरित क्रांति क्या है?": [
        E + "/hindi-page", A + "/wiki/Green_Revolution_in_India?utm_source=x", B + "/private/minutes",
        E + "/xrt", B + "/docs/hyv-seeds.pdf", D + "/rate-limited", C + "/another-blocked"],
    "Green Revolution India history": [
        A + "/wiki/Green_Revolution_in_India#History", A + "/wiki/Norman_Borlaug/", E + "/copyright",
        E + "/photo-of-board", D + "/always-500", B + "/schemes/green-revolution?b=2&a=1"],
    '"Green Revolution" "India"': [
        A + "/wiki/Green_Revolution_in_India", B + "/schemes/green-revolution?a=1&b=2", E + "/hindi-page"],
}


TRUTH = {
    A + "/wiki/Green_Revolution_in_India": ("success", False),
    A + "/wiki/Norman_Borlaug": ("success", False),
    A + "/files/green-revolution-report.pdf": ("success", False),
    A + "/files/green-revolution-scan.pdf": ("success", True),
    A + "/files/green-revolution-mixed.pdf": ("success", True),
    A + "/media/9f2c8d.jpg": ("success", True),
    A + "/media/a1b2.png": ("excluded_too_small", None),
    B + "/schemes/green-revolution": ("success", False),
    B + "/schemes/green-revolution?a=1&b=2": ("success", False),
    B + "/private/minutes": ("excluded_robots", None),
    B + "/docs/hyv-seeds.pdf": ("success", True),
    C + "/blocked-article": ("fetch_failed", None),
    C + "/another-blocked": ("fetch_failed", None),
    D + "/flaky-500": ("success", False),
    D + "/rate-limited": ("success", False),
    D + "/always-500": ("fetch_failed", None),
    D + "/missing": ("fetch_failed", None),
    E + "/noindex": ("excluded_meta_robots", None),
    E + "/xrt": ("excluded_meta_robots", None),
    E + "/copyright": ("excluded_licence", None),
    E + "/article.html": ("success", False),
    E + "/photo-of-board": ("success", True),
    E + "/lecture.mp4": ("excluded_unsupported_format", None),
    E + "/js-app": ("success", False),
    E + "/hindi-page": ("success", False),
}
NOT_FOLLOWED = [A + "/files/annual-budget-2019.pdf", A + "/static/logo.png", A + "/media/77ee.jpg", A + "/media/c3d4.jpg"]

import main, search_engines
SNIPPET = {E + "/js-app": ""}

def fake_search(q, region="in-en", max_results=5, api_key=None, search_lang=None):
    res = [{"title": u.rsplit("/", 1)[-1], "url": u,
            "text": SNIPPET.get(u, "Snippet about the Green Revolution in India and its effects on farmers.")}
           for u in SEARCH[q][:max_results]]
    return res, "mock"
main.search_web = fake_search

TASK1 = {
    "original_query": "What is the Green Revolution in India?",
    "normalized_query": "What is the Green Revolution in India?",
    "detected_language": "english",
    "key_terms": ["Green Revolution", "India"],
    "search_queries": [
        {"query": "What is the Green Revolution in India?", "language": "en", "kind": "original", "score": 1.0},
        {"query": "भारत में हरित क्रांति क्या है?", "language": "hi", "kind": "translation", "score": 0.9},
        {"query": "Green Revolution India history", "language": "en", "kind": "paraphrase", "score": 0.9},
    ],
}


TIMES = defaultdict(float); COUNTS = Counter()
real_sleep = time.sleep
def timed(name, fn):
    def w(*a, **k):
        t = time.perf_counter()
        try: return fn(*a, **k)
        finally: TIMES[name] += time.perf_counter() - t; COUNTS[name] += 1
    return w

def install_timers():
    main.time.sleep = timed("politeness_and_backoff_waits", real_sleep)
    main.check_robots_allowed = timed("robots_check", main.check_robots_allowed)
    main.parse_html_metadata = timed("html_parsing", main.parse_html_metadata)
    main.extract_html_text = timed("html_parsing", main.extract_html_text)
    main.check_meta_robots = timed("html_parsing", main.check_meta_robots)
    main.inspect_pdf_text_pages = timed("ocr_triage", main.inspect_pdf_text_pages)
    main.merge_duplicates = timed("merge", main.merge_duplicates)
    main.requests.get = timed("network_transfer", main.requests.get)

def reset_state():
    main._robots_cache.clear(); main._last_request_time.clear(); main._global_last_request[0] = 0.0
    STATE.clear(); HITS.clear()

def run(download_dir, fault_rate=0.0, seed=0, scale_delays=1.0):
    reset_state(); TIMES.clear(); COUNTS.clear()
    main.GLOBAL_QUERY_DELAY = 1.0 * scale_delays; main.DEFAULT_DOMAIN_DELAY = 1.5 * scale_delays
    rnd = random.Random(seed); injected = Counter()
    base_get = main.requests.get
    if fault_rate:
        class FakeResp:
            def __init__(s, code): s.status_code = code; s.headers = {"Retry-After": "1"} if code == 429 else {}; s.text = ""
            def close(s): pass
            def iter_content(s, chunk_size=1): return iter(())
        def faulty(url, *a, **k):
            if rnd.random() < fault_rate:
                kind = rnd.choice(["timeout", "500", "503", "429"]); injected[kind] += 1
                if kind == "timeout":
                    import requests; raise requests.exceptions.ConnectTimeout("injected timeout")
                return FakeResp(int(kind))
            return base_get(url, *a, **k)
        main.requests.get = faulty
    crashed, err = False, None
    t0 = time.perf_counter()
    try:
        docs, stats = main.search_and_collect(json.loads(json.dumps(TASK1)), top_k=10, download=True,
                                              query_index=1, download_dir=download_dir)
        main.save_output(docs, stats, 10)
        out = json.load(open("task3_input.json", encoding="utf-8"))["task3_input"]
    except Exception as e:
        crashed, err, docs, out, stats = True, repr(e), [], None, {}
        print("TESTBED CRASH:", err)
    total = time.perf_counter() - t0
    main.requests.get = base_get
    return dict(docs=docs, out=out, stats=stats, crashed=crashed, err=err, total=total,
                injected=dict(injected), times=dict(TIMES), counts=dict(COUNTS))

def score(r):
    docs = r["docs"]; by = {d["canonical_url"]: d for d in docs}
    rows = []
    for url, (st, ocr) in TRUTH.items():
        d = by.get(url)
        got_st = d["extraction_status"] if d else "MISSING"
        got_ocr = d["needs_ocr"] if d else None
        rows.append(dict(url=url.replace("http://127.0.0.1:", "site:"), expected=st, got=got_st,
                         status_ok=got_st == st,
                         expected_ocr=ocr, got_ocr=got_ocr if st == "success" else None,
                         ocr_ok=(ocr is None) or (got_ocr == ocr),
                         file_kept=bool(d and d.get("saved_path") and os.path.exists(d["saved_path"]))))
    unexpected = [d["canonical_url"] for d in docs if d["canonical_url"] not in TRUTH]
    return rows, unexpected

if __name__ == "__main__" and "--faults-only" not in sys.argv:
    print("== baseline run (real politeness delays) ==")
    install_timers()
    base = run(os.path.join(WORK, "dl_base"))
    rows, unexpected = score(base)
    out = base["out"]; pr = out["processing_requirements"]
    succ = {d["doc_id"]: d for d in base["docs"] if d["extraction_status"] == "success"}
    orphan = [d["canonical_url"].replace("http://127.0.0.1:", "site:") for i, d in succ.items()
              if i not in pr["ocr_required"] and i not in pr["text_extracted"]]
    # compliance invariants
    excluded_with_file = [r["url"] for r in rows if r["expected"] != "success" and r["file_kept"]]
    success_without_file = [r["url"] for r in rows if r["got"] == "success" and not r["file_kept"]]
    robots_hits_private = HITS[(8102, "/private/minutes")]
    video_hits = HITS[(8105, "/lecture.mp4")]
    robots_fetches = {p: HITS[(p, "/robots.txt")] for p in (8101, 8102, 8104, 8105)}
    st_counts = Counter(d["extraction_status"] for d in base["docs"])
    ocr_tp = sum(1 for r in rows if r["expected_ocr"] is True and r["got_ocr"] is True)
    ocr_fn = sum(1 for r in rows if r["expected_ocr"] is True and r["got_ocr"] is False)
    ocr_fp = sum(1 for r in rows if r["expected_ocr"] is False and r["got_ocr"] is True)
    ocr_tn = sum(1 for r in rows if r["expected_ocr"] is False and r["got_ocr"] is False)
    mixed = next(d for d in base["docs"] if d["canonical_url"].endswith("mixed.pdf"))
    summary = dict(
        raw=base["stats"]["raw_count"], unique=base["stats"]["unique_top_level_urls"],
        documents=len(base["docs"]), linked=base["stats"]["linked_resources"],
        status_counts=dict(st_counts),
        status_correct=sum(r["status_ok"] for r in rows), status_total=len(rows),
        ocr=dict(tp=ocr_tp, fp=ocr_fp, fn=ocr_fn, tn=ocr_tn),
        mixed_pages_needing_ocr=mixed["ocr_instructions"]["pages_needing_ocr"],
        unexpected_docs=unexpected, not_followed_present=[u for u in NOT_FOLLOWED if u in {d['canonical_url'] for d in base['docs']}],
        excluded_with_file=excluded_with_file, success_without_file=success_without_file,
        requests_to_disallowed_path=robots_hits_private, requests_to_video=video_hits,
        robots_fetches_per_site=robots_fetches,
        ocr_required=len(pr["ocr_required"]), text_extracted=len(pr["text_extracted"]),
        orphaned_success_docs=orphan,
        dup_rate=out["metadata"]["retrieval_quality"]["url_duplicate_rate"],
        total_seconds=round(base["total"], 2),
        stage_seconds={k: round(v, 2) for k, v in base["times"].items()}, stage_calls=base["counts"],
        retried_hits={"flaky-500": HITS[(8104, "/flaky-500")], "rate-limited": HITS[(8104, "/rate-limited")],
                      "always-500": HITS[(8104, "/always-500")], "403-site": HITS[(8103, "/blocked-article")]},
    )
    json.dump(dict(summary=summary, rows=rows), open(os.path.join(OUT, "testbed_baseline.json"), "w"), indent=2, ensure_ascii=False)
    print(json.dumps(summary, indent=1, ensure_ascii=False))

if __name__ == "__main__":
    install_timers() if "--faults-only" in sys.argv else None
    print("== fault injection (delays scaled x0.1) ==")
    res = []
    clean = None
    for rate in (0.0, 0.10, 0.25, 0.50):
        trials = []
        for seed in range(3):
            r = run(os.path.join(WORK, f"dl_f{int(rate*100)}_{seed}"), fault_rate=rate, seed=seed + 11, scale_delays=0.1)
            rows_f, _ = score(r)
            success = sum(1 for d in r["docs"] if d["extraction_status"] == "success")
            trials.append(dict(success=success, failed=sum(1 for d in r["docs"] if d["extraction_status"] == "fetch_failed"),
                               docs=len(r["docs"]), crashed=r["crashed"], injected=sum(r["injected"].values()),
                               status_ok=sum(x["status_ok"] for x in rows_f), secs=round(r["total"], 1),
                               requests=r["counts"].get("network_transfer", 0),
                               mismatches=[(x["url"], x["expected"], x["got"]) for x in rows_f if not x["status_ok"]]))
        res.append(dict(rate=rate, trials=trials))
        print(rate, trials)
    json.dump(res, open(os.path.join(OUT, "testbed_faults.json"), "w"), indent=2)
    for s in servers: s.shutdown()
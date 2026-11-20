import json

from main import (
    _looks_like_content_image,
    _link_is_relevant,
    _query_slug,
    decide_ocr,
    extract_html_text,
    load_task1_input,
    parse_html_metadata,
    save_output,
)
from search_engines import search_brave
import search_engines


def test_brave_maps_roman_hindi_to_supported_language(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {"web": {"results": []}}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(search_engines.requests, "get", fake_get)

    search_brave(
        "Bharat mein bauddh dharm ka prasar kaise hua?",
        max_results=5,
        api_key="test-api-key",
        search_lang="roman_hindi",
    )

    assert captured["params"]["search_lang"] == "hi"


def test_devanagari_query_slug_is_readable():
    slug = _query_slug("भारतीय कृषि पर हरित क्रांति का क्या प्रभाव पड़ा?")

    assert "भारतीय" in slug
    assert "हरित" in slug
    assert "q-" not in slug


def test_html_ocr_never_inferred_from_search_snippet():
    needs_ocr, reason = decide_ocr(
        "https://example.org/article",
        "html",
        "",
        saved_path="/missing/file.html",
    )
    assert needs_ocr is False
    assert "search snippet" in reason


def test_html_downloaded_text_is_direct_extraction():
    html = "<html><body><script>bad()</script><p>Useful article text.</p></body></html>"
    text = extract_html_text(html)

    needs_ocr, reason = decide_ocr(
        "https://example.org/article",
        "html",
        text,
        saved_path="downloaded.html",
    )
    assert needs_ocr is False
    assert "extracted text" in reason


def test_hashed_image_uses_alt_and_title_for_relevance():
    image_url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/9f2c8d.jpg"

    assert _link_is_relevant(
        image_url, ["green revolution"], "Green Revolution in India"
    )
    assert not _link_is_relevant(image_url, ["green revolution"], "site logo")


def test_parse_html_metadata_preserves_image_alt_and_title():
    metadata = parse_html_metadata(
        '<img src="/media/9f2c8d" alt="Green Revolution farmers" '
        'title="Agriculture in India">'
    )

    assert metadata["linked_images"] == [{
        "url": "/media/9f2c8d",
        "alt": "Green Revolution farmers",
        "title": "Agriculture in India",
    }]


def test_parse_html_metadata_keeps_unlabelled_image_metadata_empty():
    metadata = parse_html_metadata('<img src="/media/9f2c8d">')

    assert metadata["linked_images"] == [{
        "url": "/media/9f2c8d",
        "alt": "",
        "title": "",
    }]


def test_image_without_metadata_is_not_rejected_for_hashed_url():
    image_url = "https://example.org/media/9f2c8d"

    assert not _link_is_relevant(image_url, ["green revolution"], "")


def test_common_site_assets_are_not_content_images():
    assert not _looks_like_content_image(
        "https://example.org/assets/header.jpg"
    )
    assert not _looks_like_content_image(
        "https://shop.example.org/products/thumbnails/item.jpg"
    )
    assert not _looks_like_content_image(
        "https://www.taropumps.com/media/5093/taro-pumps-after-sales-service-5.jpg"
    )
    assert not _looks_like_content_image(
        "https://example.org/assets/default_500.png"
    )
    assert not _looks_like_content_image(
        "https://example.org/_next/static/media/wpparpadhe.svg"
    )
    assert _looks_like_content_image(
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/9f2c8d.jpg"
    )


def test_loads_task1_compact_api_contract(tmp_path):
    payload = {
        "language": "english",
        "normalized_query": "Fundamental rights in the Indian Constitution",
        "search_queries": [
            {
                "text": "Fundamental rights in the Indian Constitution",
                "language": "en",
                "rank": 1,
            }
        ],
        "key_terms": ["Fundamental rights"],
    }
    input_path = tmp_path / "task1.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_task1_input(input_path)

    assert loaded[0]["original_query"] == payload["normalized_query"]
    assert loaded[0]["detected_language"] == payload["language"]
    assert loaded[0]["search_queries"][0]["query"] == payload["search_queries"][0]["text"]


def test_loads_task1_full_and_task2_query_aliases(tmp_path):
    payload = [
        {
            "original_query": "Ram kaise the?",
            "search_queries": [
                {"query": "Ram kaise the?", "language": "roman_hindi"}
            ],
        },
        {
            "normalized_query": "Green Revolution impact",
            "queries": [
                {"text": "Green Revolution impact", "language": "en", "rank": 1}
            ],
        },
    ]
    input_path = tmp_path / "task1.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_task1_input(input_path)

    assert loaded[0]["search_queries"][0]["query"] == "Ram kaise the?"
    assert loaded[1]["search_queries"][0]["query"] == "Green Revolution impact"


def test_loads_utf8_bom_file(tmp_path):
    input_path = tmp_path / "task1_bom.json"
    payload = {
        "language": "english",
        "normalized_query": "Savitribai Phule",
        "search_queries": [
            {"text": "Savitribai Phule", "language": "en", "rank": 1}
        ],
        "key_terms": ["Savitribai Phule"],
    }
    input_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8-sig",
    )

    loaded = load_task1_input(input_path)

    assert loaded[0]["search_queries"][0]["query"] == "Savitribai Phule"


def test_save_output_excludes_unusable_ocr_and_text_candidates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    documents = [
        {
            "doc_id": "ocr-good",
            "needs_ocr": True,
            "extraction_status": "success",
            "saved_path": str(tmp_path / "ocr.png"),
            "text": "",
            "content_type": "image",
            "licence": "unknown",
            "licence_category": "unknown",
            "source_type": "commercial",
            "licence_candidates": [],
        },
        {
            "doc_id": "ocr-failed",
            "needs_ocr": True,
            "extraction_status": "fetch_failed",
            "saved_path": None,
            "text": "",
            "content_type": "pdf",
            "licence": "unknown",
            "licence_category": "unknown",
            "source_type": "commercial",
            "licence_candidates": [],
        },
        {
            "doc_id": "text-good",
            "needs_ocr": False,
            "extraction_status": "success",
            "saved_path": str(tmp_path / "text.html"),
            "text": "usable text",
            "content_type": "html",
            "licence": "unknown",
            "licence_category": "unknown",
            "source_type": "commercial",
            "licence_candidates": [],
        },
    ]
    (tmp_path / "ocr.png").write_bytes(b"image")
    (tmp_path / "text.html").write_text("text", encoding="utf-8")

    stats = {
        "raw_count": 3,
        "unique_top_level_urls": 2,
        "variants_searched": [],
        "total_documents": 3,
        "ocr_count": 2,
        "direct_count": 1,
        "robots_excluded": 0,
        "meta_robots_excluded": 0,
        "licence_excluded": 0,
        "unsupported_format_excluded": 0,
        "too_small_excluded": 0,
        "download_failed": 1,
        "linked_resources": 0,
        "licence_uncertain": 3,
        "collection_time_seconds": 1,
    }

    save_output(documents, stats, top_k=10)

    output = json.loads(
        (tmp_path / "task3_input.json").read_text(encoding="utf-8")
    )["task3_input"]
    requirements = output["processing_requirements"]

    assert requirements["ocr_required"] == ["ocr-good"]
    assert requirements["text_extracted"] == ["text-good"]

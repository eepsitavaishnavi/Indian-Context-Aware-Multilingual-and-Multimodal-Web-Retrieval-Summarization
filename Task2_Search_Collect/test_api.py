from fastapi.testclient import TestClient

import api


client = TestClient(api.app)


def test_task2_api_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_task2_api_rejects_missing_search_queries():
    response = client.post(
        "/api/v1/task2/search-collect",
        json={
            "task1": {
                "original_query": "Test query",
                "normalized_query": "Test query",
            },
            "top_k": 10,
            "download": False,
        },
    )

    assert response.status_code == 422


def test_task2_api_search_collect_uses_callable(monkeypatch):
    captured = {}

    def fake_search_and_collect(task1_object, top_k=10, download=True,
                                query_index=None, download_dir=None):
        captured.update({
            "top_k": top_k,
            "download": download,
            "query_index": query_index,
            "download_dir": download_dir,
            "original_query": task1_object["original_query"],
        })
        return (
            [{
                "doc_id": "test-doc",
                "url": "https://example.org/article",
                "content_type": "html",
            }],
            {
                "raw_count": 1,
                "unique_top_level_urls": 1,
                "unique_top_level_url_strings": [
                    "https://example.org/article"
                ],
                "total_documents": 1,
                "ocr_count": 0,
                "direct_count": 1,
                "robots_excluded": 0,
                "meta_robots_excluded": 0,
                "licence_excluded": 0,
                "unsupported_format_excluded": 0,
                "too_small_excluded": 0,
                "download_success": 0,
                "download_failed": 0,
                "linked_resources": 0,
                "licence_uncertain": 1,
                "collection_time_seconds": 0.1,
                "variants_searched": [],
                "original_query": task1_object["original_query"],
                "normalized_query": task1_object["original_query"],
                "query_slug": "test-query",
                "key_terms": [],
                "detected_language": "english",
                "task1_schema_version": "0.3",
            },
        )

    monkeypatch.setattr(api, "search_and_collect", fake_search_and_collect)

    response = client.post(
        "/api/v1/task2/search-collect",
        json={
            "task1": {
                "original_query": "Test query",
                "normalized_query": "Test query",
                "detected_language": "english",
                "search_queries": [
                    {"query": "Test query", "language": "en"}
                ],
            },
            "top_k": 7,
            "download": False,
            "download_dir": "test-downloads",
        },
    )

    assert response.status_code == 200
    assert response.json()["documents"][0]["doc_id"] == "test-doc"
    assert captured["top_k"] == 7
    assert captured["download"] is False
    assert captured["download_dir"] == "test-downloads"

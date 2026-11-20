import pytest

from app import generate_queries, generate_queries_brief, to_task2_response


@pytest.fixture(autouse=True)
def isolate_backend(monkeypatch):
    monkeypatch.setenv("QUERY_BACKEND", "baseline")
    monkeypatch.setenv("TRANSLATION_PROVIDER", "rules")


@pytest.mark.parametrize(
    ("query", "expected_language"),
    [
        ("राम कैसे थे राम को क्यों भगवान मानते हैं लोग?", "hindi_devanagari"),
        ("Ram kaise the?", "roman_hindi"),
        ("Green Revolution impact on Indian farmers?", "english"),
    ],
)
def test_language_detection(query, expected_language):
    result = generate_queries(query, 8)
    assert result.detected_language == expected_language


def test_brief_output_shape():
    output = generate_queries_brief("Ram kaise the?", 8)
    assert set(output) == {
        "language",
        "normalized_query",
        "search_queries",
        "key_terms",
    }


def test_query_budget_is_validated_for_direct_callers():
    with pytest.raises(ValueError, match="between 2 and 10"):
        generate_queries("What is ISRO?", 1)


def test_task2_handoff_preserves_ranked_query_shape():
    result = to_task2_response(generate_queries("Green Revolution impact on Indian farmers?", 4))

    assert result.queries
    assert [query.rank for query in result.queries] == list(range(1, len(result.queries) + 1))
    assert all(query.text and query.language in {"hi", "en", "roman_hindi"} for query in result.queries)


def test_deduplication_ignores_case_whitespace_and_trailing_punctuation():
    result = generate_queries(
        "How did the Green Revolution affect small farmers in India?",
        4,
    )
    texts = [item.query.strip().casefold().rstrip("?.!।") for item in result.search_queries]

    assert len(texts) == len(set(texts))


def test_english_query_does_not_emit_fake_baseline_hindi():
    result = generate_queries(
        "What is the impact of the Green Revolution on Indian farmers?",
        4,
    )

    assert result.detected_language == "english"
    assert result.devanagari_query == ""
    assert [item.language for item in result.search_queries] == ["en"]


def test_baseline_roman_hindi_keeps_original_without_word_salad():
    result = generate_queries("Gandhi ne movement kyu shuru kiya?", 4)

    assert result.detected_language == "roman_hindi"
    assert result.devanagari_query == ""
    assert result.english_query == ""
    assert [item.query for item in result.search_queries] == [
        "Gandhi ne movement kyun shuru kiya?"
    ]


def test_key_terms_exclude_entity_component_words():
    result = generate_queries(
        "What is the impact of the Green Revolution on Indian farmers?",
        4,
    )
    lowered = {term.lower() for term in result.key_terms}

    assert "Green Revolution" in result.key_terms
    assert "green" not in lowered
    assert "revolution" not in lowered


def test_strong_roman_hindi_marker_is_not_misclassified_as_english():
    result = generate_queries("Ram kyu bhagwan mante hain?", 4)

    assert result.detected_language == "roman_hindi"


def test_ollama_failure_returns_safe_baseline(monkeypatch):
    monkeypatch.setenv("QUERY_BACKEND", "ollama")

    def fail(question):
        raise RuntimeError("Ollama did not return a valid query response.")

    monkeypatch.setattr("app.call_ollama", fail)

    result = generate_queries("Ram kaise the?", 4)

    assert result.search_queries
    assert result.search_queries[0].query == "Ram kaise the?"
    assert any("Ollama" in warning for warning in result.warnings)


def test_ollama_generates_english_to_hindi_variant(monkeypatch):
    monkeypatch.setenv("QUERY_BACKEND", "ollama")
    monkeypatch.setattr(
        "app.call_ollama",
        lambda question: (
            ["हरित क्रांति का भारतीय किसानों पर क्या प्रभाव पड़ा?"],
            ["What is the impact of the Green Revolution on Indian farmers?"],
            ["Green Revolution", "impact", "Indian farmers"],
        ),
    )

    result = generate_queries(
        "What is the impact of the Green Revolution on Indian farmers?",
        4,
    )

    languages = {item.language for item in result.search_queries}
    assert languages == {"en", "hi"}
    assert any(
        "हरित क्रांति" in item.query
        for item in result.search_queries
    )

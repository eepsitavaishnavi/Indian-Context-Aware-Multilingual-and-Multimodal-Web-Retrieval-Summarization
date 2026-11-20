"""Indian-context multilingual search query generator."""

from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


LanguageLabel = Literal["hindi_devanagari", "roman_hindi", "english", "mixed"]


class QueryRequest(BaseModel):
    user_query: str = Field(min_length=2, max_length=500)
    max_queries: int = Field(default=8, ge=2, le=10)


class Entity(BaseModel):
    text: str
    canonical_form: str
    type: str


class SearchQuery(BaseModel):
    query: str
    language: Literal["hi", "en", "roman_hindi", "mixed"]
    kind: str
    score: float
    intent_preserved: bool | None = None
    entities_preserved: bool | None = None


class QueryResponse(BaseModel):
    diagnostics: dict = Field(default_factory=dict)
    schema_version: str = "0.3"
    original_query: str
    detected_language: LanguageLabel
    normalized_query: str
    devanagari_query: str
    english_query: str
    intent: str
    named_entities: list[Entity]
    key_terms: list[str]
    constraints: dict = Field(default_factory=dict)
    search_queries: list[SearchQuery]
    ranking_mode: str = "strategy_priority"
    warnings: list[str] = Field(default_factory=list)


class Task2Query(BaseModel):
    text: str
    language: str
    rank: int


class Task2Response(BaseModel):
    schema_version: str = "1.0"
    original_query: str
    normalized_query: str
    detected_language: str
    intent: str
    queries: list[Task2Query]
    warnings: list[str] = Field(default_factory=list)


class OllamaQueryResponse(BaseModel):
    devanagari_queries: list[str] = Field(min_length=1, max_length=4)
    english_queries: list[str] = Field(min_length=1, max_length=4)
    key_terms: list[str] = Field(default_factory=list, max_length=10)


DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
LATIN_RE = re.compile(r"[A-Za-z]")

ROMAN_HINTS = {
    "aap", "aur", "bharat", "bhagwan", "hai", "hain", "ka", "ke", "ki",
    "ko", "kaise", "kya", "kyu", "kyun", "kyon", "mein", "nahi", "par",
    "se", "tha", "the", "kab", "kahan", "kaun", "hua", "log", "mante",
    "maante",
}
ENGLISH_HINTS = {
    "what", "why", "how", "when", "where", "who", "is", "are", "was",
    "were", "the", "of", "with", "from", "impact", "effect", "difference",
    "history", "green", "revolution", "indian", "farmers",
}

ROMAN_TO_DEVANAGARI = {
    "aur": "और", "bharat": "भारत", "bhagwan": "भगवान", "kaise": "कैसे",
    "ka": "का", "ke": "के", "ki": "की", "ko": "को", "kyu": "क्यों",
    "kyun": "क्यों", "kyon": "क्यों", "kya": "क्या", "hai": "है",
    "hain": "हैं", "mein": "में", "nahi": "नहीं", "par": "पर", "se": "से",
    "tha": "था", "the": "थे", "kab": "कब", "kahan": "कहाँ", "kaun": "कौन",
    "hua": "हुआ", "log": "लोग", "mante": "मानते", "maante": "मानते",
    "india": "भारत", "green": "हरित", "revolution": "क्रांति",
}

DEVANAGARI_TO_ENGLISH = {
    "हरित क्रांति": "Green Revolution", "भारत": "India", "भारतीय": "Indian",
    "भगवान": "God", "कैसे": "how", "क्यों": "why", "क्या": "what",
    "कब": "when", "कहाँ": "where", "कौन": "who", "लोग": "people",
    "मानते": "consider", "था": "was", "थे": "were", "हैं": "are",
    "है": "is", "में": "in", "पर": "on", "का": "of", "के": "of",
    "की": "of", "को": "", "से": "from",
}

ENTITY_LEXICON = {
    "permanent settlement": ("Permanent Settlement", "HISTORICAL_EVENT"),
    "स्थायी बंदोबस्त": ("Permanent Settlement", "HISTORICAL_EVENT"),
    "green revolution": ("Green Revolution", "HISTORICAL_EVENT"),
    "हरित क्रांति": ("Green Revolution", "HISTORICAL_EVENT"),
    "isro": ("ISRO", "ORGANIZATION"),
    "इसरो": ("ISRO", "ORGANIZATION"),
    "india": ("India", "LOCATION"),
    "bharat": ("India", "LOCATION"),
    "भारत": ("India", "LOCATION"),
    "gandhi": ("Mahatma Gandhi", "PERSON"),
    "गांधी": ("Mahatma Gandhi", "PERSON"),
}


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\u200b", "").replace("\ufeff", "")
    text = text.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    return re.sub(r"\s+", " ", text).strip()


def detect_language(text: str) -> LanguageLabel:
    devanagari = bool(DEVANAGARI_RE.search(text))
    latin = bool(LATIN_RE.search(text))
    if devanagari and latin:
        return "mixed"
    if devanagari:
        return "hindi_devanagari"
    tokens = set(re.findall(r"[A-Za-z]+", text.lower()))
    hindi_count = len(tokens & ROMAN_HINTS)
    english_count = len(tokens & ENGLISH_HINTS)
    strong_hindi = bool(tokens & {
        "kaise", "kese", "kyu", "kyun", "kyon", "kya", "kab",
        "kahan", "kaun", "bhagwan", "nhi", "nahin",
    })
    if hindi_count >= 2 or strong_hindi:
        return "mixed" if english_count >= 2 else "roman_hindi"
    return "english"


ROMAN_NORMALIZATION = {
    "q": "kyun", "kyu": "kyun", "kyon": "kyun", "kese": "kaise",
    "nhi": "nahi", "bhagwaan": "bhagwan", "me": "mein", "hn": "hain",
    "h": "hai", "mante": "maante",
}


def normalize_query(text: str, language: LanguageLabel) -> str:
    text = clean_text(text)
    if language not in {"roman_hindi", "mixed"}:
        return text

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.isupper():
            return word
        value = ROMAN_NORMALIZATION.get(word.lower(), word.lower())
        return value.capitalize() if word[0].isupper() else value

    return re.sub(r"[A-Za-z]+", replace, text)


def roman_to_devanagari(text: str) -> str:
    parts = re.findall(r"[A-Za-z]+|[^A-Za-z]+", text)
    return "".join(
        ROMAN_TO_DEVANAGARI.get(part.lower(), part) if part.isalpha() else part
        for part in parts
    )


def devanagari_to_english(text: str) -> str:
    result = text
    for hindi, english in sorted(DEVANAGARI_TO_ENGLISH.items(), key=lambda item: -len(item[0])):
        result = result.replace(hindi, english)
    return re.sub(r"\s+", " ", result).strip(" ।?")


def deduplication_key(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    text = re.sub(r"[?.!。।]+$", "", text)
    return text.casefold()


def extract_entities(text: str) -> list[Entity]:
    found: list[Entity] = []
    seen: set[str] = set()
    for surface, (canonical, kind) in ENTITY_LEXICON.items():
        if re.search(r"(?<!\w)" + re.escape(surface) + r"(?!\w)", text, re.I):
            if canonical not in seen:
                match = re.search(r"(?<!\w)" + re.escape(surface) + r"(?!\w)", text, re.I)
                found.append(Entity(text=match.group(0), canonical_form=canonical, type=kind))
                seen.add(canonical)
    for match in re.finditer(r"\b[A-Z]{2,10}\b", text):
        if match.group(0) not in seen:
            found.append(Entity(text=match.group(0), canonical_form=match.group(0), type="ORGANIZATION_OR_ACRONYM"))
            seen.add(match.group(0))
    return found


def detect_intent(text: str) -> str:
    low = text.lower()
    rules = [
        (("क्यों", "kyun", "kyu", "why"), "reason"),
        (("कैसे", "kaise", "how"), "procedure_or_explanation"),
        (("कब", "kab", "when"), "time"),
        (("कहाँ", "kahan", "where"), "location"),
        (("तुलना", "antar", "compare", "difference"), "comparison"),
        (("impact", "effect", "प्रभाव", "prabhav"), "impact"),
    ]
    for markers, intent in rules:
        if any(marker in low for marker in markers):
            return intent
    return "general_information"


def extract_constraints(text: str) -> dict:
    numbers = list(dict.fromkeys(re.findall(r"\d+(?:\.\d+)?", text)))
    return {
        "numbers": numbers,
        "negation": bool(re.search(r"\b(?:not|never|without|nahi|nahin)\b|नहीं|बिना", text, re.I)),
        "comparisons": bool(re.search(r"\b(?:compare|difference|between)\b|तुलना|अंतर", text, re.I)),
    }


def extract_key_terms(text: str, entities: list[Entity]) -> list[str]:
    terms = [entity.canonical_form for entity in entities]
    entity_words = {
        word.lower()
        for entity in entities
        for value in (entity.text, entity.canonical_form)
        for word in re.findall(r"[A-Za-z]+|[\u0900-\u097F]+", value)
    }
    stop = {
        "a", "an", "and", "are", "did", "do", "does", "effect", "into", "is",
        "of", "the", "what", "when", "why", "how", "in", "ka", "ke", "ki",
        "ko", "hai", "hain", "kya", "kyun", "और", "का", "के", "की", "को",
        "क्या", "क्यों", "है", "हैं", "में", "पर",
    }
    for token in re.findall(r"[A-Za-z]{3,}|[\u0900-\u097F]{2,}", text):
        if (
            token.lower() not in stop
            and token.lower() not in entity_words
            and token.lower() not in {term.lower() for term in terms}
        ):
            terms.append(token)
        if len(terms) >= 7:
            break
    return terms


def call_ollama(question: str) -> tuple[list[str], list[str], list[str]] | None:
    if os.getenv("QUERY_BACKEND", "baseline") != "ollama":
        return None
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        "messages": [
            {"role": "system", "content": (
                "Generate faithful Hindi and English web-search queries. "
                "Return only JSON matching the requested schema. Preserve the "
                "source entities, numbers, negation, and question intent."
            )},
            {"role": "user", "content": question},
        ],
        "stream": False,
        "format": OllamaQueryResponse.model_json_schema(),
        "options": {"temperature": 0.2},
    }
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = OllamaQueryResponse.model_validate_json(
            result["message"]["content"]
        )
        devanagari_queries = [
            text.strip() for text in content.devanagari_queries
            if text.strip()
        ]
        english_queries = [
            text.strip() for text in content.english_queries
            if text.strip()
        ]
        if not devanagari_queries or not english_queries:
            raise ValueError("Ollama returned no usable query lists.")
        return (
            devanagari_queries,
            english_queries,
            [term.strip() for term in content.key_terms if term.strip()],
        )
    except (
        OSError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
    ) as exc:
        raise RuntimeError(
            "Ollama did not return a valid query response; baseline generation was used."
        ) from exc


def generate_queries(user_query: str, max_queries: int = 8) -> QueryResponse:
    if not 2 <= max_queries <= 10:
        raise ValueError("max_queries must be between 2 and 10.")

    original = clean_text(user_query)
    if not any(character.isalpha() for character in original):
        raise ValueError("Enter a question containing words.")
    language = detect_language(original)
    normalized = normalize_query(original, language)
    entities = extract_entities(normalized)
    key_terms = extract_key_terms(normalized, entities)
    intent = detect_intent(normalized)

    devanagari = normalized if language == "hindi_devanagari" else ""
    english = normalized if language == "english" else ""
    devanagari_queries: list[str] = []
    english_queries: list[str] = []
    warnings: list[str] = []
    ollama_error: RuntimeError | None = None
    try:
        ollama = call_ollama(normalized)
    except RuntimeError as exc:
        ollama = None
        ollama_error = exc

    if ollama:
        devanagari_queries, english_queries, model_terms = ollama
        devanagari = devanagari_queries[0] if devanagari_queries else ""
        english = english_queries[0] if english_queries else ""
        if model_terms:
            key_terms = model_terms[:7]
    elif language == "roman_hindi":
        # Baseline Roman-Hindi conversion is intentionally not emitted. The
        # dictionary can produce grammatically incomplete word mixtures.
        warnings.append(
            "Baseline Roman-Hindi translation is disabled; original text is retained."
        )
    elif language == "mixed":
        warnings.append(
            "Baseline mixed-script translation is disabled; original text is retained."
        )
    elif language == "hindi_devanagari":
        english = devanagari_to_english(normalized)

    candidates: list[SearchQuery] = []
    if ollama:
        for index, text in enumerate(devanagari_queries):
            candidates.append(SearchQuery(
                query=text,
                language="hi",
                kind="translation" if index == 0 else "paraphrase",
                score=0.9,
            ))
        for index, text in enumerate(english_queries):
            candidates.append(SearchQuery(
                query=text,
                language="en",
                kind="translation" if index == 0 else "paraphrase",
                score=0.9,
            ))
    else:
        if devanagari:
            candidates.append(SearchQuery(
                query=devanagari,
                language="hi",
                kind="translation",
                score=0.9,
            ))
        if english:
            candidates.append(SearchQuery(
                query=english,
                language="en",
                kind="translation",
                score=0.9,
            ))
    if language in {"roman_hindi", "mixed"}:
        candidates.insert(0, SearchQuery(query=normalized, language="roman_hindi", kind="original", score=1.0))
    elif normalized:
        candidates.insert(0, SearchQuery(query=normalized, language="hi" if language == "hindi_devanagari" else "en", kind="original", score=1.0))

    seen: set[str] = set()
    selected: list[SearchQuery] = []
    for item in candidates:
        key = deduplication_key(item.query)
        if key and key not in seen:
            seen.add(key)
            selected.append(item)
        if len(selected) >= max_queries:
            break

    if ollama_error:
        warnings.append(str(ollama_error))
    elif not ollama:
        warnings.append("Baseline mode: translation is rule-based and not a verified semantic translation.")

    return QueryResponse(
        original_query=user_query,
        detected_language=language,
        normalized_query=normalized,
        devanagari_query=devanagari,
        english_query=english,
        intent=intent,
        named_entities=entities,
        key_terms=key_terms,
        constraints=extract_constraints(original),
        search_queries=selected,
        warnings=warnings,
    )


def generate_queries_brief(
    user_query: str,
    max_queries: int = 8,
    include_meta: bool = False,
) -> dict:
    result = generate_queries(user_query, max_queries)
    queries = [
        {"text": item.query, "language": item.language, "rank": rank}
        for rank, item in enumerate(result.search_queries, start=1)
    ]
    output = {
        "language": result.detected_language,
        "normalized_query": result.normalized_query,
        "search_queries": queries,
        "key_terms": result.key_terms,
    }
    if include_meta:
        output["meta"] = {
            "original_query": result.original_query,
            "intent": result.intent,
            "named_entities": [entity.canonical_form for entity in result.named_entities],
            "constraints": result.constraints,
            "warnings": result.warnings,
        }
    return output


def to_task2_response(result: QueryResponse) -> Task2Response:
    queries = [
        Task2Query(text=item.query, language=item.language, rank=index)
        for index, item in enumerate(result.search_queries, start=1)
    ]
    return Task2Response(
        original_query=result.original_query,
        normalized_query=result.normalized_query,
        detected_language=result.detected_language,
        intent=result.intent,
        queries=queries,
        warnings=result.warnings,
    )


app = FastAPI(
    title="Indian-Context Multilingual Query Generator",
    description="Task 1: understand Hindi, Roman Hindi, English and mixed queries.",
    version="0.3.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/generate-queries", response_model=QueryResponse)
def generate(request: QueryRequest) -> QueryResponse:
    try:
        return generate_queries(request.user_query, request.max_queries)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/task1/generate")
def task1_generate(request: QueryRequest, include_meta: bool = False) -> dict:
    try:
        return generate_queries_brief(request.user_query, request.max_queries, include_meta)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/task2/queries", response_model=Task2Response)
def task2_queries(request: QueryRequest) -> Task2Response:
    try:
        return to_task2_response(generate_queries(request.user_query, request.max_queries))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

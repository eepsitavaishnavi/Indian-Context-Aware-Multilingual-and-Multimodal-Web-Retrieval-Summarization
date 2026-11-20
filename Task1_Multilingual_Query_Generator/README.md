# Task 1: Multilingual Query Generator

Task 1 accepts a Hindi (Devanagari), Roman-Hindi, English, or mixed question
and returns normalized metadata plus ranked Hindi/English search queries for
Task 2.

## What Task 1 does now

1. Cleans Unicode text and normalizes common Roman-Hindi spellings.
2. Detects `hindi_devanagari`, `roman_hindi`, `english`, or `mixed`.
3. Extracts intent, key terms, constraints, and a small set of named entities.
4. Preserves original queries and creates additional candidates only through
   safe Devanagari/English mappings or a validated Ollama response.
5. Removes duplicate candidates and assigns sequential ranks.
6. Exposes a compact JSON contract for Task 2 and a detailed diagnostic
   response for debugging.

The default implementation is deterministic and rule based. When enabled,
the optional local Ollama backend can provide model-generated Hindi and
English queries. Ollama is never required to start the API.

Example Task 2 response:

```json
{
  "language": "roman_hindi",
  "normalized_query": "Ram kaise the?",
  "search_queries": [
  {"text": "Ram kaise the?", "language": "roman_hindi", "rank": 1}
  ],
  "key_terms": ["Ram", "kaise"]
}
```

## Changes from the earlier version

The earlier project version described a larger, model-first structure with
multiple handoff documents, separate `app/` modules, setup/evaluation scripts,
and an additional model dependency file. That structure was confusing and
several documented paths/endpoints no longer existed in the actual folder.

The project was consolidated and corrected as follows:

- Moved the active implementation boundary to the single, current
  [`app.py`](app.py).
- Kept the Streamlit demo and focused tests, while removing obsolete launcher,
  model-dependency, write-up, and multi-step handoff documents.
- Replaced the old documentation with this README and the concise
  [`TASK2_QUICKSTART.md`](TASK2_QUICKSTART.md).
- Standardized the documented server command to `uvicorn app:app`.
- Documented the real endpoints:
  `/health`, `/api/v1/task1/generate`, `/api/v1/task2/queries`, and
  `/generate-queries`.
- Added validation for direct Python callers so `max_queries` is always
  between 2 and 10, matching the API schema.
- Changed Ollama failures from silent fallback to an explicit warning while
  retaining a usable deterministic baseline.
- Added stronger Roman-Hindi detection with strong-script markers.
- Disabled unsafe baseline Roman-Hindi/mixed-script translation instead of
  returning grammatically incomplete word mixtures.
- Excluded entity component words from extra key terms.
- Added structured Ollama JSON validation for Hindi and English query lists.
- Added regressions for English baseline behavior, Roman-Hindi safety,
  key-term redundancy, strong Roman-Hindi markers, and Ollama failure fallback.
- Added tests for query-budget validation and sequential Task2 handoff ranks.
- Removed generated caches and temporary files from the project folder.

The earlier version's evaluation claims were not treated as measured results.
No accuracy, retrieval-gain, or human-quality number should be reported until
the evaluation data is actually run and reviewed.

## Files

- [`app.py`](app.py) - query logic, Pydantic models, FastAPI API, and Task 2 handoff.
- [`streamlit_app.py`](streamlit_app.py) - optional local demo.
- [`test_app.py`](test_app.py) - focused unit and handoff tests.
- [`TASK2_QUICKSTART.md`](TASK2_QUICKSTART.md) - the integration contract.

The `evaluation/` directory contains optional evaluation inputs/templates; it
is not required to run the API.

## Setup and run

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python -m uvicorn app:app --host 127.0.0.1 --port 8000
streamlit run streamlit_app.py
```

The default backend is deterministic rule-based generation. To use the
optional local Ollama backend:

```powershell
$env:QUERY_BACKEND = "ollama"
$env:OLLAMA_MODEL = "qwen3:4b"
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

If Ollama is unavailable or returns invalid data, the API explicitly reports
the fallback in `warnings` and uses the baseline generator.

## API

- `GET /health`
- `POST /api/v1/task1/generate` - compact Task 1 contract
- `POST /api/v1/task2/queries` - Task 2-shaped response
- `POST /generate-queries` - full diagnostics

Request:

```json
{"user_query": "Ram kaise the?", "max_queries": 4}
```

`max_queries` must be between 2 and 10 (default 8). Task 2 should iterate
`queries` by `rank`, search each `text`, deduplicate URLs, and retain the
original query for downstream reranking. Query `rank` and `score` are ordering
signals, not relevance probabilities.

## Known limitations

The deterministic baseline does not create invented Roman-Hindi or mixed-script
translations. Pure Devanagari and English inputs may still receive safe
rule-based cross-script variants. The entity lexicon is intentionally small,
and Ollama output requires human or retrieval evaluation for semantic
preservation.

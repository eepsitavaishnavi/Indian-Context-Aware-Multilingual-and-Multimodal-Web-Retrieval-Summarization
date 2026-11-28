# Task 2: On-the-Fly Web Search, Scraping and Multimodal Content Acquisition

This project implements **Task 2** of an Indian-context-aware, multilingual and multimodal web retrieval and summarization system. Task 2 receives search queries from Task 1, searches the live web, downloads permitted evidence, removes duplicate resources, records provenance and restrictions, and prepares a structured package for Task 3.

Task 2 routes images and PDF pages to OCR or direct text extraction. 

## Team Members

| Name | Roll Number |
|---|---:|
| Janapareddy Vidya Varshini | 230041013 |
| Korubilli Vaishnavi | 230041016 |
| Mullapudi Namaswi | 230041023 |

## Project Objective

Given a Hindi, Roman-Hindi, English or mixed-language question, Task 2:

- accept ranked multilingual query variants produced by Task 1;
- search the live web through Brave or a DuckDuckGo fallback;
- normalize and deduplicate URLs;
- check robots, metadata and licence restrictions;
- collect permitted HTML pages, PDFs and images;
- inspect downloaded content and classify it for direct extraction or OCR;
- retain failed and excluded URLs with reasons for traceability;
- preserve query, rank and source provenance; and
- generate `task3_input.json` for Task 3.

## Repository Layout

```text
E:\task2_search_collect\
├── requirements.txt
├── task1_input.json
├── task2_search_collect\
│   ├── main.py                    # Core search, download and acquisition pipeline
│   ├── search_engines.py          # Brave/DDGS search backends
│   ├── run_pipeline.py            # Direct Task 1 -> Task 2 runner
│   ├── api.py                     # FastAPI interface
│   ├── evaluate_relevance.py      # P@K and nDCG relevance evaluation
│   ├── relevance_labels.csv       # Preliminary title/URL relevance labels
|   |── testbed.py 
│   ├── tests\
│   │   ├── test_api.py                # FastAPI endpoint tests
│   │   └── test_task2_integration.py  # Loader, routing, Brave language and output tests
├── task3_input.json
└── downloads
```

## System Architecture

```text
User Question
      |
      v
Task 1: multilingual query understanding
      |
      | Query variants, language, key terms and ranks
      v
Task 2: search and multimodal acquisition
      |
      +-- search
      +-- URL canonicalization and deduplication
      +-- robots/meta-robots checks
      +-- licence and source classification
      +-- HTML/PDF/image download
      +-- PDF page inspection
      +-- OCR/direct-text routing
      +-- status, error and provenance recording
      |
      v
task3_input.json
      |
      v
Task 3: OCR, text cleaning and passage preparation
```

## Requirements

- Python 3.10 or newer
- Brave Search API key
- Optional Task 1 local Ollama setup

Create and activate a virtual environment:

```powershell
cd E:\task2_search_collect
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Search Configuration

Set an Brave API key for the live Brave backend:

```powershell
$env:BRAVE_API_KEY = "your-real-brave-api-key"
```

If Brave is absent or unavailable, Task 2 falls back to DuckDuckGo Search
through the available `ddgs` backends.

## End-to-End Run

```powershell
cd E:\task2_search_collect

python run_pipeline.py `
  --query "How did the Indian Constitution promote equality?" `
  --max-queries 4 `
  --top-k 15
```

Run multiple questions in one batch:

```powershell
python run_pipeline.py --query `
  "भगवान राम के जीवन से हमें क्या सीख मिलती है?" `
  "1857 ki kranti ke mukhya karan kya the?" `
  "How did the Indian Constitution promote equality?" `
  "भारतीय मानसून कृषि के लिए क्यों महत्वपूर्ण है?" `
  "What were the main achievements of the Indus Valley Civilization?"
```

### Pipeline Options

| Option | Default | Purpose |
|---|---:|---|
| `--query` | required | One or more user questions |
| `--max-queries` | 4 | Maximum Task 1 variants per question; accepted range is 2–10 |
| `--top-k` | 15 | Maximum search results for each query variant |
| `--download-dir` | `downloads` | Separate directory for downloaded files |

Example isolated run:

```powershell
python run_pipeline.py `
  --query "What is the importance of the monsoon in Indian agriculture?" `
  --top-k 15 `
  --download-dir downloads_monsoon
```

The pipeline writes:

- `task1_input.json`
- `task3_input.json`
- downloaded HTML, PDF and image files under the selected download directory

## Manual Task 1 Handoff

Task 2 can process an existing Task 1 JSON package:

```powershell
cd E:\task2_search_collect
python main.py task1_input.json
```

The loader accepts full and compact Task 1 objects, including:

- full `search_queries[].query` objects;
- compact `search_queries[].text` objects;
- older `queries[].text` objects;
- a single object or a JSON array.

## REST API

Start the API:

```powershell
cd E:\task2_search_collect
python -m uvicorn api:app --host 127.0.0.1 --port 8001
```

Check availability:

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/health"
```

Available endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/task2/search-collect` | Return collected documents and statistics |
| `POST` | `/api/v1/task2/task3-input` | Write and return `task3_input.json` |

Example API request:

```powershell
$body = @{
  task1 = @{
    original_query = "How did the Green Revolution affect Indian farmers?"
    normalized_query = "How did the Green Revolution affect Indian farmers?"
    detected_language = "english"
    search_queries = @(
      @{ query = "How did the Green Revolution affect Indian farmers?"; language = "en" }
    )
  }
  top_k = 15
  download = $true
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/v1/task2/search-collect" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

## Acquisition Pipeline

For every query variant, Task 2:

1. obtains ranked search results;
2. records rank, engine, language and source-query provenance;
3. canonicalizes URLs and removes duplicate canonical pages;
4. checks `robots.txt`, meta robots and `X-Robots-Tag`;
5. identifies licence uncertainty and source type;
6. downloads permitted HTML, PDF and image content;
7. applies minimum-size filtering to images;
8. extracts visible HTML text and PDF text;
9. identifies direct-text and OCR-required content;
10. records failures and exclusions instead of silently dropping them.

Every `.pdf` link on a collected HTML page that passed the compliance checks is
followed (except links inside `<nav>`, `<footer>` and `<aside>`). Images are filtered by alt/title key terms. The limits are
`MAX_LINKED_PDFS_PER_PAGE`, `MAX_LINKED_IMAGES_PER_PAGE` and
`MAX_LINKED_FILES_PER_QUESTION` in `main.py`. Each linked document records
`matched_key_terms` and `link_anchor_text` for later stages, and
`metadata.linked_pdf_funnel` counts PDF links per page and per run.

Linked resources are checked using their actual parent page and content
metadata. Hash-named or extensionless images are not rejected merely because
their URL does not contain a descriptive name.

## OCR and Direct-Text Routing

Task 2 performs routing only:

| Content | Routing decision |
|---|---|
| HTML | Direct extraction when visible text is available |
| Text-based PDF | Direct extraction |
| Scanned PDF | OCR required |
| Mixed PDF | OCR required for the identified text-deficient pages |
| Image | OCR required |
| Failed or too-small file | Excluded from usable processing lists |

The generated package contains:

- `ocr_required`: usable document IDs requiring OCR routing;
- `text_extracted`: usable document IDs available for direct text processing;
- exact PDF page hints for mixed documents;
- OCR priority and reason metadata.

## Task 3 Input

`task3_input.json` contains:

- `task3_input.documents`: raw document records;
- `task3_input.metadata`: search and collection statistics;
- `task3_input.processing_requirements`: OCR and direct-text document lists;
- canonical URLs and source-query provenance;
- download and extraction status;
- saved file paths;
- robots, licence and source metadata;
- OCR instructions and page hints.

## Relevance Evaluation

The relevance evaluator prepares and scores title/URL labels:

```powershell
cd E:\task2_search_collect

python evaluate_relevance.py prepare --input task3_input.json --labels relevance_labels.csv
python evaluate_relevance.py score --labels relevance_labels.csv
```

It computes:

- P@1;
- P@3;
- P@5;
- nDCG@5; and
- useful-source rate.

Labels `1` and `2` are relevant. Label `0` is not relevant. Duplicate canonical
URLs are removed before ranking calculations.

The reported P@K, nDCG@5 and useful-source values are based on
preliminary title and URL relevance labels. 

## Controlled Testbed

Run the local controlled testbed:

```powershell
cd E:\task2_search_collect
python testbed.py
```

The testbed uses local HTTP servers and known ground truth to verify:

- status decisions and exclusions;
- OCR/direct extraction routing;
- mixed-PDF page detection;
- download and handoff reconciliation;
- robots.txt compliance;
- fault tolerance and network timing.

Results are stored under `testbed_results`.

## Testing

Task 2 has two pytest test files in `task2_search_collect\tests`:

| File | Tests | Coverage |
|---|---:|---|
| `test_api.py` | 3 | `/health`, rejection of a request with no search queries (HTTP 422), and `/api/v1/task2/search-collect` parameter pass-through using a mocked search-and-collect function |
| `test_task2_integration.py` | 8 | Brave language mapping (Roman-Hindi to `hi`), readable Devanagari query slugs, HTML OCR routing, Task 1 loader contracts (compact, full, `queries` alias and UTF-8 BOM) and the OCR/direct-text lists in `task3_input.json` |

The tests mock network calls and use temporary directories, so they need neither
a Brave API key nor internet access. The API tests use FastAPI's `TestClient`,
which needs `httpx`; install `pytest` and `httpx` if they are not already in
your environment. The tests import `api`, `main` and `search_engines` directly,
so run them from `task2_search_collect`.

Run Task 2 and integration tests:

```powershell
cd E:\task2_search_collect
python -m pytest -q
```

Run one test file at a time:

```powershell
python -m pytest -q tests\test_api.py
python -m pytest -q tests\test_task2_integration.py
```

Compile the principal Python modules:

```powershell
python -m py_compile main.py run_pipeline.py search_engines.py api.py evaluate_relevance.py
```

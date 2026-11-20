# Task 1 and Task 2 Research Pipeline

This repository contains the integrated implementation for:

1. **Task 1 - Multilingual Query Generation**
2. **Task 2 - Web Search and Content Acquisition**


Task 1 understands a Hindi, Roman-Hindi, English, or mixed-language question
and creates ranked search-query variants. Task 2 searches those variants,
collects permitted HTML, PDF, and image evidence, classifies extraction/OCR
requirements, and writes a package that Task 3 can process.


## Repository layout

```text

├── Task1_Multilingual_Query_Generator\
│   ├── app.py
│   ├── streamlit_app.py
│   ├── test_app.py
│   ├── requirements.txt
│   └── README.md
└── Task2_Search_Collect\
    ├── main.py
    ├── search_engines.py
    ├── run_pipeline.py
    ├── test_task2_integration.py
    ├── test_run_pipeline.py
    ├── requirements.txt
    ├── README.md
    ├── task1_input.json
    └── task3_input.json
```

Generated download directories are intentionally separate from source code.


## Requirements

- Windows PowerShell
- Python 3.10 or newer
- Internet access for live search and downloads
- A Brave Search API key


Install the unified dependencies from the repository root:

```powershell
cd E:\btp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The module-specific requirements files remain available:

```powershell
python -m pip install -r Task1_Multilingual_Query_Generator\requirements.txt
python -m pip install -r Task2_Search_Collect\requirements.txt
```

##Search configuration

Brave is the primary search backend when a valid key is configured:

```powershell
$env:BRAVE_API_KEY = "your-real-brave-api-key"
```

The key is read only from the environment.

If Brave is unset, rejected, unavailable, or rate-limited, Task 2 falls back
to DuckDuckGo through the configured search backends.

## end-to-end execution

The direct runner is the normal integration path. It calls Task 1 in-process,
writes the Task 1 handoff, then passes that handoff to Task 2:

```powershell
cd E:\btp\task2_search_collect

python run_pipeline.py `
  --query "How did the Indian Constitution promote equality?" `
  --max-queries 4 `
  --top-k 10
```

Run several questions in one batch by using one `--query` followed by all
question values:

```powershell
python run_pipeline.py `
  --query `
  "भगवान राम के जीवन से हमें क्या सीख मिलती है?" `
  "1857 ki kranti ke mukhya karan kya the?" `
  "How did the Indian Constitution promote equality?" `
  "भारतीय मानसून कृषि के लिए क्यों महत्वपूर्ण है?" `
  "What were the main achievements of the Indus Valley Civilization?" `
  --max-queries 4 `
  --top-k 10
```

Do not repeat `--query`; the command-line option accepts one or more values.

### Execution options

| Option | Default | Meaning |
|---|---:|---|
| `--query` | required | One or more user questions |
| `--max-queries` | `4` | Maximum Task 1 variants per question, from 2 to 10 |
| `--top-k` | `10` | Maximum search results per generated variant |
| `--download-dir` | `downloads` | Optional separate download root |

Use an isolated directory for experiments:

```powershell
python run_pipeline.py `
  --query "What is the importance of the monsoon season in Indian agriculture?" `
  --top-k 10 `
  --download-dir E:\btp\task2_search_collect\downloads_monsoon_test
```

The output JSON remains in `task2_search_collect`, while downloaded files go
under the selected directory.

## Integrated data flow

```text
user question(s)
        │
        ▼
Task 1: app.generate_queries()
        │
        ├── language detection
        ├── normalization
        ├── intent and constraint extraction
        ├── entity and key-term extraction
        ├── Hindi/English/Roman-Hindi query variants
        └── duplicate removal and ranking
        │
        ▼
task1_input.json
        │
        ▼
Task 2: search_and_collect()
        │
        ├── Brave/DDG search cascade
        ├── URL normalization and deduplication
        ├── robots and metadata restrictions
        ├── licence and source classification
        ├── HTML/PDF/image downloads
        ├── image relevance filtering
        └── OCR/direct-extraction classification
        │
        ▼
task3_input.json + downloads\
        │
        ▼
Task 3: OCR, passage extraction, confidence, and final evidence processing
```

## Task 1 details

Task 1 is implemented in
[`Task1_Multilingual_Query_Generator/app.py`](Task1_Multilingual_Query_Generator/app.py).

It currently:

- Cleans Unicode text and removes BOM/zero-width noise.
- Detects `hindi_devanagari`, `roman_hindi`, `english`, or `mixed`.
- Normalizes common Roman-Hindi spellings.
- Converts between supported Hindi and English forms where the rule lexicon
  provides a mapping.
- Detects intent such as reason, procedure/explanation, time, location,
  comparison, and impact.
- Extracts named entities, key terms, and constraints.
- Generates ranked search-query candidates with language, kind, score,
  intent-preservation, and entity-preservation metadata.
- Removes exact duplicates after case, whitespace, and trailing-punctuation
  normalization.
- Enforces `max_queries` between 2 and 10.
- Preserves full metadata in the direct Task 1 to Task 2 pipeline.

The baseline generator is deterministic and rule based. Ollama is optional.
If the Ollama backend is enabled but unavailable or invalid, Task 1 reports a
warning and retains the deterministic baseline.

The baseline disables unsafe Roman-Hindi/mixed-script translations instead of
producing word mixtures. Entity component words are excluded from extra key
terms, and Ollama responses use structured JSON lists with model-failure
fallback.

### Task 1 outputs

The full Task 1 response includes:

- `original_query`
- `normalized_query`
- `detected_language`
- `devanagari_query`
- `english_query`
- `intent`
- `named_entities`
- `key_terms`
- `constraints`
- `search_queries`
- `ranking_mode`
- `warnings`

Task 2 also accepts older/compact handoff forms:

- `search_queries[].query`
- `search_queries[].text`
- `queries[].text`

It accepts a single JSON object or an array and reads both normal UTF-8 and
UTF-8 BOM-prefixed JSON.

## Task 2 details

Task 2 is implemented in
[`task2_search_collect/main.py`](task2_search_collect/main.py), with search
backends in [`search_engines.py`](task2_search_collect/search_engines.py).

For each generated variant, Task 2:

1. Requests up to `top_k` search results.
2. Preserves query, language, kind, score, and provenance metadata.
3. Normalizes URLs and merges duplicate top-level results.
4. Checks `robots.txt`, meta robots, and `X-Robots-Tag`.
5. Classifies licence information and source type.
6. Downloads permitted HTML, PDF, and image resources.
7. Extracts visible HTML text and page metadata.
8. Inspects PDF text page by page.
9. Classifies direct extraction versus OCR requirements.
10. Writes usable processing lists for Task 3 while retaining failed records
    for traceability.

### Image filtering

Linked images are not accepted solely because their URL contains a query
keyword. This is important because Wikipedia and CDN URLs often contain hashes
or generic names.

The current image flow:

- Captures `src`, `alt`, and `title` from `<img>` tags.
- Supports extensionless and hash-named image URLs.
- Uses relevant `alt`/`title` metadata for linked-image relevance.
- Rejects linked images with no usable metadata.
- Rejects common non-content assets such as logos, headers, menus, banners,
  placeholders, tracking pixels, product thumbnails, rating graphics,
  contact widgets, and marketing/service assets.
- Retains relevant historical, educational, religious, agricultural, and
  other content images when their metadata identifies the topic.
- Applies the existing minimum image-size check.

Old download folders are not retroactively cleaned. Use a new
`--download-dir` for a clean run.

### OCR classification

Task 2 classifies but does not execute OCR:

- Images are routed to OCR by default.
- PDFs are inspected across all pages.
- Fully text-based PDFs are marked for direct extraction.
- Scanned PDFs are marked for OCR.
- Mixed PDFs include likely page numbers needing OCR.
- HTML uses downloaded visible text rather than search snippets.
- Failed or too-small records are excluded from usable processing lists.

Task 3 should execute OCR, normalize OCR text, extract passages, calculate
confidence, and preserve page/image provenance.

## Output files

The integrated run creates:

- `task1_input.json` - Task 1 to Task 2 handoff.
- `task3_input.json` - Task 2 to Task 3 document package.
- `downloads\html_pages\<query-folder>\` - saved HTML.
- `downloads\pdfs\<query-folder>\` - saved PDFs.
- `downloads\photos\<query-folder>\` - saved images.

`task3_input.json` contains:

- document records and IDs
- source URLs and provenance
- original query and query variants
- content type and extraction status
- saved file paths
- licence and robots status
- OCR instructions and PDF page hints
- `processing_requirements.ocr_required`
- `processing_requirements.text_extracted`
- collection and retrieval statistics

The processing lists contain only usable document IDs. Failed, excluded, or
too-small records remain available in the documents section for traceability.

## Optional REST and Streamlit modes

The direct runner is recommended. Task 1 can also run as an API:

```powershell
cd E:\btp\Task1_Multilingual_Query_Generator
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Health check:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health"
```

Generate a compact Task 1 handoff:

```powershell
$body = @{
    user_query = "Ram kaise the?"
    max_queries = 4
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/task1/generate" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

The optional Streamlit demo is started with:

```powershell
cd E:\btp\Task1_Multilingual_Query_Generator
streamlit run streamlit_app.py
```

## Verification and tests

Run both module suites:

```powershell
cd E:\btp\Task1_Multilingual_Query_Generator
python -m pytest -q

cd E:\btp\task2_search_collect
python -m pytest -q
python -m py_compile main.py run_pipeline.py search_engines.py
```


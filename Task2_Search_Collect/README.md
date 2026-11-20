# Task 2: Web Search and Content Acquisition

Task 2 accepts the Task 1 output, searches each ranked query, merges duplicate URLs, downloads permitted HTML/PDF/image content, and creates `task3_input.json` for Task 3.

## Requirements

Python 3.10 or newer is recommended.

Install dependencies:

```powershell
cd E:\btp\task2_search_collect
python -m pip install -r requirements.txt
```

## Brave Search

Brave is optional. Set a valid API key before running Task 2:

```powershell
$env:BRAVE_API_KEY="your-real-brave-api-key"
```

Task 2 falls back to DuckDuckGo when Brave is unset, rejected, unavailable, or rate-limited.

Roman-Hindi and mixed-script search language hints are normalized before Brave calls; Hindi, English, and regional searches are supported.

## Recommended Run

The recommended pipeline runs Task 1 and Task 2 without starting a REST server:

```powershell
cd E:\btp\task2_search_collect
python run_pipeline.py --query "What is the Green Revolution?"
```

Defaults:

- `max_queries`: 4 Task 1 search variants
- `top_k`: 10 results per search variant

Override the defaults:

```powershell
python run_pipeline.py `
  --query "What is the Green Revolution?" `
  --max-queries 6 `
  --top-k 10
```

Use `--download-dir` for an isolated test run so files are not mixed with an
existing `downloads` tree:

```powershell
python run_pipeline.py `
  --query "What are the major teachings associated with Lord Ram?" `
  --top-k 10 `
  --download-dir downloads_lord_ram_test
```

Run several questions in one batch:

```powershell
python run_pipeline.py --query `
  "What is the Green Revolution?" `
  "How did the Green Revolution affect small farmers in India?" `
  "भारत में पंचायती राज व्यवस्था कैसे काम करती है?"
```

## Manual Task 1 Handoff

Task 2 can also run from an existing Task 1 JSON file:

```powershell
python main.py task1_input.json
```

The handoff accepts:

- Full Task 1 objects with `search_queries[].query`
- Compact Task 1 objects with `search_queries[].text`
- Task 2 query aliases with `queries[].text`
- A single object or a JSON array

Task 2 normalizes these shapes into a common `search_queries[].query` form.

`task1_input.json` can contain BOM-prefixed UTF-8 or standard UTF-8 JSON.

## Search and Download Behavior

For each query variant, Task 2:

1. Searches Brave or DuckDuckGo.
2. Requests up to `top_k` results.
3. Collects and normalizes URLs.
4. Merges duplicate canonical URLs.
5. Checks robots.txt and crawl restrictions.
6. Checks meta robots and X-Robots-Tag directives.
7. Downloads permitted HTML, PDF, and image content.
8. Removes files smaller than the configured image threshold.
9. Filters linked PDFs using URL terms and linked images using relevant
   `alt`/`title` metadata, so hash-named CDN images are not rejected by filename
   while unlabelled banners and unrelated images are excluded.
10. Preserves source queries and result provenance.

Task 2 retains failed or excluded records in `task3_input.json` for traceability. It does not silently omit search failures.

## OCR Classification

Task 2 identifies whether Task 3 may need OCR:

- Images: require OCR by default.
- PDFs: inspect extracted text across PDF pages.
- Fully text-based PDFs: direct extraction.
- Fully scanned PDFs: OCR required.
- Mixed PDFs: OCR instruction includes the likely OCR page numbers.
- HTML: use downloaded visible text, not search-snippet length.
- Failed and too-small downloads: excluded from `processing_requirements.ocr_required`.

Task 2 does **not** execute OCR or create OCR-extracted passages. That work belongs to Task 3.

## Outputs

The pipeline creates:

- `task1_input.json`: Task 1 handoff
- `task3_input.json`: raw document package for Task 3
- `downloads/html_pages/<query-folder>/`: HTML files
- `downloads/pdfs/<query-folder>/`: PDF files
- `downloads/photos/<query-folder>/`: image files

The folder slug uses query index and a readable Latin or Unicode folder name.

### Task 3 package

`task3_input.json` contains:

- `task3_input.documents`
- `task3_input.metadata`
- `task3_input.processing_requirements`
- OCR-required usable document IDs
- Direct-text usable document IDs
- Source URL and content provenance
- Extraction status and errors
- Robots, licence, source, and OCR metadata

Usable OCR processing IDs are limited to successful records with saved files.

## Verify the Output

```powershell
python -c "import json, os, collections; d=json.load(open('task3_input.json',encoding='utf-8-sig'))['task3_input']; docs=d['documents']; print('Documents:',len(docs)); print('Types:',dict(collections.Counter(x['content_type'] for x in docs))); print('OCR required:',len(d['processing_requirements']['ocr_required'])); print('Existing OCR files:',sum(bool(x.get('saved_path') and os.path.exists(x['saved_path'])) for x in docs if x['doc_id'] in d['processing_requirements']['ocr_required']))"
```

Verify processing IDs against documents:

```powershell
python -c "import json; d=json.load(open('task3_input.json',encoding='utf-8-sig'))['task3_input']; ids={x['doc_id'] for x in d['documents']}; req=d['processing_requirements']; print('OCR IDs valid:',set(req['ocr_required'])<=ids); print('Text IDs valid:',set(req['text_extracted'])<=ids)"
```

## Tests

Run Task 2 tests:

```powershell
cd E:\btp\task2_search_collect
python -m pytest -q
```

Compile source files:

```powershell
python -m py_compile main.py run_pipeline.py search_engines.py
```

## Repeated Runs

- Live runs perform new searches.
- `task1_input.json` and `task3_input.json` are replaced.
- Download filenames are deterministic for the URL.
- Existing downloaded files may be overwritten.
- Files from earlier runs remain unless removed manually.
- The in-memory search cache lasts for one Python process.

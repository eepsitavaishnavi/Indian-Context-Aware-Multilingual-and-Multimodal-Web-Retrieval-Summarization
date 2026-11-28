"""FastAPI interface for Task 2.

The API calls the same search_and_collect() implementation used by the CLI
pipeline.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from main import (
    TOP_K,
    merge_duplicates,
    merge_pdf_funnels,
    normalize_task1_objects,
    save_output,
    search_and_collect,
)


app = FastAPI(
    title="Task 2 Search and Content Acquisition",
    description="Search, download, classify, and prepare raw documents for Task 3.",
    version="1.0",
)


class Task2Request(BaseModel):
    task1: Any = Field(
        description="One full/compact Task 1 object or a list of objects."
    )
    top_k: int = Field(default=TOP_K, ge=1, le=50)
    download: bool = True
    download_dir: str | None = None


def _collect(task1_payload: Any, top_k: int, download: bool,
             download_dir: str | None):
    try:
        task1_objects = normalize_task1_objects(task1_payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    all_documents = []
    combined_stats = None
    unique_top_level_urls = set()

    for index, task1_object in enumerate(task1_objects, start=1):
        documents, stats = search_and_collect(
            task1_object,
            top_k=top_k,
            download=download,
            query_index=index,
            download_dir=download_dir,
        )
        all_documents.extend(documents)
        unique_top_level_urls.update(
            stats.get("unique_top_level_url_strings", [])
        )

        if combined_stats is None:
            combined_stats = stats
        else:
            for key in (
                "raw_count",
                "total_documents",
                "ocr_count",
                "direct_count",
                "robots_excluded",
                "meta_robots_excluded",
                "licence_excluded",
                "unsupported_format_excluded",
                "too_small_excluded",
                "download_success",
                "download_failed",
                "linked_resources",
                "licence_uncertain",
                "collection_time_seconds",
            ):
                combined_stats[key] = (
                    combined_stats.get(key, 0) + stats.get(key, 0)
                )
            combined_stats["variants_searched"] = (
                combined_stats.get("variants_searched", [])
                + stats.get("variants_searched", [])
            )
            combined_stats["linked_pdf_funnel"] = merge_pdf_funnels(
                combined_stats.get("linked_pdf_funnel"),
                stats.get("linked_pdf_funnel"),
            )
            for key in (
                "original_query",
                "normalized_query",
                "query_slug",
                "key_terms",
                "detected_language",
                "task1_schema_version",
            ):
                existing = combined_stats.get(key)
                if not isinstance(existing, list):
                    existing = [existing]
                combined_stats[key] = existing + [stats.get(key)]

    if len(task1_objects) > 1:
        all_documents = merge_duplicates(all_documents)
        combined_stats["unique_top_level_url_strings"] = sorted(
            unique_top_level_urls
        )
        combined_stats["unique_top_level_urls"] = len(
            combined_stats["unique_top_level_url_strings"]
        )
        combined_stats["total_documents"] = len(all_documents)

    if combined_stats is None:
        raise HTTPException(
            status_code=422,
            detail="No Task 1 query objects were provided.",
        )
    return all_documents, combined_stats


@app.get("/health")
def health():
    return {"status": "ok", "module": "task2"}


@app.post("/api/v1/task2/search-collect")
def search_collect(request: Task2Request):
    documents, stats = _collect(
        request.task1,
        request.top_k,
        request.download,
        request.download_dir,
    )
    return {
        "documents": documents,
        "statistics": stats,
    }


@app.post("/api/v1/task2/task3-input")
def create_task3_input(request: Task2Request):
    documents, stats = _collect(
        request.task1,
        request.top_k,
        request.download,
        request.download_dir,
    )
    save_output(documents, stats, request.top_k)
    with open("task3_input.json", encoding="utf-8-sig") as output:
        return json.load(output)

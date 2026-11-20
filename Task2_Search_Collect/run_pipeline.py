"""
Run Task 1, then Task 2, and write the package for Task 3.

Example:
    python run_pipeline.py --query "What is the Green Revolution?" --max-queries 4
"""

import argparse
import json
import sys
from pathlib import Path


TASK2_DIR = Path(__file__).resolve().parent
TASK1_DIR = TASK2_DIR.parent / "Task1_Multilingual_Query_Generator"

sys.path.insert(0, str(TASK1_DIR))
sys.path.insert(0, str(TASK2_DIR))

from app import generate_queries
from main import load_task1_input, merge_duplicates, save_output, search_and_collect


MAX_QUERIES = 4
TOP_K = 10
TASK1_OUTPUT = TASK2_DIR / "task1_input.json"


def run_pipeline(user_queries, max_queries=MAX_QUERIES, top_k=TOP_K,
                 download_dir=None):
    if isinstance(user_queries, str):
        user_queries = [user_queries]

    if not user_queries:
        raise ValueError("At least one user query is required.")

    task1_payload = [
        generate_queries(query, max_queries).model_dump(mode="json")
        for query in user_queries
    ]
    if len(task1_payload) == 1:
        task1_payload = task1_payload[0]

    TASK1_OUTPUT.write_text(
        json.dumps(task1_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[TASK 1] Saved handoff: {TASK1_OUTPUT}")

    task1_objects = load_task1_input(TASK1_OUTPUT)
    all_documents = []
    combined_stats = None
    unique_top_level_urls = set()

    for index, task1_object in enumerate(task1_objects, start=1):
        print(
            f"\n[TASK 2] Processing question {index}/{len(task1_objects)}: "
            f"{task1_object.get('original_query')}"
        )
        documents, stats = search_and_collect(
            task1_object,
            top_k=top_k,
            download=True,
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
                combined_stats[key] = combined_stats.get(key, 0) + stats.get(key, 0)
            combined_stats["variants_searched"] = (
                combined_stats.get("variants_searched", [])
                + stats.get("variants_searched", [])
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
        combined_stats["unique_top_level_url_strings"] = sorted(unique_top_level_urls)
        combined_stats["unique_top_level_urls"] = len(
            combined_stats["unique_top_level_url_strings"]
        )
        combined_stats["total_documents"] = len(all_documents)

    save_output(all_documents, combined_stats, top_k)
    print("[DONE] Task 3 input: task3_input.json")
    return TASK1_OUTPUT, TASK2_DIR / "task3_input.json"


def main():
    parser = argparse.ArgumentParser(
        description="Run the Task 1 to Task 2 pipeline."
    )
    parser.add_argument(
        "--query",
        nargs="+",
        required=True,
        help="One or more user questions to process in the same batch.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=MAX_QUERIES,
        help="Number of Task 1 search-query variants (2-10).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=TOP_K,
        help="Maximum search results per Task 1 query.",
    )
    parser.add_argument(
        "--download-dir",
        default=None,
        help="Optional separate root directory for this run's downloads.",
    )
    args = parser.parse_args()

    run_pipeline(args.query, args.max_queries, args.top_k, args.download_dir)


if __name__ == "__main__":
    main()

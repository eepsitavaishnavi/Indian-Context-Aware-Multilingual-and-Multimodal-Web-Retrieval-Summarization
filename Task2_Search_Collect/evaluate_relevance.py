"""Prepare and score human relevance judgments for Task 2 live runs.

1. Run: python evaluate_relevance.py prepare
2. Fill the 'label' column in relevance_labels.csv:
   2 = directly answers/substantial
   1 = on-topic/partial
   0 = off-topic, spam, broken, or inaccessible
3. Run: python evaluate_relevance.py score
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path


INPUT_FILE = Path("task3_input.json")
LABEL_FILE = Path("relevance_labels.csv")


def load_package(path: Path):
    package = json.loads(path.read_text(encoding="utf-8-sig"))
    return package.get("task3_input", package)


def generic_original_query(query_text: str, ranked_urls,
                           document_query_map, original_queries):
    """Resolve a search variant without topic-specific marker lists."""
    mapped = []
    for result in ranked_urls:
        canonical = result.get("canonical_url") or result.get("url", "")
        question = document_query_map.get(canonical)
        if question:
            mapped.append(question)
    if mapped:
        counts = defaultdict(int)
        for question in mapped:
            counts[question] += 1
        winner = max(counts, key=counts.get)
        if sum(counts[q] == counts[winner] for q in counts) == 1:
            return winner

    if len(original_queries) == 1:
        return original_queries[0]

    stopwords = {
        "a", "an", "the", "what", "which", "who", "how", "why", "when",
        "where", "is", "are", "was", "were", "did", "do", "does",
        "का", "के", "की", "को", "में", "पर", "से", "क्या", "कौन", "कैसे",
    }
    query_terms = {
        term for term in re.findall(
            r"[A-Za-z0-9]+|[\u0900-\u097F]+",
            query_text.lower(),
        )
        if term not in stopwords
    }
    scores = []
    for question in original_queries:
        question_terms = {
            term for term in re.findall(
                r"[A-Za-z0-9]+|[\u0900-\u097F]+",
                question.lower(),
            )
            if term not in stopwords
        }
        scores.append((
            len(query_terms & question_terms) /
            max(1, len(query_terms | question_terms)),
            question,
        ))
    scores.sort(reverse=True)
    if scores and scores[0][0] > 0:
        if len(scores) == 1 or scores[0][0] > scores[1][0]:
            return scores[0][1]
    return ""


def prepare(input_path: Path, label_path: Path):
    package = load_package(input_path)
    metadata = package.get("metadata", {})
    search_stats = metadata.get("search_stats", {})
    original_queries = metadata.get("original_query", [])
    document_query_map = {}
    for document in package.get("documents", []):
        canonical = document.get("canonical_url") or document.get("url", "")
        if canonical:
            document_query_map[canonical] = document.get("original_query", "")
    if isinstance(original_queries, str):
        original_queries = [original_queries]

    rows = {}
    for query_key, details in search_stats.items():
        query_text = query_key.rsplit("  [", 1)[0]
        original_query = details.get("original_query", "")
        if not original_query:
            ranked_urls = details.get("ranked_urls", [])
            mapped_queries = [
                document_query_map.get(
                    result.get("canonical_url") or result.get("url", "")
                )
                for result in ranked_urls
            ]
            mapped_queries = [q for q in mapped_queries if q]
            if mapped_queries:
                original_query = mapped_queries[0]
            else:
                original_query = generic_original_query(
                    query_text,
                    ranked_urls,
                    document_query_map,
                    original_queries,
                )
        if not details.get("ranked_urls"):
            continue
        for result in details["ranked_urls"][:5]:
            url = result.get("url", "")
            if not url:
                continue
            key = (
                original_query,
                query_text,
                result.get("canonical_url") or url,
                int(result.get("rank", 0)),
            )
            rows[key] = {
                "original_query": original_query,
                "search_query": query_text,
                "kind": details.get("kind", ""),
                "language": details.get("language", ""),
                "engine": details.get("engine_used", ""),
                "rank": int(result.get("rank", 0)),
                "url": url,
                "canonical_url": result.get("canonical_url") or url,
                "title": result.get("title", ""),
                "label": "",
            }

    with label_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(next(iter(rows.values()))) if rows else [
            "original_query", "search_query", "kind", "language", "engine",
            "rank", "url", "canonical_url", "title", "label",
        ])
        writer.writeheader()
        writer.writerows(rows.values())
    return len(rows)


def score(label_path: Path):
    with label_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    labeled = [row for row in rows if row.get("label", "").strip()]
    invalid = []
    for row in labeled:
        try:
            label = int(row["label"])
            if label not in (0, 1, 2):
                invalid.append(row)
        except (TypeError, ValueError):
            invalid.append(row)
    if invalid:
        raise ValueError(
            f"{len(invalid)} label rows use values outside 0, 1, or 2."
        )

    by_variant = defaultdict(list)
    by_query = defaultdict(list)
    for row in labeled:
        by_query[row["original_query"]].append(row)
        by_variant[
            (row["original_query"], row["search_query"], row["engine"])
        ].append(row)

    variant_scores = defaultdict(list)
    variant_ndcg = []
    for (question, search_query, engine), variant_rows in by_variant.items():
        unique_rows = []
        seen = set()
        for row in sorted(variant_rows, key=lambda item: int(item["rank"])):
            canonical = row["canonical_url"]
            if canonical not in seen:
                seen.add(canonical)
                unique_rows.append(row)

        print(f"\nSearch call: {search_query} [{engine}]")
        for k in (1, 3, 5):
            relevant = sum(
                int(row["label"]) >= 1
                for row in unique_rows[:k]
            )
            score_value = (
                relevant / min(k, len(unique_rows))
                if unique_rows else 0.0
            )
            print(f"P@{k}: {score_value:.3f}")
            variant_scores[k].append(score_value)
        gains = [
            int(row["label"]) for row in unique_rows[:5]
        ]
        if len(gains) < 5:
            gains += [0] * (5 - len(gains))
        dcg = sum(
            (2 ** label - 1) / math.log2(rank + 1)
            for rank, label in enumerate(gains, start=1)
        )
        pooled_labels = []
        for row in by_query[question]:
            if row["canonical_url"] not in {
                x["canonical_url"] for x in pooled_labels
            }:
                pooled_labels.append(row)
        ideal_gains = sorted(
            (int(row["label"]) for row in pooled_labels),
            reverse=True,
        )[:5]
        if len(ideal_gains) < 5:
            ideal_gains += [0] * (5 - len(ideal_gains))
        ideal_dcg = sum(
            (2 ** label - 1) / math.log2(rank + 1)
            for rank, label in enumerate(ideal_gains, start=1)
        )
        ndcg = dcg / ideal_dcg if ideal_dcg > 0 else 0.0
        print(f"nDCG@5: {ndcg:.3f}")
        variant_ndcg.append(ndcg)

    print("\nMacro averages")
    for k in (1, 3, 5):
        values = variant_scores[k]
        print(
            f"P@{k}: {sum(values) / len(values):.3f}"
            if values else f"P@{k}: 0.000"
        )
    print(
        f"nDCG@5: {sum(variant_ndcg) / len(variant_ndcg):.3f}"
        if variant_ndcg else "nDCG@5: 0.000"
    )

    for question, question_rows in by_query.items():
        unique_urls = []
        seen = set()
        for row in sorted(question_rows, key=lambda item: int(item["rank"])):
            canonical = row["canonical_url"]
            if canonical not in seen:
                seen.add(canonical)
                unique_urls.append(row)

        print(f"\nQuestion: {question}")
        for k in (1, 3, 5):
            relevant = sum(
                int(row["label"]) >= 1
                for row in unique_urls[:k]
            )
            print(f"P@{k}: {relevant / min(k, len(unique_urls)):.3f}" if unique_urls else f"P@{k}: 0.000")

        useful = sum(int(row["label"]) >= 1 for row in unique_urls)
        print(
            f"Useful-source rate: {useful / len(unique_urls):.3f}"
            if unique_urls else "Useful-source rate: 0.000"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "score"])
    parser.add_argument("--input", default=str(INPUT_FILE))
    parser.add_argument("--labels", default=str(LABEL_FILE))
    args = parser.parse_args()

    if args.action == "prepare":
        count = prepare(Path(args.input), Path(args.labels))
        print(f"Wrote {count} pooled rows to {args.labels}")
    else:
        score(Path(args.labels))


if __name__ == "__main__":
    main()


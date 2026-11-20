import json
import sys
from pathlib import Path

from run_pipeline import MAX_QUERIES, TASK1_OUTPUT, TOP_K

TASK1_DIR = Path(__file__).resolve().parents[1] / "Task1_Multilingual_Query_Generator"
sys.path.insert(0, str(TASK1_DIR))
from app import generate_queries


def test_pipeline_defaults():
    assert 2 <= MAX_QUERIES <= 10
    assert TOP_K == 10
    assert TASK1_OUTPUT.name == "task1_input.json"


def test_pipeline_payload_preserves_full_task1_metadata():
    payload = generate_queries(
        "What is the Green Revolution?",
        4,
    ).model_dump(mode="json")

    assert payload["original_query"] == "What is the Green Revolution?"
    assert payload["normalized_query"] == "What is the Green Revolution?"
    assert payload["key_terms"]
    assert payload["search_queries"]
    assert "score" in payload["search_queries"][0]
    assert "kind" in payload["search_queries"][0]
    assert "intent" in payload
    assert "named_entities" in payload
    assert "constraints" in payload

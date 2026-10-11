from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from api_server.answers import answer_question  # noqa: E402
from api_server.main import _org_or_422  # noqa: E402


@pytest.fixture()
def envelope() -> dict:
    path = ROOT / "submission/smoke-100/envelopes.jsonl"
    with path.open(encoding="utf-8") as stream:
        return json.loads(next(line for line in stream if line.strip()))


def test_answers_are_grounded_in_the_selected_envelope(envelope: dict) -> None:
    result = answer_question("What are the latest financials?", envelope)

    assert result["topic"] == "financials"
    assert "NOK" in result["answer"]
    assert result["run_id"] == envelope["run_id"]
    assert result["sources"]
    assert all(source["url"] for source in result["sources"])


def test_unknown_answers_preserve_abstention(envelope: dict) -> None:
    result = answer_question("What remains unknown?", envelope)

    assert result["topic"] == "gaps"
    assert "unavailable rather than inferred" in result["answer"]
    assert result["unknown_areas"] == envelope["unknown_areas"]


def test_organisation_numbers_are_checksum_validated() -> None:
    assert _org_or_422("985 589 003") == "985589003"
    with pytest.raises(HTTPException) as error:
        _org_or_422("123456789")
    assert error.value.status_code == 422


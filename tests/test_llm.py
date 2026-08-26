import pytest
from conftest import FakeClient
from pydantic import BaseModel

from invoice_agent.llm import _parse_json_reply, complete_structured


class _Toy(BaseModel):
    name: str
    value: int


def test_parses_bare_json():
    assert _parse_json_reply('{"a": 1}') == {"a": 1}


def test_parses_fenced_json():
    assert _parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}


def test_parses_json_with_surrounding_prose():
    assert _parse_json_reply('Here you go:\n{"a": 1}\nHope that helps!') == {"a": 1}


def test_unparseable_reply_raises():
    with pytest.raises(ValueError):
        _parse_json_reply("I cannot help with that.")


def test_self_correction_loop_feeds_errors_back():
    """A client that returns junk then valid JSON should succeed on attempt 2."""
    client = FakeClient(['{"name": "x"}', '{"name": "x", "value": 2}'])
    result, attempts, corrections = complete_structured(client, "m", "sys", "user", _Toy)
    assert result == _Toy(name="x", value=2)
    assert attempts == 2
    assert len(corrections) == 1
    # the retry message must contain the validation error for self-correction
    retry_messages = client.calls[1]["messages"]
    assert any("value" in str(m.get("content", "")) for m in retry_messages[-1:])


def test_retry_budget_exhaustion_raises():
    from invoice_agent.llm import ExtractionFailedError

    client = FakeClient(["junk", "more junk", "still junk"])
    with pytest.raises(ExtractionFailedError):
        complete_structured(client, "m", "sys", "user", _Toy)

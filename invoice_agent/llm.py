"""LLM client and structured-output helper.

Works against any OpenAI-compatible endpoint (xAI Grok by default). Structured
output is enforced client-side: the model is prompted with the JSON schema, the
reply is parsed and validated with Pydantic, and validation errors are fed back
for a bounded number of self-correction retries.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from invoice_agent.config import LLMSettings

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ExtractionFailedError(RuntimeError):
    """The model could not produce schema-valid output within the retry budget."""


def make_client(settings: LLMSettings) -> OpenAI:
    return OpenAI(api_key=settings.api_key, base_url=settings.base_url)


def _parse_json_reply(reply: str) -> dict:
    """Parse a model reply into a JSON object, tolerating markdown fences and prose."""
    cleaned = _FENCE_RE.sub("", reply.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def complete_structured(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    response_model: type[T],
    max_attempts: int = 3,
) -> tuple[T, int, list[str]]:
    """Call the LLM and return (validated object, attempts used, correction notes).

    On schema violations the error is appended to the conversation and the model
    is asked to correct itself -- the pipeline's first self-correction loop.
    """
    schema = json.dumps(response_model.model_json_schema(), indent=2)
    messages: list[dict] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"{user}\n\n"
                "Respond with a single JSON object matching this JSON schema exactly. "
                "Output only the JSON object -- no prose, no markdown fences.\n\n"
                f"{schema}"
            ),
        },
    ]
    corrections: list[str] = []

    for attempt in range(1, max_attempts + 1):
        response = client.chat.completions.create(model=model, messages=messages, temperature=0)
        reply = response.choices[0].message.content or ""
        try:
            return response_model.model_validate(_parse_json_reply(reply)), attempt, corrections
        except (json.JSONDecodeError, ValidationError) as exc:
            error_note = f"attempt {attempt}: {type(exc).__name__}: {exc}"
            corrections.append(error_note)
            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid against the schema. "
                        f"Error:\n{exc}\n\n"
                        "Reply again with only the corrected JSON object."
                    ),
                }
            )

    raise ExtractionFailedError(
        f"Model failed to produce valid {response_model.__name__} after {max_attempts} attempts: "
        + "; ".join(corrections)
    )

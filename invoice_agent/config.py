"""LLM provider configuration.

Provider-agnostic: any OpenAI-compatible endpoint works. Resolution order:

1. Explicit overrides: LLM_API_KEY + LLM_BASE_URL + LLM_MODEL
2. XAI_API_KEY      -> xAI Grok (the case's preferred reasoning engine)
3. OPENAI_API_KEY   -> OpenAI
4. ANTHROPIC_API_KEY-> Anthropic via its OpenAI-compatibility endpoint

Model choice per provider can always be overridden with LLM_MODEL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when no usable LLM credentials are found."""


@dataclass(frozen=True)
class LLMSettings:
    api_key: str
    base_url: str
    model: str
    provider: str


_PROVIDERS = [
    # (env var, provider name, base_url, default model)
    ("XAI_API_KEY", "xai", "https://api.x.ai/v1", "grok-4.6"),
    ("OPENAI_API_KEY", "openai", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("ANTHROPIC_API_KEY", "anthropic", "https://api.anthropic.com/v1/", "claude-opus-5"),
]

_NO_KEY_MESSAGE = """\
No LLM API key found. Set one of the following in your environment or a .env file:

  XAI_API_KEY        (recommended -- Grok is the case's preferred engine)
  OPENAI_API_KEY
  ANTHROPIC_API_KEY

or configure a custom OpenAI-compatible endpoint explicitly:

  LLM_API_KEY=...  LLM_BASE_URL=...  LLM_MODEL=...
"""


def resolve_llm_settings(env: dict[str, str] | None = None) -> LLMSettings:
    """Resolve LLM credentials from the environment. Raises ConfigError if none found."""
    if env is None:
        load_dotenv()
        env = dict(os.environ)

    explicit_key = env.get("LLM_API_KEY")
    if explicit_key:
        base_url = env.get("LLM_BASE_URL")
        model = env.get("LLM_MODEL")
        if not (base_url and model):
            raise ConfigError("LLM_API_KEY is set but LLM_BASE_URL or LLM_MODEL is missing.")
        return LLMSettings(api_key=explicit_key, base_url=base_url, model=model, provider="custom")

    for env_var, provider, base_url, default_model in _PROVIDERS:
        key = env.get(env_var)
        if key:
            return LLMSettings(
                api_key=key,
                base_url=base_url,
                model=env.get("LLM_MODEL", default_model),
                provider=provider,
            )

    raise ConfigError(_NO_KEY_MESSAGE)

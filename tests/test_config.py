import pytest

from invoice_agent.config import ConfigError, resolve_llm_settings


def test_explicit_llm_settings_win_over_provider_keys():
    settings = resolve_llm_settings(
        {
            "LLM_API_KEY": "k",
            "LLM_BASE_URL": "https://example.com/v1",
            "LLM_MODEL": "custom-model",
            "XAI_API_KEY": "ignored",
        }
    )
    assert settings.provider == "custom"
    assert settings.model == "custom-model"


def test_explicit_key_without_base_url_errors():
    with pytest.raises(ConfigError):
        resolve_llm_settings({"LLM_API_KEY": "k", "LLM_MODEL": "m"})


def test_xai_key_selects_grok():
    settings = resolve_llm_settings({"XAI_API_KEY": "k"})
    assert settings.provider == "xai"
    assert settings.base_url == "https://api.x.ai/v1"
    assert settings.model == "grok-4.6"


def test_xai_preferred_over_openai():
    settings = resolve_llm_settings({"XAI_API_KEY": "x", "OPENAI_API_KEY": "o"})
    assert settings.provider == "xai"


def test_model_override_applies_to_detected_provider():
    settings = resolve_llm_settings({"OPENAI_API_KEY": "k", "LLM_MODEL": "gpt-4o"})
    assert settings.provider == "openai"
    assert settings.model == "gpt-4o"


def test_no_key_raises_actionable_error():
    with pytest.raises(ConfigError, match="XAI_API_KEY"):
        resolve_llm_settings({})


def test_xai_default_uses_fast_extraction_model():
    settings = resolve_llm_settings({"XAI_API_KEY": "k"})
    assert settings.extraction_model == "grok-4.20-0309-non-reasoning"


def test_model_override_resets_extraction_model_to_match():
    settings = resolve_llm_settings({"XAI_API_KEY": "k", "LLM_MODEL": "grok-4.5"})
    assert settings.extraction_model == "grok-4.5"


def test_explicit_extraction_model_wins():
    settings = resolve_llm_settings({"XAI_API_KEY": "k", "LLM_EXTRACTION_MODEL": "grok-4.3"})
    assert settings.model == "grok-4.6"
    assert settings.extraction_model == "grok-4.3"

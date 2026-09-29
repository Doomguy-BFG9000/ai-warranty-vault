"""What gets put on the wire to the model provider.

These tests exist because of a real failure. The service set `temperature=0.0` on
every chat model, reasoning that grounded extraction wants no sampling variety. That
parameter was removed in this model generation, so the first real call returned

    400 invalid_request_error: `temperature` is deprecated for this model.

langchain-anthropic strips sampling parameters only for `claude-fable-5*`, so on any
other current model an unwanted `temperature` reaches the API and the request fails
outright. No mock could have caught it: the scripted model ignores every parameter.
Inspecting the built payload can, and costs nothing.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.errors import ProviderError, ProviderNotConfigured
from app.providers import build_chat_model, build_embeddings, describe_provider_failure

# Sampling parameters the current model generation rejects with a 400.
BANNED_PARAMS = ("temperature", "top_p", "top_k")


def _live_settings(**overrides) -> Settings:
    return Settings(
        provider_mode="live",
        anthropic_api_key="sk-ant-not-a-real-key",
        openai_api_key="sk-not-a-real-key",
        **overrides,
    )


def _payload(chat) -> dict:
    # Builds the request body without sending it, so this needs no key that works
    # and makes no network call.
    return chat._get_request_payload([{"role": "user", "content": "ping"}])


def test_no_sampling_parameters_are_ever_sent():
    payload = _payload(build_chat_model(_live_settings()))
    present = [name for name in BANNED_PARAMS if name in payload]
    assert present == [], (
        f"{present} would be sent to the API, which rejects them with a 400. "
        "Control depth with output_config.effort instead."
    )


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5", "claude-opus-5"])
def test_no_sampling_parameters_on_any_configured_model(model):
    """Not just the default — the model is an environment variable."""
    payload = _payload(build_chat_model(_live_settings(answer_model=model)))
    assert [name for name in BANNED_PARAMS if name in payload] == []
    assert payload["model"] == model


def test_effort_is_what_controls_depth():
    settings = _live_settings(model_effort="high", vision_effort="low")

    answering = _payload(build_chat_model(settings))
    assert answering["output_config"]["effort"] == "high"

    # The transcription path overrides it: mechanical work, and the expensive path.
    transcription = _payload(
        build_chat_model(settings, model=settings.vision_model, effort=settings.vision_effort)
    )
    assert transcription["output_config"]["effort"] == "low"


def test_the_output_ceiling_leaves_room_for_thinking():
    """Thinking tokens count against max_tokens, so a tight ceiling truncates answers."""
    payload = _payload(build_chat_model(_live_settings()))
    assert payload["max_tokens"] >= 8192


def test_a_missing_key_is_refused_rather_than_mocked():
    settings = Settings(provider_mode="live", anthropic_api_key=None, openai_api_key=None)
    with pytest.raises(ProviderNotConfigured):
        build_chat_model(settings)
    with pytest.raises(ProviderNotConfigured):
        build_embeddings(settings)


def test_mock_mode_is_only_reached_by_asking_for_it():
    from app.mock_chat import ScriptedChatModel

    assert isinstance(build_chat_model(Settings(provider_mode="mock")), ScriptedChatModel)
    # And a missing key in live mode does not fall through to it.
    with pytest.raises(ProviderNotConfigured):
        build_chat_model(Settings(provider_mode="live", anthropic_api_key=None))


class _Quota(Exception):
    pass


def test_a_provider_failure_keeps_the_providers_own_message():
    """`RateLimitError` alone cannot be acted on; the message says which kind it is.

    OpenAI raises it both for real throttling and for `insufficient_quota` — a key
    with no credit — and those need opposite responses from whoever reads the log.
    """
    described = describe_provider_failure(
        _Quota("Error code: 429 - insufficient_quota: You exceeded your current quota")
    )
    assert "_Quota" in described
    assert "insufficient_quota" in described

    # Trimmed, because some providers answer with a whole HTML page.
    assert len(describe_provider_failure(_Quota("x" * 5000))) < 400

    # And a bare exception still names itself.
    assert describe_provider_failure(_Quota("")) == "_Quota"


async def test_an_embedding_failure_reaches_the_caller_as_a_provider_error(monkeypatch, embeddings):
    async def explode(_texts):
        raise _Quota("Error code: 429 - insufficient_quota")

    monkeypatch.setattr(embeddings.embeddings, "aembed_documents", explode)

    from app.providers import embed_documents

    with pytest.raises(ProviderError) as caught:
        await embed_documents(embeddings, ["some text"])
    # The message carries the diagnosis rather than just the exception class.
    assert "insufficient_quota" in str(caught.value)

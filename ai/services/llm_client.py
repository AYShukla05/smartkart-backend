import logging

import anthropic
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 600


class LLMGenerationError(Exception):
    """Raised when the LLM provider fails to generate a response."""


def _get_client():
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _build_system_param(system):
    """Wrap a system prompt string as a cacheable content block.

    System prompts here are constant across requests (same instructions,
    different user message), so marking them cache_control="ephemeral"
    lets Anthropic skip reprocessing those tokens on repeat calls. Below
    the provider's minimum cacheable prefix length (~2048 tokens for Haiku)
    this is simply a no-op - harmless to always set, and it's the
    mechanism later phases with longer system prompts will rely on for
    real savings.
    """
    if not system:
        return None
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def generate(prompt, system=None, model=DEFAULT_MODEL, max_tokens=DEFAULT_MAX_TOKENS):
    """Blocking call. Returns the complete generated text."""
    try:
        response = _get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_build_system_param(system),
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except anthropic.APIError:
        logger.error("LLM generate() call failed", exc_info=True)
        raise LLMGenerationError("Failed to generate text.")


def stream(prompt, system=None, model=DEFAULT_MODEL, max_tokens=DEFAULT_MAX_TOKENS):
    """Returns a generator yielding string tokens one at a time."""
    try:
        with _get_client().messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=_build_system_param(system),
            messages=[{"role": "user", "content": prompt}],
        ) as text_stream:
            for token in text_stream.text_stream:
                yield token
    except anthropic.APIError:
        logger.error("LLM stream() call failed", exc_info=True)
        raise LLMGenerationError("Failed to generate text.")

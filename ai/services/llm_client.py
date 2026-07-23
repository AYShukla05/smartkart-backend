import logging

import anthropic
import voyageai
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 600

DEFAULT_EMBEDDING_MODEL = "voyage-4-lite"
DEFAULT_EMBEDDING_DIMENSIONS = 512
CURRENT_EMBEDDING_MODEL_ID = f"{DEFAULT_EMBEDDING_MODEL}-{DEFAULT_EMBEDDING_DIMENSIONS}"


class LLMGenerationError(Exception):
    """Raised when the LLM provider fails to generate a response."""


def _get_client():
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _get_voyage_client():
    return voyageai.Client(api_key=settings.VOYAGE_API_KEY)


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


def call_with_tools(messages, system, tools, model=DEFAULT_MODEL, max_tokens=700):
    """Make a single call to the model with tool definitions.

    Returns the raw Anthropic API response object. Makes exactly one API
    call - does not loop, does not execute tools, does not know what the
    tools do. The caller inspects response.content blocks and decides
    whether to execute tool calls or treat the response as final.
    """
    try:
        return _get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_build_system_param(system),
            tools=tools,
            messages=messages,
        )
    except anthropic.APIError:
        logger.error("LLM call_with_tools() call failed", exc_info=True)
        raise LLMGenerationError("Failed to call model with tools.")


def embed(text, *, input_type, model=DEFAULT_EMBEDDING_MODEL, dimensions=DEFAULT_EMBEDDING_DIMENSIONS):
    """Embed a single text string. Returns a list of floats (the vector).

    input_type has no default and must be passed explicitly ("query" or
    "document") - Voyage encodes these differently, and defaulting it
    risks a call site silently embedding on the wrong side of that
    asymmetry with no error, just worse ranking.
    """
    vectors, _ = embed_batch([text], input_type=input_type, model=model, dimensions=dimensions)
    return vectors[0]


def embed_batch(texts, *, input_type, model=DEFAULT_EMBEDDING_MODEL, dimensions=DEFAULT_EMBEDDING_DIMENSIONS):
    """Embed multiple texts in a single API call.

    Returns (vectors, total_tokens): vectors is a list of embeddings, one
    per input text, in the same order; total_tokens is Voyage's reported
    usage for the call, for real (not estimated) cost tracking by callers
    that index in bulk. Raises LLMGenerationError on failure.
    """
    try:
        result = _get_voyage_client().embed(
            texts,
            model=model,
            input_type=input_type,
            output_dimension=dimensions,
        )
        return result.embeddings, result.total_tokens
    except voyageai.error.VoyageError:
        logger.error("LLM embed_batch() call failed", exc_info=True)
        raise LLMGenerationError("Failed to generate embeddings.")

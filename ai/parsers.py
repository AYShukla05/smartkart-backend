import json
import logging
import re

logger = logging.getLogger(__name__)

DESCRIPTION_MARKER = '"description": "'

_MARKDOWN_FENCE_OPEN = re.compile(r"^```(?:json)?\s*")
_MARKDOWN_FENCE_CLOSE = re.compile(r"\s*```$")
_STRING_FIELD = r'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"'
_ARRAY_FIELD = r'"{field}"\s*:\s*\[(.*?)\]'


def strip_markdown_fences(text):
    text = _MARKDOWN_FENCE_OPEN.sub("", text.strip())
    text = _MARKDOWN_FENCE_CLOSE.sub("", text)
    return text.strip()


def parse_description_json(buffer):
    """Parse the model's complete JSON buffer into structured fields.

    Never raises: a truncated or malformed response (e.g. generation cut
    off by the token cap) degrades to a best-effort partial result instead
    of failing the request.
    """
    cleaned = strip_markdown_fences(buffer)
    try:
        data = json.loads(cleaned)
        return {
            "title": data.get("title", ""),
            "bullets": data.get("bullets", []),
            "seo_keywords": data.get("seo_keywords", []),
            "description": data.get("description", ""),
        }
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM JSON response as valid JSON", exc_info=True)
        return _recover_partial_fields(cleaned)


def _recover_partial_fields(cleaned):
    """Best-effort regex extraction, used only when the buffer isn't valid
    JSON at all (e.g. truncated mid-string by the max_tokens cap)."""
    result = {"title": "", "bullets": [], "seo_keywords": [], "description": ""}

    title_match = re.search(_STRING_FIELD.format(field="title"), cleaned)
    if title_match:
        result["title"] = title_match.group(1)

    # Description may be unterminated (no closing quote) if truncated mid-value.
    description_match = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)', cleaned)
    if description_match:
        result["description"] = description_match.group(1)

    bullets_match = re.search(_ARRAY_FIELD.format(field="bullets"), cleaned, re.DOTALL)
    if bullets_match:
        result["bullets"] = re.findall(r'"((?:[^"\\]|\\.)*)"', bullets_match.group(1))

    keywords_match = re.search(_ARRAY_FIELD.format(field="seo_keywords"), cleaned, re.DOTALL)
    if keywords_match:
        result["seo_keywords"] = re.findall(r'"((?:[^"\\]|\\.)*)"', keywords_match.group(1))

    return result


class DescriptionStreamParser:
    """Accumulates streamed tokens and splits them into two channels:

    - plain description text, forwarded to the caller as soon as the
      streamed JSON reaches the "description" field's value
    - everything else, buffered silently until the stream ends

    The `"description": "` marker can straddle a token boundary (one
    provider token might be `desc` and the next `ription`), so detection
    is done against the accumulated buffer, not each token in isolation.
    """

    def __init__(self):
        self._buffer = ""
        self._description_started = False

    def feed(self, token):
        """Feed one streamed token. Returns the substring (possibly empty)
        that should be forwarded to the caller as live description text."""
        self._buffer += token

        if self._description_started:
            return token

        marker_index = self._buffer.find(DESCRIPTION_MARKER)
        if marker_index == -1:
            return ""

        self._description_started = True
        return self._buffer[marker_index + len(DESCRIPTION_MARKER):]

    def finalize(self):
        """Parse the complete buffer into structured fields once streaming ends."""
        return parse_description_json(self._buffer)

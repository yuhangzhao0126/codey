"""Provider error sniffer: map heterogeneous provider errors to one type.

OpenAI returns 400 + code=context_length_exceeded. Anthropic-compatible
gateways return 400 with "prompt is too long" in the message. Some gateways
return HTTP 413. We catch and re-raise as PromptTooLongError so the reactive
path in turn.py has one exception type to handle.
"""
from __future__ import annotations


class PromptTooLongError(Exception):
    """Raised when the provider reports the prompt exceeded its limit."""
    def __init__(self, message: str, *, original: BaseException | None = None):
        super().__init__(message)
        self.original = original


_PROMPT_TOO_LONG_SUBSTRINGS = (
    "context length",
    "context_length_exceeded",
    "prompt is too long",
    "prompt too long",
    "maximum context",
    "too many tokens",
    "request entity too large",
)


def sniff(exc: BaseException) -> PromptTooLongError | None:
    """Return a PromptTooLongError if `exc` looks like a context-overflow
    error from any supported provider; otherwise None.

    Inspects:
      - exc.status_code (if present): 413 is a strong signal
      - exc.code        (if present): "context_length_exceeded"
      - str(exc), exc.message (if present): substring scan
    """
    status = getattr(exc, "status_code", None)
    if status == 413:
        return PromptTooLongError(str(exc) or "request entity too large", original=exc)

    code = getattr(exc, "code", None)
    if isinstance(code, str) and "context_length" in code.lower():
        return PromptTooLongError(str(exc) or "context length exceeded", original=exc)

    blobs = []
    msg = getattr(exc, "message", None)
    if isinstance(msg, str):
        blobs.append(msg.lower())
    blobs.append(str(exc).lower())
    haystack = " ".join(blobs)
    for needle in _PROMPT_TOO_LONG_SUBSTRINGS:
        if needle in haystack:
            return PromptTooLongError(str(exc) or needle, original=exc)
    return None

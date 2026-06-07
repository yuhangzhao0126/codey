"""Tests for the provider-error sniffer."""
from __future__ import annotations

import pytest

from codey.context.errors import PromptTooLongError, sniff


class FakeProviderError(Exception):
    """Stand-in for openai.BadRequestError / openai.APIStatusError."""
    def __init__(self, status_code: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def test_sniff_recognizes_openai_context_length_exceeded():
    err = FakeProviderError(400, "This model's maximum context length is 128000 tokens",
                            code="context_length_exceeded")
    result = sniff(err)
    assert isinstance(result, PromptTooLongError)


def test_sniff_recognizes_anthropic_prompt_too_long():
    err = FakeProviderError(400, "prompt is too long: 250000 tokens > 200000 maximum")
    assert isinstance(sniff(err), PromptTooLongError)


def test_sniff_recognizes_http_413():
    err = FakeProviderError(413, "Request Entity Too Large")
    assert isinstance(sniff(err), PromptTooLongError)


def test_sniff_returns_none_on_unrelated_error():
    err = FakeProviderError(429, "Too Many Requests", code="rate_limit_exceeded")
    assert sniff(err) is None


def test_sniff_returns_none_on_random_exception():
    err = ValueError("nothing to do with the provider")
    assert sniff(err) is None


def test_prompt_too_long_carries_original():
    original = FakeProviderError(413, "too big")
    e = PromptTooLongError("too big", original=original)
    assert e.original is original

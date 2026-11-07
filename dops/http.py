from __future__ import annotations

import email.utils
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping

from . import __version__
from .config import DEFAULT_HTTP_BACKOFF_SECONDS, DEFAULT_HTTP_MAX_RETRIES
from .runtime import emit_diagnostic


@dataclass(slots=True)
class HttpResponse:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpStatusError(RuntimeError):
    def __init__(self, status: int, url: str, headers: Mapping[str, str], body: bytes, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.url = url
        self.headers = headers
        self.body = body
        self.reason = reason


def default_user_agent() -> str:
    return f"decisionops-cli/{__version__} (+https://github.com/aidecisionops/cli)"


_MAX_RETRY_AFTER_SECONDS = 60.0


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        seconds = max(float(value), 0.0)
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = max(parsed.timestamp() - time.time(), 0.0)
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def _retry_delay(attempt: int, headers: Mapping[str, str] | None = None) -> float:
    if headers:
        retry_after = _retry_after_seconds(headers)
        if retry_after is not None:
            return retry_after
    return DEFAULT_HTTP_BACKOFF_SECONDS * (2 ** max(attempt - 1, 0))


def _should_retry(status: int | None, attempt: int, max_attempts: int) -> bool:
    return attempt < max_attempts and (status is None or status in {408, 429, 500, 502, 503, 504})


def urlopen_with_retries(
    request: urllib.request.Request,
    *,
    timeout: float,
    context,
    max_attempts: int | None = None,
) -> HttpResponse:
    configured_retries = DEFAULT_HTTP_MAX_RETRIES if max_attempts is None else max_attempts
    attempts = max(configured_retries + 1, 1)
    method = request.get_method().upper()
    url = request.full_url
    last_error_summary: str | None = None
    for attempt in range(1, attempts + 1):
        emit_diagnostic(f"{method} {url} (attempt {attempt}/{attempts})")
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                response_headers = getattr(response, "headers", {})
                response_body = response.read() if hasattr(response, "read") else b""
                return HttpResponse(
                    url=response.geturl(),
                    status=getattr(response, "status", 200),
                    headers={key.lower(): value for key, value in response_headers.items()} if hasattr(response_headers, "items") else {},
                    body=response_body,
                )
        except urllib.error.HTTPError as error:
            body = error.read()
            headers = {key.lower(): value for key, value in error.headers.items()}
            last_error_summary = f"HTTP {error.code}: {error.reason}"
            if _should_retry(error.code, attempt, attempts):
                delay = _retry_delay(attempt, headers)
                emit_diagnostic(f"Retrying {method} {url} after HTTP {error.code} in {delay:.2f}s")
                time.sleep(delay)
                continue
            raise HttpStatusError(error.code, error.geturl(), headers, body, str(error.reason)) from error
        except socket.timeout:
            last_error_summary = "timeout"
            if _should_retry(None, attempt, attempts):
                delay = _retry_delay(attempt)
                emit_diagnostic(f"Retrying {method} {url} after timeout in {delay:.2f}s")
                time.sleep(delay)
                continue
            raise
        except urllib.error.URLError as error:
            last_error_summary = f"network error: {error.reason}"
            if _should_retry(None, attempt, attempts):
                delay = _retry_delay(attempt)
                emit_diagnostic(f"Retrying {method} {url} after network error in {delay:.2f}s")
                time.sleep(delay)
                continue
            raise
    detail = f" Last failure: {last_error_summary}." if last_error_summary else ""
    raise RuntimeError(f"Exhausted retries for {method} {url}.{detail}")

from __future__ import annotations

import io
import urllib.error
import urllib.request
import unittest
from unittest.mock import patch

from dops.http import HttpResponse, HttpStatusError, _retry_after_seconds, default_user_agent, urlopen_with_retries


class HttpTests(unittest.TestCase):
    def test_default_user_agent_looks_like_cli_identifier(self) -> None:
        value = default_user_agent()
        self.assertIn("decisionops-cli/", value)
        self.assertIn("github.com/aidecisionops/cli", value)

    def test_urlopen_with_retries_retries_then_succeeds(self) -> None:
        request = urllib.request.Request("https://api.example.com/status", method="GET")

        class _Response:
            status = 200
            headers = {"content-type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def geturl(self) -> str:
                return "https://api.example.com/status"

            def read(self) -> bytes:
                return b"ok"

        attempts = [
            urllib.error.HTTPError(
                "https://api.example.com/status",
                429,
                "Too Many Requests",
                {"Retry-After": "0"},
                io.BytesIO(b"{}"),
            ),
            _Response(),
        ]

        def fake_urlopen(*args, **kwargs):
            outcome = attempts.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("time.sleep", return_value=None):
                response = urlopen_with_retries(request, timeout=1, context=None)
        self.assertIsInstance(response, HttpResponse)
        self.assertEqual(response.status, 200)

    def test_urlopen_with_retries_raises_http_status_error(self) -> None:
        request = urllib.request.Request("https://api.example.com/status", method="GET")
        error = urllib.error.HTTPError(
            "https://api.example.com/status",
            500,
            "Server Error",
            {"Retry-After": "0"},
            io.BytesIO(b"fail"),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with patch("time.sleep", return_value=None):
                with self.assertRaises(HttpStatusError) as raised:
                    urlopen_with_retries(request, timeout=1, context=None, max_attempts=0)
        self.assertEqual(raised.exception.status, 500)

    def test_retry_after_seconds_caps_large_values(self) -> None:
        self.assertEqual(_retry_after_seconds({"retry-after": "999999"}), 60.0)
        self.assertEqual(_retry_after_seconds({"retry-after": "30"}), 30.0)
        self.assertEqual(_retry_after_seconds({"retry-after": "0"}), 0.0)

from __future__ import annotations

import ssl
import unittest
from unittest.mock import patch

from dops.tls import create_ssl_context


class TlsTests(unittest.TestCase):
    def test_create_ssl_context_uses_certifi_when_available(self) -> None:
        create_ssl_context.cache_clear()
        with patch("certifi.where", return_value="/tmp/certifi.pem"):
            with patch("ssl.create_default_context", return_value=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as mocked:
                context = create_ssl_context()
        mocked.assert_called_once_with(cafile="/tmp/certifi.pem")
        self.assertIsInstance(context, ssl.SSLContext)

    def test_create_ssl_context_falls_back_without_certifi(self) -> None:
        create_ssl_context.cache_clear()
        import builtins

        original_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "certifi":
                raise ImportError("missing")
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import):
            with patch("ssl.create_default_context", return_value=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)) as mocked:
                context = create_ssl_context()
        mocked.assert_called_once_with()
        self.assertIsInstance(context, ssl.SSLContext)

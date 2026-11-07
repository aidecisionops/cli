from __future__ import annotations

import importlib
import os
import shutil
import tempfile
import unittest


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="dops-config-test-")
        self.original_config_path = os.environ.get("DECISIONOPS_CONFIG_PATH")
        self.original_http_max_retries = os.environ.get("DECISIONOPS_HTTP_MAX_RETRIES")
        self.original_http_backoff_seconds = os.environ.get("DECISIONOPS_HTTP_BACKOFF_SECONDS")
        import dops.config

        self.config_module = dops.config

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self.original_config_path is None:
            os.environ.pop("DECISIONOPS_CONFIG_PATH", None)
        else:
            os.environ["DECISIONOPS_CONFIG_PATH"] = self.original_config_path
        if self.original_http_max_retries is None:
            os.environ.pop("DECISIONOPS_HTTP_MAX_RETRIES", None)
        else:
            os.environ["DECISIONOPS_HTTP_MAX_RETRIES"] = self.original_http_max_retries
        if self.original_http_backoff_seconds is None:
            os.environ.pop("DECISIONOPS_HTTP_BACKOFF_SECONDS", None)
        else:
            os.environ["DECISIONOPS_HTTP_BACKOFF_SECONDS"] = self.original_http_backoff_seconds
        importlib.reload(self.config_module)

    def test_config_file_sets_defaults(self) -> None:
        config_path = os.path.join(self.temp_dir, "config.toml")
        with open(config_path, "w", encoding="utf8") as handle:
            handle.write(
                "\n".join(
                    [
                        'api_base_url = "https://config.example.com"',
                        "verbose = true",
                        "[oauth]",
                        'client_id = "config-client"',
                        'scopes = ["alpha", "beta"]',
                        "",
                    ]
                )
            )
        os.environ["DECISIONOPS_CONFIG_PATH"] = config_path
        config = importlib.reload(self.config_module)
        self.assertEqual(config.DEFAULT_API_BASE_URL, "https://config.example.com")
        self.assertEqual(config.DEFAULT_OAUTH_CLIENT_ID, "config-client")
        self.assertEqual(config.DEFAULT_OAUTH_SCOPES, ["alpha", "beta"])
        self.assertTrue(config.DEFAULT_VERBOSE)

    def test_default_oauth_scopes_include_admin_write(self) -> None:
        config = importlib.reload(self.config_module)
        self.assertIn("admin:read", config.DEFAULT_OAUTH_SCOPES)
        self.assertIn("admin:write", config.DEFAULT_OAUTH_SCOPES)

    def test_malformed_config_does_not_crash_reload(self) -> None:
        config_path = os.path.join(self.temp_dir, "config.toml")
        with open(config_path, "w", encoding="utf8") as handle:
            handle.write("verbose = [\n")
        os.environ["DECISIONOPS_CONFIG_PATH"] = config_path
        config = importlib.reload(self.config_module)
        self.assertIsNotNone(config.config_error())
        self.assertFalse(config.DEFAULT_VERBOSE)

    def test_invalid_env_integer_uses_default_and_reports_warning(self) -> None:
        os.environ["DECISIONOPS_HTTP_MAX_RETRIES"] = "abc"
        config = importlib.reload(self.config_module)
        self.assertEqual(config.DEFAULT_HTTP_MAX_RETRIES, 2)
        self.assertIn("Invalid integer for DECISIONOPS_HTTP_MAX_RETRIES", config.config_error() or "")

    def test_invalid_config_integer_uses_default_and_reports_warning(self) -> None:
        config_path = os.path.join(self.temp_dir, "config.toml")
        with open(config_path, "w", encoding="utf8") as handle:
            handle.write("[http]\nmax_retries = \"abc\"\n")
        os.environ["DECISIONOPS_CONFIG_PATH"] = config_path
        config = importlib.reload(self.config_module)
        self.assertEqual(config.DEFAULT_HTTP_MAX_RETRIES, 2)
        self.assertIn("http.max_retries", config.config_error() or "")

    def test_invalid_env_float_uses_default_and_reports_warning(self) -> None:
        os.environ["DECISIONOPS_HTTP_BACKOFF_SECONDS"] = "abc"
        config = importlib.reload(self.config_module)
        self.assertEqual(config.DEFAULT_HTTP_BACKOFF_SECONDS, 0.5)
        self.assertIn("Invalid number for DECISIONOPS_HTTP_BACKOFF_SECONDS", config.config_error() or "")

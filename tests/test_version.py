from __future__ import annotations

import unittest
from unittest.mock import patch

from dops import _version


class VersionTests(unittest.TestCase):
    def test_resolve_version_prefers_metadata_before_default(self) -> None:
        with patch("dops._version._version_from_build_file", return_value=None):
            with patch("dops._version._version_from_git", return_value=None):
                with patch("dops._version._version_from_metadata", return_value="0.1.0"):
                    with patch("dops._version.DEFAULT_VERSION", "0.1.11"):
                        self.assertEqual(_version.resolve_version(), "0.1.0")

    def test_resolve_version_uses_metadata_before_default(self) -> None:
        with patch("dops._version._version_from_build_file", return_value=None):
            with patch("dops._version._version_from_git", return_value=None):
                with patch("dops._version._version_from_metadata", return_value="0.1.99"):
                    with patch("dops._version.DEFAULT_VERSION", "0.1.11"):
                        self.assertEqual(_version.resolve_version(), "0.1.99")

    def test_version_from_git_ignores_non_version_commit_hashes(self) -> None:
        completed = type("Completed", (), {"stdout": "797a4ba\n"})()
        with patch("dops._version.subprocess.run", return_value=completed):
            self.assertIsNone(_version._version_from_git())

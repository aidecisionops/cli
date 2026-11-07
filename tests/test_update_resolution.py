from __future__ import annotations

import unittest
from unittest.mock import patch

from dops.command_groups.update import _resolve_target_release


class UpdateResolutionTests(unittest.TestCase):
    def test_resolve_target_release_keeps_explicit_version(self) -> None:
        self.assertEqual(_resolve_target_release("v0.1.5"), "v0.1.5")

    def test_resolve_target_release_extracts_latest_tag(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def geturl(self) -> str:
                return "https://github.com/aidecisionops/cli/releases/download/v0.1.5/dops-darwin-arm64"

        with patch("urllib.request.urlopen", return_value=_Response()):
            self.assertEqual(_resolve_target_release("latest"), "v0.1.5")

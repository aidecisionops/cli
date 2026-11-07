from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dops.platforms import load_platforms


class PlatformTests(unittest.TestCase):
    def test_load_platforms_reports_invalid_platform_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "codex.toml"
            file_path.write_text("id = [\n", encoding="utf8")

            with self.assertRaises(RuntimeError) as raised:
                load_platforms(temp_dir)

        self.assertIn("DecisionOps platform definition is invalid", str(raised.exception))
        self.assertIn("codex.toml", str(raised.exception))
        self.assertIn("Refresh the skill bundle", str(raised.exception))

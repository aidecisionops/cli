from __future__ import annotations

import unittest
from pathlib import Path

from dops.installers.templates import render_powershell_installer, render_shell_installer

REPO_ROOT = Path(__file__).resolve().parents[1]


class InstallerTemplateTests(unittest.TestCase):
    def test_shell_installer_matches_checked_in_file(self) -> None:
        self.assertEqual((REPO_ROOT / "install" / "install.sh").read_text(encoding="utf8"), render_shell_installer())

    def test_powershell_installer_matches_checked_in_file(self) -> None:
        self.assertEqual((REPO_ROOT / "install" / "install.ps1").read_text(encoding="utf8"), render_powershell_installer())

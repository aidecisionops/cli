from __future__ import annotations

import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dops.command_groups.update import _installed_binary_path, run_update


class UpdateTests(unittest.TestCase):
    def test_installed_binary_path_defaults_to_user_bin(self) -> None:
        with patch("dops.command_groups.update.Path.home", return_value=Path("/tmp/example-home")):
            path = _installed_binary_path(None)
        self.assertEqual(path, Path("/tmp/example-home/.dops/bin") / path.name)

    def test_run_update_reports_installed_binary_and_shell_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir)
            binary = _installed_binary_path(str(install_dir))
            binary.write_text("#!/bin/sh\necho 0.1.3\n", encoding="utf-8")
            if os.name != "nt":
                binary.chmod(0o755)

            stdout = io.StringIO()
            flags = argparse.Namespace(version=None, install_dir=str(install_dir))

            def fake_run(command, **kwargs):
                if isinstance(command, list) and command and command[0] == str(binary):
                    return argparse.Namespace(returncode=0, stdout="0.1.3\n")
                return argparse.Namespace(returncode=0)

            with redirect_stdout(stdout):
                with patch("dops.command_groups.update.subprocess.run", side_effect=fake_run):
                    with patch("dops.command_groups.update.shutil.which", return_value="/usr/local/bin/dops"):
                        run_update(flags)

            output = stdout.getvalue()
            self.assertIn("Installed binary:", output)
            self.assertIn(str(binary), output)
            self.assertIn("0.1.3", output)
            self.assertIn("Current shell still resolves `dops`", output)

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dops.fileio import atomic_copy_dir, atomic_write_text


class FileIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="dops-fileio-test-")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_atomic_write_text_preserves_original_error_when_cleanup_fails(self) -> None:
        file_path = Path(self.temp_dir) / "config.toml"

        def fail_replace(src: str, dst: str) -> None:
            raise RuntimeError("replace failed")

        with (
            mock.patch("dops.fileio.os.replace", side_effect=fail_replace),
            mock.patch("dops.fileio.os.unlink", side_effect=PermissionError("cleanup failed")),
        ):
            with self.assertRaises(RuntimeError) as raised:
                atomic_write_text(file_path, "hello\n", encoding="utf8")

        self.assertEqual(str(raised.exception), "replace failed")

    def test_atomic_copy_dir_round_trips_content(self) -> None:
        source = Path(self.temp_dir) / "source"
        source.mkdir()
        (source / "file.txt").write_text("hello\n", encoding="utf8")
        target = Path(self.temp_dir) / "target"
        atomic_copy_dir(source, target)
        self.assertTrue((target / "file.txt").exists())
        self.assertEqual((target / "file.txt").read_text(encoding="utf8"), "hello\n")

    def test_atomic_copy_dir_replaces_existing_target(self) -> None:
        source = Path(self.temp_dir) / "source"
        source.mkdir()
        (source / "new.txt").write_text("new\n", encoding="utf8")
        target = Path(self.temp_dir) / "target"
        target.mkdir()
        (target / "old.txt").write_text("old\n", encoding="utf8")
        atomic_copy_dir(source, target)
        self.assertTrue((target / "new.txt").exists())
        self.assertFalse((target / "old.txt").exists())

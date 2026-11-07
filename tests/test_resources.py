from __future__ import annotations

import io
import importlib
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from dops.http import HttpResponse


def _skill_repo_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("skill-main/platforms/codex.toml", 'id = "codex"\ndisplay_name = "Codex"\n')
        archive.writestr("skill-main/decision-ops/SKILL.md", "# DecisionOps\n")
    return buffer.getvalue()


def _monorepo_skill_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("decisionops-main/skill/platforms/codex.toml", 'id = "codex"\ndisplay_name = "Codex"\n')
        archive.writestr("decisionops-main/skill/decision-ops/SKILL.md", "# DecisionOps\n")
    return buffer.getvalue()


class ResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="dops-resources-test-")
        self.original_home = os.environ.get("DECISIONOPS_HOME")
        os.environ["DECISIONOPS_HOME"] = self.temp_dir
        import dops.resources

        self.resources = importlib.reload(dops.resources)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self.original_home is None:
            os.environ.pop("DECISIONOPS_HOME", None)
        else:
            os.environ["DECISIONOPS_HOME"] = self.original_home
        importlib.reload(self.resources)

    def test_find_platforms_dir_downloads_and_caches_skill_repo(self) -> None:
        with patch.object(
            self.resources,
            "urlopen_with_retries",
            return_value=HttpResponse(
                url="https://codeload.github.com/aidecisionops/skill/zip/refs/heads/main",
                status=200,
                headers={},
                body=_skill_repo_zip(),
            ),
        ) as mocked:
            platforms_dir = Path(self.resources.find_platforms_dir())

        self.assertTrue((platforms_dir / "codex.toml").exists())
        self.assertTrue((Path(self.temp_dir) / "resources" / "skill-repo" / "repo" / "decision-ops" / "SKILL.md").exists())
        mocked.assert_called_once()

    def test_find_platforms_dir_supports_monorepo_skill_subdir_archive(self) -> None:
        with patch.object(
            self.resources,
            "urlopen_with_retries",
            return_value=HttpResponse(
                url="https://codeload.github.com/aidecisionops/skill/zip/refs/heads/main",
                status=200,
                headers={},
                body=_monorepo_skill_zip(),
            ),
        ) as mocked:
            platforms_dir = Path(self.resources.find_platforms_dir())

        self.assertTrue((platforms_dir / "codex.toml").exists())
        self.assertTrue((Path(self.temp_dir) / "resources" / "skill-repo" / "repo" / "decision-ops" / "SKILL.md").exists())
        mocked.assert_called_once()

    def test_find_skill_source_dir_reuses_cached_skill_repo_without_redownloading(self) -> None:
        with patch.object(
            self.resources,
            "urlopen_with_retries",
            return_value=HttpResponse(
                url="https://codeload.github.com/aidecisionops/skill/zip/refs/heads/main",
                status=200,
                headers={},
                body=_skill_repo_zip(),
            ),
        ):
            self.resources.ensure_skill_repo_cache()

        with patch.object(self.resources, "urlopen_with_retries", side_effect=AssertionError("should not redownload")):
            skill_dir = Path(self.resources.find_skill_source_dir())

        self.assertTrue((skill_dir / "SKILL.md").exists())

    def test_ensure_skill_repo_cache_refreshes_when_cached_manifest_ref_differs(self) -> None:
        cache_dir = Path(self.temp_dir) / "resources" / "skill-repo" / "repo"
        (cache_dir / "platforms").mkdir(parents=True, exist_ok=True)
        (cache_dir / "decision-ops").mkdir(parents=True, exist_ok=True)
        (cache_dir / "platforms" / "codex.toml").write_text('id = "codex"\ndisplay_name = "Codex"\n', encoding="utf8")
        (cache_dir / "decision-ops" / "SKILL.md").write_text("# Old cache\n", encoding="utf8")
        (Path(self.temp_dir) / "resources" / "skill-repo" / "manifest.json").write_text(
            '{"repo_url":"https://github.com/aidecisionops/skill.git","ref":"old-ref"}\n',
            encoding="utf8",
        )

        with patch.object(
            self.resources,
            "urlopen_with_retries",
            return_value=HttpResponse(
                url="https://codeload.github.com/aidecisionops/skill/zip/refs/heads/main",
                status=200,
                headers={},
                body=_skill_repo_zip(),
            ),
        ) as mocked:
            platforms_dir = Path(self.resources.find_platforms_dir())

        self.assertTrue((platforms_dir / "codex.toml").exists())
        mocked.assert_called_once()

    def test_resolve_local_skill_repo_supports_decision_ops_subdir(self) -> None:
        repo_root = Path(self.temp_dir) / "skill-repo"
        (repo_root / "platforms").mkdir(parents=True, exist_ok=True)
        (repo_root / "platforms" / "codex.toml").write_text('id = "codex"\ndisplay_name = "Codex"\n', encoding="utf8")
        bundle_dir = repo_root / "decision-ops"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "SKILL.md").write_text("# DecisionOps\n", encoding="utf8")

        platforms_dir, skill_dir = self.resources.resolve_local_skill_repo(str(bundle_dir))

        self.assertEqual(Path(platforms_dir).resolve(), (repo_root / "platforms").resolve())
        self.assertEqual(Path(skill_dir).resolve(), bundle_dir.resolve())

    def test_download_skill_repo_rejects_zip_path_traversal(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../../etc/evil.txt", "pwned")
            archive.writestr("skill-main/platforms/codex.toml", 'id = "codex"\ndisplay_name = "Codex"\n')
            archive.writestr("skill-main/decision-ops/SKILL.md", "# DecisionOps\n")
        with patch.object(
            self.resources,
            "urlopen_with_retries",
            return_value=HttpResponse(
                url="https://codeload.github.com/aidecisionops/skill/zip/refs/heads/main",
                status=200,
                headers={},
                body=buffer.getvalue(),
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                self.resources._download_skill_repo(
                    "https://github.com/aidecisionops/skill.git", "main"
                )
            self.assertIn("path traversal", str(raised.exception))

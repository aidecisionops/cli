from __future__ import annotations

import unittest
from unittest.mock import patch

from dops.git import infer_default_branch


class GitTests(unittest.TestCase):
    def test_infer_default_branch_prefers_remote_head(self) -> None:
        with patch("dops.git.git_output", side_effect=lambda _rp, *args: "refs/remotes/origin/main" if "symbolic-ref" in args else "feature-branch"):
            self.assertEqual(infer_default_branch("/tmp/repo"), "main")

    def test_infer_default_branch_falls_back_to_current_branch(self) -> None:
        call_count = 0

        def fake_git_output(_rp: str, *args: str) -> str:
            nonlocal call_count
            call_count += 1
            if "symbolic-ref" in args:
                raise RuntimeError("not set")
            return "develop"

        with patch("dops.git.git_output", side_effect=fake_git_output):
            self.assertEqual(infer_default_branch("/tmp/repo"), "develop")

    def test_infer_default_branch_defaults_to_main(self) -> None:
        with patch("dops.git.git_output", side_effect=RuntimeError("no git")):
            self.assertEqual(infer_default_branch("/tmp/repo"), "main")

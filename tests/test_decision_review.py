from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
import os
import subprocess

from dops.decision_review import review_decision_paths


class DecisionReviewTests(TestCase):
    def test_directory_references_report_changes_and_use_filtered_acceptance_history(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)

            def git(*args: str, date: str | None = None) -> None:
                env = dict(os.environ)
                if date:
                    env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
                subprocess.run(["git", "-C", tmp, *args], check=True, capture_output=True, env=env)

            git("init")
            git("config", "user.name", "DecisionOps Test")
            git("config", "user.email", "test@example.com")
            (repo / "docs").mkdir()
            (repo / "docs" / "removed.md").write_text("removed\n")
            (repo / "docs" / "changed.md").write_text("baseline\n")
            git("add", ".")
            git("commit", "-m", "Baseline", date="2020-01-01T00:00:00Z")
            (repo / "docs" / "removed.md").unlink()
            (repo / "docs" / "changed.md").write_text("baseline\n" + "new\n" * 100)
            git("add", "-A")
            git("commit", "-m", "Change referenced directory")
            client = Mock()
            client.request.return_value = {"history": [{
                "changeType": "status_change",
                "snapshot": {"status": "accepted"},
                "createdAt": datetime(2020, 1, 2, tzinfo=timezone.utc).isoformat(),
            }]}
            findings = review_decision_paths(client, tmp, [{
                "id": "DEC-2020-1", "status": "Accepted", "related": ["docs", "docs/changed.md"],
            }])
            self.assertEqual([(item["flag"], item["path"]) for item in findings], [
                ("file_deleted", "docs/removed.md"),
                ("file_heavily_changed", "docs/changed.md"),
            ])
            client.request.assert_called_once_with("GET", "/v1/decisions/DEC-2020-1/history?accepted_only=true&limit=1")

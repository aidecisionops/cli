from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote

from .api_client import DopsClient
from .git import git_output


def project_decisions(client: DopsClient) -> list[dict]:
    if not client.org_id or not client.project_id:
        raise RuntimeError("Bind this checkout to a DecisionOps project before reviewing its decisions.")

    decisions: list[dict] = []
    offset = 0
    while True:
        page = client.request("POST", "/v1/decision-ops/search", {
            "org_id": client.org_id,
            "project_id": client.project_id,
            "limit": 100,
            "offset": offset,
        })
        if not isinstance(page, dict) or not isinstance(page.get("items"), list) or "next_offset" not in page:
            raise RuntimeError("The server did not return a paged project decision list.")
        decisions.extend(page["items"])
        next_offset = page["next_offset"]
        if next_offset is None:
            return decisions
        if type(next_offset) is not int or next_offset <= offset:
            raise RuntimeError("The server returned an invalid next decision page.")
        offset = next_offset


def _repo_paths(decision: dict) -> list[str]:
    paths = []
    for reference in decision.get("related", []):
        path = PurePosixPath(reference)
        if "://" not in reference and not path.is_absolute() and ".." not in path.parts and path.parts:
            paths.append(str(path))
    return list(dict.fromkeys(paths))


def _renames(repo: str, base: str) -> dict[str, str]:
    fields = iter(git_output(repo, "diff", "--name-status", "-z", "--find-renames", base, "HEAD").split("\0"))
    renames = {}
    for status in fields:
        if not status:
            break
        old = next(fields)
        if status.startswith("R"):
            renames[old] = next(fields)
    return renames


def review_decision_paths(client: DopsClient, repo: str, decisions: list[dict]) -> list[dict]:
    findings = []
    for decision in decisions:
        if decision["status"] != "Accepted" or not (paths := _repo_paths(decision)):
            continue
        decision_id = decision["id"]
        history = client.request("GET", f"/v1/decisions/{quote(decision_id, safe='')}/history?limit=100")
        if not isinstance(history, dict) or not isinstance(history.get("history"), list):
            raise RuntimeError(f"Could not read acceptance history for {decision_id}.")

        accepted = next((revision for revision in history["history"]
                         if revision.get("changeType") == "status_change"
                         and revision.get("snapshot", {}).get("status") == "accepted"), None)
        if not accepted:
            findings.append({"id": decision_id, "flag": "acceptance_history_missing"})
            continue
        base = git_output(repo, "rev-list", "-1", f"--before={accepted['createdAt']}", "HEAD")
        if not base:
            findings.append({"id": decision_id, "flag": "acceptance_commit_missing"})
            continue
        renames = None
        for path in paths:
            literal = ["--literal-pathspecs", "ls-tree", "-r", "--name-only", "-z"]
            if path not in git_output(repo, *literal, base, "--", path).split("\0"):
                continue
            finding = {"id": decision_id, "path": path}
            if path not in git_output(repo, *literal, "HEAD", "--", path).split("\0"):
                if renames is None:
                    renames = _renames(repo, base)
                if path in renames:
                    findings.append({**finding, "flag": "file_renamed", "new_path": renames[path]})
                else:
                    findings.append({**finding, "flag": "file_deleted"})
                continue
            stats = git_output(repo, "--literal-pathspecs", "diff", "--numstat", "--no-renames", base, "HEAD", "--", path)
            if stats:
                added, deleted, _ = stats.split("\t", 2)
                try:
                    changed = int(added) + int(deleted)
                except ValueError:
                    continue
                if changed >= 100:
                    findings.append({**finding, "flag": "file_heavily_changed", "lines_changed": changed})
    return findings

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

DRY_RUN=0
AUTO_CONFIRM=0
WAIT_FOR_ACTIONS=1
ALLOW_NON_MAIN=0
VERSION_ARG=""

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

die() {
  printf '\n[ERROR] %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./publish-new-version.sh [options]

Options:
  --version <version>   Publish a specific version (for example: 0.1.13 or v0.1.13)
  --yes                 Skip the interactive confirmation prompt
  --dry-run             Print the planned actions without mutating files, git state, or GitHub
  --no-wait             Do not wait for GitHub Actions to finish
  --allow-non-main      Allow publishing from a branch other than main
  -h, --help            Show this help text

Default behavior:
  - infers the next patch version from pyproject.toml
  - updates local version references
  - runs the full unittest suite
  - commits the release, pushes the branch, creates a tag, and pushes the tag
  - waits for the Release CLI and CLI CI workflows to complete
  - writes a markdown release report to build/release-reports/<tag>.md
EOF
}

normalize_version() {
  local value="$1"
  printf '%s\n' "${value#v}"
}

python_cmd() {
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    printf '%s\n' "$ROOT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    die "Python is required."
  fi
}

run_cmd() {
  log "$*"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  "$@"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

current_version() {
  "$PYTHON" - <<'PY'
from pathlib import Path
import tomllib

data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
print(data["project"]["version"])
PY
}

next_patch_version() {
  "$PYTHON" - "$1" <<'PY'
import sys

parts = sys.argv[1].split(".")
if len(parts) != 3 or not all(part.isdigit() for part in parts):
    raise SystemExit("Expected a simple semantic version like 0.1.12")
major, minor, patch = map(int, parts)
print(f"{major}.{minor}.{patch + 1}")
PY
}

update_version_references() {
  local old_version="$1"
  local new_version="$2"
  "$PYTHON" - "$old_version" "$new_version" <<'PY'
from pathlib import Path
import re
import sys

old = sys.argv[1]
new = sys.argv[2]

def replace_file(path: str, replacer) -> None:
    file_path = Path(path)
    original = file_path.read_text(encoding="utf-8")
    updated = replacer(original)
    if updated == original:
        raise SystemExit(f"Expected to update {path}, but no changes were made.")
    file_path.write_text(updated, encoding="utf-8")

replace_file(
    "pyproject.toml",
    lambda text: re.sub(
        r'(?m)^version = "' + re.escape(old) + r'"$',
        f'version = "{new}"',
        text,
        count=1,
    ),
)
replace_file(
    "dops/_version.py",
    lambda text: re.sub(
        r'(?m)^DEFAULT_VERSION = "' + re.escape(old) + r'"$',
        f'DEFAULT_VERSION = "{new}"',
        text,
        count=1,
    ),
)
replace_file(
    "dops/command_groups/update.py",
    lambda text: text.replace(f"v{old}", f"v{new}"),
)
replace_file(
    "README.md",
    lambda text: text.replace(f"v{old}", f"v{new}"),
)
PY
}

confirm() {
  if [[ "$AUTO_CONFIRM" -eq 1 ]]; then
    return 0
  fi
  printf '\nProceed with release %s from branch %s? [y/N] ' "$TAG" "$BRANCH"
  read -r answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

wait_for_run_id() {
  local workflow_name="$1"
  local expected_ref="$2"
  local expected_sha="$3"
  local deadline=$((SECONDS + 300))

  while (( SECONDS < deadline )); do
    local json
    json="$(gh run list --workflow "$workflow_name" --limit 20 --json databaseId,headBranch,headSha,status,conclusion,url 2>/dev/null || printf '[]')"
    local run_id
    run_id="$("$PYTHON" - "$json" "$expected_ref" "$expected_sha" <<'PY'
import json
import sys

runs = json.loads(sys.argv[1])
expected_ref = sys.argv[2]
expected_sha = sys.argv[3]
for run in runs:
    if run.get("headBranch") == expected_ref and run.get("headSha") == expected_sha:
        print(run["databaseId"])
        raise SystemExit(0)
raise SystemExit(1)
PY
)" || true
    if [[ -n "$run_id" ]]; then
      printf '%s\n' "$run_id"
      return 0
    fi
    sleep 5
  done

  return 1
}

render_report() {
  local report_path="$1"
  local release_run_json="$2"
  local ci_run_json="$3"
  local release_json="$4"

  "$PYTHON" - "$report_path" "$TAG" "$VERSION" "$BRANCH" "$COMMIT_SHA" "$release_run_json" "$ci_run_json" "$release_json" <<'PY'
from pathlib import Path
import json
import sys

report_path = Path(sys.argv[1])
tag = sys.argv[2]
version = sys.argv[3]
branch = sys.argv[4]
commit_sha = sys.argv[5]
release_run = json.loads(sys.argv[6])
ci_run = json.loads(sys.argv[7])
release = json.loads(sys.argv[8])

def job_lines(run: dict) -> str:
    jobs = run.get("jobs") or []
    if not jobs:
        return "- No job data returned."
    lines = []
    for job in jobs:
        lines.append(f"- {job['name']}: {job.get('conclusion') or job.get('status')}")
    return "\n".join(lines)

def asset_lines(release_data: dict) -> str:
    assets = release_data.get("assets") or []
    if not assets:
        return "- No release assets found."
    lines = []
    for asset in assets:
        lines.append(
            f"- [{asset['name']}]({asset['url']}) ({asset['size']} bytes, downloads: {asset['downloadCount']})"
        )
    return "\n".join(lines)

content = f"""# Release Report for {tag}

- Version: `{version}`
- Tag: `{tag}`
- Branch: `{branch}`
- Commit: `{commit_sha}`

## Local Verification

- Command: `.venv/bin/python -m unittest discover -s tests -v`
- Status: passed before publish

## Release Workflow

- URL: {release_run.get('url')}
- Status: {release_run.get('status')}
- Conclusion: {release_run.get('conclusion')}

{job_lines(release_run)}

## Main CI Workflow

- URL: {ci_run.get('url')}
- Status: {ci_run.get('status')}
- Conclusion: {ci_run.get('conclusion')}

{job_lines(ci_run)}

## GitHub Release

- URL: {release.get('url')}
- Draft: {release.get('isDraft')}
- Prerelease: {release.get('isPrerelease')}

{asset_lines(release)}
"""

report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(content, encoding="utf-8")
print(report_path)
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      [[ $# -ge 2 ]] || die "--version requires a value"
      VERSION_ARG="$2"
      shift 2
      ;;
    --yes)
      AUTO_CONFIRM=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --no-wait)
      WAIT_FOR_ACTIONS=0
      shift
      ;;
    --allow-non-main)
      ALLOW_NON_MAIN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

require_command git
require_command gh

PYTHON="$(python_cmd)"
BRANCH="$(git branch --show-current)"
[[ -n "$BRANCH" ]] || die "Could not determine the current branch."

if [[ "$ALLOW_NON_MAIN" -ne 1 && "$BRANCH" != "main" ]]; then
  die "Releases must run from main by default. Re-run with --allow-non-main if needed."
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  log "Checking GitHub authentication"
  gh auth status >/dev/null

  log "Fetching latest main from origin"
  git fetch origin main --tags
  if [[ "$BRANCH" == "main" ]]; then
    git pull --ff-only origin main
  fi
fi

CURRENT_VERSION="$(current_version)"
VERSION="$(normalize_version "${VERSION_ARG:-$(next_patch_version "$CURRENT_VERSION")}")"
TAG="v$VERSION"

[[ "$VERSION" != "$CURRENT_VERSION" ]] || die "Target version matches the current version ($CURRENT_VERSION)."
git rev-parse "$TAG" >/dev/null 2>&1 && die "Tag already exists locally: $TAG"
git ls-remote --tags origin "$TAG" | grep -q "$TAG" && die "Tag already exists on origin: $TAG"

log "Current version: $CURRENT_VERSION"
log "Target version:  $VERSION"
log "Release tag:     $TAG"
log "Branch:          $BRANCH"

log "Updating version references"
if [[ "$DRY_RUN" -eq 0 ]]; then
  update_version_references "$CURRENT_VERSION" "$VERSION"
fi

log "Running local tests"
if [[ "$DRY_RUN" -eq 0 ]]; then
  "$PYTHON" -m unittest discover -s tests -v
fi

log "Git status before release"
git status --short

confirm || die "Release cancelled."

run_cmd git add -A
run_cmd git commit -m "Release $TAG"
run_cmd git push origin "$BRANCH"
run_cmd git tag "$TAG"
run_cmd git push origin "$TAG"

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Dry run complete. No files, git state, or GitHub resources were changed."
  exit 0
fi

COMMIT_SHA="$(git rev-parse HEAD)"
RELEASE_REPORT_PATH="build/release-reports/${TAG}.md"

if [[ "$WAIT_FOR_ACTIONS" -eq 0 ]]; then
  log "Skipping workflow wait because --no-wait was provided."
  exit 0
fi

log "Locating Release CLI workflow for $TAG"
RELEASE_RUN_ID="$(wait_for_run_id "Release CLI" "$TAG" "$COMMIT_SHA")" || die "Could not find the Release CLI workflow run for $TAG."
log "Watching Release CLI run $RELEASE_RUN_ID"
gh run watch "$RELEASE_RUN_ID" --interval 15 || true

log "Locating CLI CI workflow for $COMMIT_SHA"
CI_RUN_ID="$(wait_for_run_id "CLI CI" "$BRANCH" "$COMMIT_SHA")" || die "Could not find the CLI CI workflow run for $COMMIT_SHA."
log "Watching CLI CI run $CI_RUN_ID"
gh run watch "$CI_RUN_ID" --interval 15 || true

log "Fetching workflow and release metadata"
RELEASE_RUN_JSON="$(gh run view "$RELEASE_RUN_ID" --json status,conclusion,url,jobs)"
CI_RUN_JSON="$(gh run view "$CI_RUN_ID" --json status,conclusion,url,jobs)"
RELEASE_JSON="$(gh release view "$TAG" --json name,tagName,url,isDraft,isPrerelease,assets 2>/dev/null || printf '{"name": null, "tagName": "%s", "url": null, "isDraft": null, "isPrerelease": null, "assets": []}' "$TAG")"

REPORT_PATH="$(render_report "$RELEASE_REPORT_PATH" "$RELEASE_RUN_JSON" "$CI_RUN_JSON" "$RELEASE_JSON")"

RELEASE_CONCLUSION="$("$PYTHON" - "$RELEASE_RUN_JSON" <<'PY'
import json
import sys
print(json.loads(sys.argv[1]).get("conclusion") or "")
PY
)"
CI_CONCLUSION="$("$PYTHON" - "$CI_RUN_JSON" <<'PY'
import json
import sys
print(json.loads(sys.argv[1]).get("conclusion") or "")
PY
)"

log "Release complete"
printf '\n'
printf 'Version:      %s\n' "$VERSION"
printf 'Tag:          %s\n' "$TAG"
printf 'Commit:       %s\n' "$COMMIT_SHA"
printf 'Release run:  %s\n' "https://github.com/aidecisionops/cli/actions/runs/$RELEASE_RUN_ID"
printf 'CI run:       %s\n' "https://github.com/aidecisionops/cli/actions/runs/$CI_RUN_ID"
printf 'Release page: %s\n' "https://github.com/aidecisionops/cli/releases/tag/$TAG"
printf 'Report:       %s\n' "$REPORT_PATH"

[[ "$RELEASE_CONCLUSION" == "success" ]] || die "Release workflow did not succeed. See $REPORT_PATH"
[[ "$CI_CONCLUSION" == "success" ]] || die "CLI CI workflow did not succeed. See $REPORT_PATH"

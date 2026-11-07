"""Verify IDE MCP configs and probe the DecisionOps MCP endpoint.

Catches the "installed but wrong" class of failures where `dops install`
thinks everything is fine because files exist, but the IDE is actually
pointing at a stale URL, has the entry disabled, or can't reach the
remote MCP server with the current auth.
"""

from __future__ import annotations

import json
import socket
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .http import HttpStatusError, default_user_agent, urlopen_with_retries
from .tls import create_ssl_context


@dataclass
class McpEntryReport:
    """Diagnostic for a single IDE's MCP config entry.

    `issues` is the human-readable rollup; callers feed it into the
    doctor report so users see concrete "what to do next" text rather
    than having to interpret structured fields.
    """

    config_path: str
    format: str
    server_name: str
    expected_url: str
    config_exists: bool
    parse_error: str | None = None
    entry_found: bool = False
    entry_url: str | None = None
    entry_enabled: bool | None = None
    url_matches: bool = False
    bearer_token: str | None = None
    competing_entries: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return (
            self.config_exists
            and self.parse_error is None
            and self.entry_found
            and self.url_matches
            and self.entry_enabled is not False
        )

    def short_status(self) -> str:
        if not self.config_exists:
            return "not configured"
        if self.parse_error:
            return "parse error"
        if not self.entry_found:
            return "entry missing"
        if not self.url_matches:
            return f"wrong url ({self.entry_url})"
        if self.entry_enabled is False:
            return "disabled"
        return "ok"


def _mcp_only_install_command(platform_id: str | None) -> str:
    """Tell users exactly how to fix a missing/wrong MCP entry without
    re-running skill and manifest work that is probably already correct.

    The existing `--skip-skill --skip-manifest` flags on `dops install`
    already do this; doctor just needs to point at the right incantation
    with the platform name baked in.
    """
    target = platform_id or "<platform>"
    return f"dops install {target} --skip-skill --skip-manifest"


def inspect_mcp_entry(
    *,
    config_path: str,
    fmt: str,
    root_key: str | None,
    server_name: str,
    expected_url: str,
    platform_id: str | None = None,
) -> McpEntryReport:
    report = McpEntryReport(
        config_path=config_path,
        format=fmt,
        server_name=server_name,
        expected_url=expected_url,
        config_exists=Path(config_path).exists(),
    )
    fix_hint = _mcp_only_install_command(platform_id)
    if not report.config_exists:
        report.issues.append(
            f"MCP config not present at {config_path}. Run `{fix_hint}`."
        )
        return report
    try:
        raw = Path(config_path).read_text(encoding="utf8")
    except (OSError, UnicodeDecodeError) as error:
        report.parse_error = f"Cannot read {config_path}: {error}"
        report.issues.append(report.parse_error)
        return report

    if fmt == "codex_toml":
        _inspect_codex_toml(raw, report)
    elif fmt == "json_map":
        _inspect_json_map(raw, root_key or "mcpServers", report)
    else:
        report.issues.append(f"Unsupported MCP config format: {fmt}")
        return report

    if report.entry_found:
        if not report.url_matches:
            report.issues.append(
                f"MCP entry `{server_name}` in {config_path} points to {report.entry_url}, "
                f"expected {expected_url}. Run `{fix_hint}` to repair."
            )
        if report.entry_enabled is False:
            # Reinstall is still the quickest remediation — the new writer
            # replaces the block with a minimal `url = "..."` entry, which
            # defaults to enabled in every supported IDE.
            report.issues.append(
                f"MCP entry `{server_name}` in {config_path} is disabled. Run `{fix_hint}` "
                "to rewrite the entry, or flip `enabled = true` by hand."
            )
    else:
        suffix = ""
        if report.competing_entries:
            suffix = (
                f" Another entry ({', '.join(report.competing_entries)}) already points to "
                f"{expected_url}; consider removing it to avoid confusion."
            )
        report.issues.append(
            f"MCP entry `{server_name}` missing from {config_path}. Run `{fix_hint}`.{suffix}"
        )
    return report


def _normalize_url(value: str | None) -> str:
    return (value or "").rstrip("/")


def _extract_bearer_token(candidate: dict) -> str | None:
    for header_key in ("http_headers", "headers"):
        headers = candidate.get(header_key)
        if not isinstance(headers, dict):
            continue
        for key, value in headers.items():
            if str(key).lower() == "authorization" and isinstance(value, str):
                cleaned = value.strip()
                if cleaned.lower().startswith("bearer "):
                    token = cleaned[7:].strip()
                    if token:
                        return token
    return None


def _inspect_codex_toml(raw: str, report: McpEntryReport) -> None:
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as error:
        report.parse_error = f"Invalid TOML: {error}"
        report.issues.append(report.parse_error)
        return
    servers = data.get("mcp_servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        report.issues.append("`mcp_servers` table is missing or malformed")
        return
    expected = _normalize_url(report.expected_url)
    for name, candidate in servers.items():
        if not isinstance(candidate, dict):
            continue
        if name == report.server_name:
            report.entry_found = True
            url = candidate.get("url")
            report.entry_url = str(url) if url else None
            if "enabled" in candidate:
                report.entry_enabled = bool(candidate.get("enabled"))
            else:
                report.entry_enabled = True
            report.url_matches = _normalize_url(report.entry_url) == expected
            report.bearer_token = _extract_bearer_token(candidate)
        elif _normalize_url(str(candidate.get("url", ""))) == expected:
            report.competing_entries.append(str(name))


def _inspect_json_map(raw: str, root_key: str, report: McpEntryReport) -> None:
    stripped = raw.strip()
    if not stripped:
        report.issues.append(f"Config file is empty; no `{root_key}` block")
        return
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as error:
        report.parse_error = f"Invalid JSON: {error}"
        report.issues.append(report.parse_error)
        return
    root = data.get(root_key) if isinstance(data, dict) else None
    if not isinstance(root, dict):
        report.issues.append(f"`{root_key}` block is missing or not an object")
        return
    expected = _normalize_url(report.expected_url)
    for name, candidate in root.items():
        if not isinstance(candidate, dict):
            continue
        if name == report.server_name:
            report.entry_found = True
            url = candidate.get("url")
            report.entry_url = str(url) if url else None
            if "disabled" in candidate:
                report.entry_enabled = not bool(candidate.get("disabled"))
            elif "enabled" in candidate:
                report.entry_enabled = bool(candidate.get("enabled"))
            else:
                report.entry_enabled = True
            report.url_matches = _normalize_url(report.entry_url) == expected
            report.bearer_token = _extract_bearer_token(candidate)
        elif _normalize_url(str(candidate.get("url", ""))) == expected:
            report.competing_entries.append(str(name))


@dataclass
class ApiAuthProbeResult:
    """Does the CLI's stored token actually authenticate with the API?

    Hits a cheap REST endpoint the CLI token is *supposed* to work with
    (`/v1/auth/me`). Separate from the MCP endpoint, which uses a
    different audience — see `McpReachabilityResult` below.
    """

    status: int
    error: str | None = None

    @property
    def reachable(self) -> bool:
        return self.status == 200 and self.error is None

    def short_status(self) -> str:
        if self.reachable:
            return "ok"
        if self.status == 401:
            return "unauthorized — run `dops login --force`"
        if self.status == 403:
            return "forbidden — token missing required scope; run `dops login --force`"
        if self.status == 0:
            return f"unreachable — {self.error}"
        return f"error {self.status}: {self.error}"


@dataclass
class McpReachabilityResult:
    """Is the MCP server process up and responding?

    CLI tokens cannot authenticate against `/mcp` (different OAuth
    audience — IDE MCP clients obtain their own tokens via the
    protected-resource flow), so we probe without auth and treat a
    401 "Missing bearer token" response as proof the server is alive.
    """

    status: int
    error: str | None = None

    @property
    def reachable(self) -> bool:
        # A 401 with the "missing bearer token" shape is the expected
        # response for an unauthenticated probe — it means the server
        # handler is alive and enforcing auth.
        return self.status in (200, 401) and self.error is None

    def short_status(self) -> str:
        if self.reachable:
            return "server reachable (IDE MCP client handles its own auth)"
        if self.status == 0:
            return f"unreachable — {self.error}"
        return f"unexpected status {self.status}: {self.error}"


def probe_api_auth(*, api_base_url: str, token: str, path: str = "/v1/auth/me", timeout: int = 10) -> ApiAuthProbeResult:
    url = f"{api_base_url.rstrip('/')}{path}"
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {token}",
        "user-agent": default_user_agent(),
    }
    request = urllib.request.Request(url, method="GET", headers=headers)
    try:
        response = urlopen_with_retries(request, timeout=timeout, context=create_ssl_context())
    except HttpStatusError as error:
        message = _read_error_message(error)
        if path == "/v1/auth/me" and error.status == 403 and "user session required" in (message or "").lower():
            return probe_api_auth(api_base_url=api_base_url, token=token, path="/v1/decisions?limit=1", timeout=timeout)
        return ApiAuthProbeResult(status=error.status, error=message)
    except socket.timeout:
        return ApiAuthProbeResult(status=0, error=f"timeout after {timeout}s")
    except urllib.error.URLError as error:
        return ApiAuthProbeResult(status=0, error=str(error.reason))
    return ApiAuthProbeResult(status=response.status, error=None)


def probe_mcp_reachability(*, mcp_url: str, timeout: int = 10) -> McpReachabilityResult:
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "user-agent": default_user_agent(),
    }
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf8")
    request = urllib.request.Request(mcp_url, data=body, method="POST", headers=headers)
    try:
        response = urlopen_with_retries(request, timeout=timeout, context=create_ssl_context())
    except HttpStatusError as error:
        # 401 with "Missing bearer token" is the happy path here — it
        # means the server is up and the auth middleware is running.
        if error.status == 401:
            return McpReachabilityResult(status=401, error=None)
        return McpReachabilityResult(status=error.status, error=_read_error_message(error))
    except socket.timeout:
        return McpReachabilityResult(status=0, error=f"timeout after {timeout}s")
    except urllib.error.URLError as error:
        return McpReachabilityResult(status=0, error=str(error.reason))
    return McpReachabilityResult(status=response.status, error=None)


def _read_error_message(error: HttpStatusError) -> str:
    raw = error.body.decode("utf8") if error.body else ""
    try:
        payload = json.loads(raw)
        return str(payload.get("error") or payload.get("message") or error.reason)
    except json.JSONDecodeError:
        return raw or str(error.reason)


@dataclass
class McpGateProbeResult:
    """Live MCP authenticated probe for tool schema & repo project resolution."""

    authenticated: bool
    status: int
    supports_project_provisioning: bool = False
    project_resolution: str | None = None
    resolved_org_id: str | None = None
    resolved_project_id: str | None = None
    suggested_project_name: str | None = None
    available_projects_count: int = 0
    error: str | None = None

    def short_status(self) -> str:
        if not self.authenticated:
            if self.status == 401:
                return "unauthorized — configured MCP bearer token was rejected"
            if self.status == 0:
                return f"unreachable — {self.error}"
            return f"error {self.status}: {self.error}"
        parts = ["authenticated"]
        if self.supports_project_provisioning:
            parts.append("on-demand project creation enabled")
        if self.project_resolution:
            target = self.resolved_project_id or "unknown"
            parts.append(f"resolution={self.project_resolution} ({target})")
        return " • ".join(parts)


def _parse_mcp_rpc_body(raw_bytes: bytes) -> dict:
    text = raw_bytes.decode("utf8", errors="replace").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("data:"):
            candidate = stripped[5:].strip()
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    return {}


def probe_mcp_gate_resolution(
    *,
    mcp_url: str,
    token: str,
    org_id: str | None = None,
    project_id: str | None = None,
    repo_ref: str | None = None,
    timeout: int = 10,
) -> McpGateProbeResult:
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "authorization": f"Bearer {token}",
        "user-agent": default_user_agent(),
    }
    list_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf8")
    list_req = urllib.request.Request(mcp_url, data=list_body, method="POST", headers=headers)
    try:
        list_resp = urlopen_with_retries(list_req, timeout=timeout, context=create_ssl_context())
    except HttpStatusError as error:
        return McpGateProbeResult(
            authenticated=False,
            status=error.status,
            error=_read_error_message(error),
        )
    except socket.timeout:
        return McpGateProbeResult(authenticated=False, status=0, error=f"timeout after {timeout}s")
    except urllib.error.URLError as error:
        return McpGateProbeResult(authenticated=False, status=0, error=str(error.reason))

    rpc_payload = _parse_mcp_rpc_body(list_resp.body)
    tools = ((rpc_payload.get("result") or {}).get("tools")) if isinstance(rpc_payload.get("result"), dict) else None
    supports_provisioning = False
    if isinstance(tools, list):
        gate_tool = next((t for t in tools if isinstance(t, dict) and t.get("name") == "do-prepare-decision-gate"), None)
        draft_tool = next((t for t in tools if isinstance(t, dict) and t.get("name") == "do-create-decision-draft"), None)
        gate_props = (((gate_tool or {}).get("inputSchema") or {}).get("properties")) or {}
        draft_props = (((draft_tool or {}).get("inputSchema") or {}).get("properties")) or {}
        supports_provisioning = (
            "create_project_if_missing" in gate_props
            and "project_name" in gate_props
            and "create_project_if_missing" in draft_props
        )

    if not repo_ref:
        return McpGateProbeResult(
            authenticated=True,
            status=list_resp.status,
            supports_project_provisioning=supports_provisioning,
        )

    arguments: dict[str, object] = {
        "repo_ref": repo_ref,
        "task_summary": "DecisionOps doctor connectivity and project resolution check",
    }
    if project_id:
        arguments["project_id"] = project_id

    gate_body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "do-prepare-decision-gate",
                "arguments": arguments,
            },
        }
    ).encode("utf8")
    gate_req = urllib.request.Request(mcp_url, data=gate_body, method="POST", headers=headers)
    try:
        gate_resp = urlopen_with_retries(gate_req, timeout=timeout, context=create_ssl_context())
    except HttpStatusError as error:
        return McpGateProbeResult(
            authenticated=True,
            status=list_resp.status,
            supports_project_provisioning=supports_provisioning,
            error=_read_error_message(error),
        )
    except (socket.timeout, urllib.error.URLError) as error:
        return McpGateProbeResult(
            authenticated=True,
            status=list_resp.status,
            supports_project_provisioning=supports_provisioning,
            error=str(error),
        )

    gate_rpc = _parse_mcp_rpc_body(gate_resp.body)
    result_obj = gate_rpc.get("result") if isinstance(gate_rpc.get("result"), dict) else {}
    structured = result_obj.get("structuredContent") if isinstance(result_obj.get("structuredContent"), dict) else {}
    if not structured and isinstance(result_obj.get("content"), list):
        for item in result_obj["content"]:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    parsed_text = json.loads(item["text"])
                    if isinstance(parsed_text, dict):
                        structured = parsed_text
                        break
                except json.JSONDecodeError:
                    continue

    if result_obj.get("isError"):
        error_msg = "MCP gate call returned error"
        if isinstance(result_obj.get("content"), list) and result_obj["content"]:
            first = result_obj["content"][0]
            if isinstance(first, dict) and first.get("text"):
                error_msg = str(first["text"])
        return McpGateProbeResult(
            authenticated=True,
            status=gate_resp.status,
            supports_project_provisioning=supports_provisioning,
            error=error_msg,
        )

    suggested = structured.get("suggested_new_project")
    suggested_name = str(suggested.get("name")) if isinstance(suggested, dict) and suggested.get("name") else None
    available = structured.get("available_projects")
    available_count = len(available) if isinstance(available, list) else 0

    return McpGateProbeResult(
        authenticated=True,
        status=gate_resp.status,
        supports_project_provisioning=supports_provisioning,
        project_resolution=str(structured["project_resolution"]) if structured.get("project_resolution") else None,
        resolved_org_id=str(structured["org_id"]) if structured.get("org_id") else None,
        resolved_project_id=str(structured["project_id"]) if structured.get("project_id") else None,
        suggested_project_name=suggested_name,
        available_projects_count=available_count,
    )

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .config import (
    DEFAULT_API_BASE_URL,
    DEFAULT_AUTH_TOKEN_ENV,
    DEFAULT_OAUTH_API_AUDIENCE,
    DEFAULT_OAUTH_CLIENT_ID,
    DEFAULT_OAUTH_ISSUER_URL,
    DEFAULT_OAUTH_SCOPES,
    decisionops_home,
)
from .fileio import atomic_write_text
from .http import default_user_agent
from .http import HttpStatusError, HttpResponse, urlopen_with_retries
from .tls import create_ssl_context


@dataclass(slots=True)
class AuthState:
    apiBaseUrl: str
    issuerUrl: str
    clientId: str
    scopes: list[str]
    tokenType: str
    accessToken: str
    audience: str | None = None
    refreshToken: str | None = None
    expiresAt: str | None = None
    issuedAt: str | None = None
    method: str = "token"
    user: dict[str, str] | None = None


@dataclass(slots=True)
class OAuthDiscovery:
    authorizationEndpoint: str
    tokenEndpoint: str
    issuer: str
    revocationEndpoint: str | None = None
    userinfoEndpoint: str | None = None


@dataclass(slots=True)
class LoginResult:
    state: AuthState
    storagePath: str
    openedBrowser: bool
    authorizationUrl: str | None = None
    verificationUri: str | None = None
    userCode: str | None = None


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _render_oauth_callback_html(params: dict[str, str]) -> tuple[str, int]:
    failed = bool(params.get("error"))
    title = "DecisionOps login failed" if failed else "DecisionOps login complete"
    badge_label = "Authorization failed" if failed else "Authorization complete"
    summary = (
        _escape_html(params.get("error_description") or params.get("error") or "The browser handoff could not be completed.")
        if failed
        else "You can return to the terminal. Your local DecisionOps client has received the login response."
    )
    detail = (
        "Retry the sign-in command from the terminal, then approve the browser prompt again."
        if failed
        else "This tab can be closed once you are ready."
    )
    accent = "#b42318" if failed else "#067647"
    accent_soft = "rgba(217, 45, 32, 0.12)" if failed else "rgba(6, 118, 71, 0.12)"
    panel_border = "rgba(217, 45, 32, 0.22)" if failed else "rgba(6, 118, 71, 0.22)"
    icon = (
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.75a9.25 9.25 0 1 0 9.25 9.25A9.26 9.26 0 0 0 12 2.75Zm0 5.1a1.1 1.1 0 0 1 1.1 1.1v4.25a1.1 1.1 0 1 1-2.2 0V8.95a1.1 1.1 0 0 1 1.1-1.1Zm0 9.2a1.35 1.35 0 1 1 1.35-1.35A1.35 1.35 0 0 1 12 17.05Z" fill="currentColor"/></svg>'
        if failed
        else '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.75a9.25 9.25 0 1 0 9.25 9.25A9.26 9.26 0 0 0 12 2.75Zm4.34 7.19-4.77 5.52a1.1 1.1 0 0 1-1.61.06l-2.31-2.31a1.1 1.1 0 0 1 1.56-1.56l1.47 1.47 4-4.62a1.1 1.1 0 0 1 1.66 1.44Z" fill="currentColor"/></svg>'
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f4f1ea;
        --surface: rgba(255, 255, 255, 0.94);
        --surface-border: rgba(41, 37, 36, 0.12);
        --text: #1c1917;
        --muted: #57534e;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-height: 100vh;
        font-family: Inter, "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
        color: var(--text);
        background:
          radial-gradient(circle at top left, rgba(15, 118, 110, 0.15), transparent 34%),
          radial-gradient(circle at top right, rgba(6, 118, 71, 0.14), transparent 28%),
          linear-gradient(180deg, var(--bg) 0%, #f8f7f4 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 24px;
      }}
      .card {{
        width: min(100%, 560px);
        background: var(--surface);
        border: 1px solid var(--surface-border);
        border-radius: 24px;
        padding: 24px;
        box-shadow: 0 20px 45px rgba(28, 25, 23, 0.08);
      }}
      .brand {{ display: inline-flex; align-items: center; gap: 12px; margin-bottom: 20px; font-size: 14px; font-weight: 600; }}
      .brand-mark {{ width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center; background: #1c1917; color: #fafaf9; font-size: 15px; font-weight: 700; }}
      .badge {{ display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 999px; background: {accent_soft}; color: {accent}; font-size: 13px; font-weight: 600; }}
      .badge svg {{ width: 16px; height: 16px; }}
      h1 {{ margin: 18px 0 10px; font-size: clamp(28px, 5vw, 40px); line-height: 1.05; letter-spacing: -0.04em; }}
      p {{ margin: 0; font-size: 16px; line-height: 1.65; color: var(--muted); }}
      .notice {{ margin-top: 24px; padding: 18px; border-radius: 18px; border: 1px solid {panel_border}; background: {accent_soft}; }}
      .notice strong {{ display: block; margin-bottom: 6px; color: var(--text); font-size: 15px; }}
      .actions {{ display: flex; align-items: center; gap: 12px; margin-top: 24px; }}
      .button {{ appearance: none; border: 0; border-radius: 999px; background: #1c1917; color: #fafaf9; padding: 12px 18px; font: inherit; font-weight: 600; cursor: pointer; }}
      .footnote {{ font-size: 13px; }}
      @media (max-width: 640px) {{
        .card {{ padding: 20px; border-radius: 20px; }}
        .actions {{ flex-direction: column; align-items: stretch; }}
        .button {{ width: 100%; }}
      }}
    </style>
  </head>
  <body>
    <main class="card">
      <div class="brand"><div class="brand-mark">Do</div><span>DecisionOps local sign-in</span></div>
      <div class="badge">{icon}<span>{badge_label}</span></div>
      <h1>{title}</h1>
      <p>{summary}</p>
      <section class="notice" aria-live="polite"><strong>{"What happened" if failed else "What to do next"}</strong><p>{detail}</p></section>
      <div class="actions">
        <button class="button" type="button" onclick="window.close()">Close this tab</button>
        <p class="footnote">If the tab does not close automatically, you can leave it and continue from the terminal.</p>
      </div>
    </main>
  </body>
</html>"""
    return html, 400 if failed else 200


def _auth_path() -> Path:
    return Path(decisionops_home()) / "auth.json"


def _secure_write(file_path: Path, value: str) -> None:
    atomic_write_text(file_path, value, mode=stat.S_IRUSR | stat.S_IWUSR)


def _parse_json_text(text: str, url: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Expected JSON response from {url}, received: {text[:240]}") from error


def _parse_json_response(response: HttpResponse) -> dict[str, Any]:
    return _parse_json_text(response.body.decode("utf8"), response.url)


def _request_json(url: str, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None, timeout: float = 10.0) -> dict[str, Any]:
    request_headers = {"user-agent": default_user_agent(), **(headers or {})}
    request = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    try:
        return _parse_json_response(urlopen_with_retries(request, timeout=timeout, context=create_ssl_context()))
    except HttpStatusError as error:
        raw = error.body.decode("utf8")
        try:
            payload = _parse_json_text(raw, error.url)
            message = str(payload.get("error_description") or payload.get("error") or error.reason)
        except RuntimeError:
            message = raw[:240] if raw else error.reason
        raise RuntimeError(f"Auth request failed ({error.status}): {message}") from error
    except socket.timeout as error:
        raise RuntimeError(f"Auth request timed out for {url}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach auth endpoint: {error.reason}") from error


def _post_form(url: str, body: dict[str, str], timeout: float = 10.0) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(body).encode("utf8")
    return _request_json(
        url,
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "application/json"},
        body=encoded,
        timeout=timeout,
    )


def _get_json(url: str, access_token: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
    headers = {"accept": "application/json"}
    if access_token:
        headers["authorization"] = f"Bearer {access_token}"
    return _request_json(url, headers=headers, timeout=timeout)


def _base64_url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _sha256(value: str) -> str:
    return _base64_url_encode(hashlib.sha256(value.encode("utf8")).digest())


def _generate_verifier() -> str:
    return _base64_url_encode(secrets.token_bytes(48))


def _generate_state() -> str:
    return _base64_url_encode(secrets.token_bytes(24))


def _normalize_scopes(scopes: list[str] | None = None) -> list[str]:
    source = scopes if scopes else DEFAULT_OAUTH_SCOPES
    deduped: list[str] = []
    for scope in source:
        clean = scope.strip()
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped


def _resolve_oauth_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    return {
        "apiBaseUrl": options.get("apiBaseUrl") or DEFAULT_API_BASE_URL,
        "issuerUrl": options.get("issuerUrl") or DEFAULT_OAUTH_ISSUER_URL,
        "clientId": options.get("clientId") or DEFAULT_OAUTH_CLIENT_ID,
        "audience": options.get("audience") or DEFAULT_OAUTH_API_AUDIENCE,
        "scopes": _normalize_scopes(options.get("scopes")),
    }


def _metadata_candidates(issuer_url: str) -> list[str]:
    parsed = urllib.parse.urlsplit(issuer_url.rstrip("/"))
    base = f"{parsed.scheme}://{parsed.netloc}"
    issuer_path = parsed.path.rstrip("/")
    candidates: list[str] = []

    # RFC 8414: when the issuer has a path component, insert `/.well-known`
    # before the issuer path instead of appending it after.
    if issuer_path:
        candidates.extend(
            [
                f"{base}/.well-known/oauth-authorization-server{issuer_path}",
                f"{base}/.well-known/openid-configuration{issuer_path}",
            ]
        )

    # Compatibility fallback for providers that still publish discovery
    # documents relative to the issuer URL.
    candidates.extend(
        [
            f"{issuer_url.rstrip('/')}/.well-known/oauth-authorization-server",
            f"{issuer_url.rstrip('/')}/.well-known/openid-configuration",
        ]
    )

    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _oauth_endpoint(issuer_url: str, endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(issuer_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/oauth") or path == "/oauth":
        next_path = f"{path}/{endpoint}"
    elif path:
        next_path = f"{path}/oauth/{endpoint}"
    else:
        next_path = f"/oauth/{endpoint}"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, next_path, "", ""))


def discover_oauth(options: dict[str, Any] | None = None) -> OAuthDiscovery:
    resolved = _resolve_oauth_options(options)
    issuer_url = str(resolved["issuerUrl"]).rstrip("/")
    for candidate in _metadata_candidates(issuer_url):
        try:
            payload = _get_json(candidate)
        except RuntimeError:
            continue
        authorization_endpoint = str(payload.get("authorization_endpoint") or "")
        token_endpoint = str(payload.get("token_endpoint") or "")
        if authorization_endpoint and token_endpoint:
            return OAuthDiscovery(
                authorizationEndpoint=authorization_endpoint,
                tokenEndpoint=token_endpoint,
                revocationEndpoint=str(payload["revocation_endpoint"]) if payload.get("revocation_endpoint") else None,
                userinfoEndpoint=str(payload["userinfo_endpoint"]) if payload.get("userinfo_endpoint") else None,
                issuer=str(payload.get("issuer") or resolved["issuerUrl"]),
            )
    return OAuthDiscovery(
        authorizationEndpoint=_oauth_endpoint(issuer_url, "authorize"),
        tokenEndpoint=_oauth_endpoint(issuer_url, "token"),
        revocationEndpoint=_oauth_endpoint(issuer_url, "revoke"),
        userinfoEndpoint=_oauth_endpoint(issuer_url, "userinfo"),
        issuer=str(resolved["issuerUrl"]),
    )


def _fetch_user_info(discovery: OAuthDiscovery, access_token: str) -> dict[str, str] | None:
    if not discovery.userinfoEndpoint:
        return None
    try:
        payload = _get_json(discovery.userinfoEndpoint, access_token)
        return {
            key: str(payload[key])
            for key in ("sub", "email", "name")
            if payload.get(key) is not None
        }
    except RuntimeError:
        return None


def _require_token_value(token: dict[str, Any], field: str, context: str) -> str:
    value = token.get(field)
    if value is None or not str(value).strip():
        raise RuntimeError(f"{context} did not include `{field}`.")
    return str(value)


def _build_auth_state(
    token: dict[str, Any],
    method: str,
    resolved: dict[str, Any],
    discovery: OAuthDiscovery,
    user_info: dict[str, str] | None = None,
) -> AuthState:
    expires_at = None
    if token.get("expires_in"):
        try:
            expires_seconds = int(token["expires_in"])
        except (TypeError, ValueError):
            expires_seconds = None
        if expires_seconds is not None:
            expires_at = (datetime.now(UTC) + timedelta(seconds=expires_seconds)).isoformat().replace("+00:00", "Z")
    user = None
    if user_info:
        user = {}
        if user_info.get("sub"):
            user["id"] = user_info["sub"]
        if user_info.get("email"):
            user["email"] = user_info["email"]
        if user_info.get("name"):
            user["name"] = user_info["name"]
    return AuthState(
        apiBaseUrl=str(resolved["apiBaseUrl"]),
        issuerUrl=discovery.issuer,
        clientId=str(resolved["clientId"]),
        audience=str(resolved["audience"]) if resolved.get("audience") else None,
        scopes=str(token["scope"]).split() if token.get("scope") else list(resolved["scopes"]),
        tokenType=str(token.get("token_type") or "Bearer"),
        accessToken=_require_token_value(token, "access_token", "OAuth token response"),
        refreshToken=str(token["refresh_token"]) if token.get("refresh_token") else None,
        expiresAt=expires_at,
        issuedAt=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        method=method,
        user=user,
    )


def read_auth_state() -> AuthState | None:
    env_state = _read_env_auth_state()
    if env_state is not None:
        return env_state
    file_path = _auth_path()
    if not file_path.exists():
        return None
    try:
        parsed = json.loads(file_path.read_text(encoding="utf8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Saved CLI auth file is corrupt: {file_path}. Run `dops login --clear` and sign in again."
        ) from error
    if not parsed.get("accessToken"):
        return None
    return AuthState(
        apiBaseUrl=str(parsed.get("apiBaseUrl") or DEFAULT_API_BASE_URL),
        issuerUrl=str(parsed.get("issuerUrl") or parsed.get("apiBaseUrl") or DEFAULT_OAUTH_ISSUER_URL),
        clientId=str(parsed.get("clientId") or DEFAULT_OAUTH_CLIENT_ID),
        audience=str(parsed["audience"]) if parsed.get("audience") else None,
        scopes=[str(item) for item in parsed.get("scopes") or DEFAULT_OAUTH_SCOPES],
        tokenType=str(parsed.get("tokenType") or "Bearer"),
        accessToken=str(parsed["accessToken"]),
        refreshToken=str(parsed["refreshToken"]) if parsed.get("refreshToken") else None,
        expiresAt=str(parsed["expiresAt"]) if parsed.get("expiresAt") else None,
        issuedAt=str(parsed.get("issuedAt") or datetime.now(UTC).isoformat().replace("+00:00", "Z")),
        method=str(parsed.get("method") or "token"),
        user=parsed.get("user"),
    )


def write_auth_state(auth: AuthState) -> str:
    file_path = _auth_path()
    _secure_write(file_path, json.dumps(asdict(auth), indent=2) + "\n")
    return str(file_path)


def clear_auth_state() -> None:
    _auth_path().unlink(missing_ok=True)


def default_api_base_url() -> str:
    return DEFAULT_API_BASE_URL


def default_client_id() -> str:
    return DEFAULT_OAUTH_CLIENT_ID


def default_scopes() -> list[str]:
    return list(DEFAULT_OAUTH_SCOPES)


def _token_env_names() -> list[str]:
    ordered: list[str] = []
    for name in (DEFAULT_AUTH_TOKEN_ENV, "DECISIONOPS_ACCESS_TOKEN", "DECISIONOPS_TOKEN"):
        if name and name not in ordered:
            ordered.append(name)
    return ordered


def _read_env_auth_state() -> AuthState | None:
    for env_name in _token_env_names():
        token = os.environ.get(env_name, "").strip()
        if not token:
            continue
        resolved = _resolve_oauth_options({})
        return AuthState(
            apiBaseUrl=str(resolved["apiBaseUrl"]),
            issuerUrl=str(resolved["issuerUrl"]),
            clientId=str(resolved["clientId"]),
            audience=str(resolved["audience"]) if resolved.get("audience") else None,
            scopes=list(resolved["scopes"]),
            tokenType="Bearer",
            accessToken=token,
            issuedAt=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            method=f"env:{env_name}",
        )
    return None


def save_token_auth_state(*, token: str, apiBaseUrl: str | None = None, issuerUrl: str | None = None, clientId: str | None = None, audience: str | None = None, scopes: list[str] | None = None) -> tuple[AuthState, str]:
    resolved = _resolve_oauth_options(
        {"apiBaseUrl": apiBaseUrl, "issuerUrl": issuerUrl, "clientId": clientId, "audience": audience, "scopes": scopes}
    )
    state = AuthState(
        apiBaseUrl=str(resolved["apiBaseUrl"]),
        issuerUrl=str(resolved["issuerUrl"]),
        clientId=str(resolved["clientId"]),
        audience=str(resolved["audience"]) if resolved.get("audience") else None,
        scopes=list(resolved["scopes"]),
        tokenType="Bearer",
        accessToken=token,
        issuedAt=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        method="token",
    )
    return state, write_auth_state(state)


def is_expired(auth: AuthState, skew_seconds: int = 30) -> bool:
    if not auth.expiresAt:
        return False
    expires_at = datetime.fromisoformat(auth.expiresAt.replace("Z", "+00:00"))
    return expires_at <= datetime.now(UTC) + timedelta(seconds=skew_seconds)


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "DecisionOpsOAuth/1.0"
    callback_state: "_CallbackState"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/auth/callback":
            self.send_error(404, "Not Found")
            return
        params = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        html, status_code = _render_oauth_callback_html(params)
        payload = html.encode("utf8")
        self.send_response(status_code)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        full_url = f"http://127.0.0.1:{self.server.server_address[1]}{self.path}"
        self.callback_state.value = {"callbackUrl": full_url, "params": params}
        self.callback_state.event.set()
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


@dataclass
class _CallbackState:
    event: threading.Event
    value: dict[str, Any] | None = None


def _start_oauth_callback_server(callback_port: int = 0, timeout_ms: int = 120_000) -> tuple[str, Callable[[], dict[str, Any]]]:
    state = _CallbackState(event=threading.Event())

    class Handler(_CallbackHandler):
        callback_state = state

    server = ThreadingHTTPServer(("127.0.0.1", callback_port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    callback_url = f"http://127.0.0.1:{server.server_address[1]}/auth/callback"

    def wait_for_callback() -> dict[str, Any]:
        try:
            if not state.event.wait(timeout_ms / 1000):
                raise RuntimeError("Timed out waiting for browser authentication to complete.")
            if state.value is None:
                raise RuntimeError("Browser authentication callback completed without a response payload.")
            return state.value
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    return callback_url, wait_for_callback


def login_with_pkce(
    *,
    apiBaseUrl: str | None = None,
    issuerUrl: str | None = None,
    clientId: str | None = None,
    audience: str | None = None,
    scopes: list[str] | None = None,
    openBrowser: bool = True,
    callbackPort: int = 0,
    timeoutMs: int = 120_000,
    onAuthorizeUrl: Callable[[str], None] | None = None,
) -> LoginResult:
    resolved = _resolve_oauth_options(
        {"apiBaseUrl": apiBaseUrl, "issuerUrl": issuerUrl, "clientId": clientId, "audience": audience, "scopes": scopes}
    )
    discovery = discover_oauth(resolved)
    verifier = _generate_verifier()
    challenge = _sha256(verifier)
    state = _generate_state()
    callback_url, wait_for_callback = _start_oauth_callback_server(callbackPort, timeoutMs)
    authorization_url = urllib.parse.urlparse(discovery.authorizationEndpoint)
    query = urllib.parse.parse_qsl(authorization_url.query, keep_blank_values=True)
    query.extend(
        [
            ("response_type", "code"),
            ("client_id", str(resolved["clientId"])),
            ("redirect_uri", callback_url),
            ("scope", " ".join(resolved["scopes"])),
            ("code_challenge", challenge),
            ("code_challenge_method", "S256"),
            ("state", state),
        ]
    )
    if resolved.get("audience"):
        query.append(("resource", str(resolved["audience"])))
    auth_url = urllib.parse.urlunparse(authorization_url._replace(query=urllib.parse.urlencode(query)))
    if onAuthorizeUrl:
        onAuthorizeUrl(auth_url)
    opened_browser = bool(openBrowser and webbrowser.open(auth_url))
    callback = wait_for_callback()
    params = callback["params"]
    if params.get("error"):
        raise RuntimeError(f"Browser authentication failed: {params.get('error_description') or params['error']}")
    if params.get("state") != state:
        raise RuntimeError("Browser authentication failed: state verification mismatch.")
    if not params.get("code"):
        raise RuntimeError("Browser authentication failed: callback did not include an authorization code.")
    token_payload = _post_form(
        discovery.tokenEndpoint,
        {
            "grant_type": "authorization_code",
            "client_id": str(resolved["clientId"]),
            "code": str(params["code"]),
            "redirect_uri": callback_url,
            "code_verifier": verifier,
            **({"resource": str(resolved["audience"])} if resolved.get("audience") else {}),
        },
    )
    access_token = _require_token_value(token_payload, "access_token", "OAuth token response")
    user_info = _fetch_user_info(discovery, access_token)
    auth_state = _build_auth_state(token_payload, "pkce", resolved, discovery, user_info)
    storage_path = write_auth_state(auth_state)
    return LoginResult(
        state=auth_state,
        storagePath=storage_path,
        openedBrowser=opened_browser,
        authorizationUrl=auth_url,
    )


def refresh_auth_state(auth: AuthState, *, apiBaseUrl: str | None = None, issuerUrl: str | None = None, clientId: str | None = None, audience: str | None = None, scopes: list[str] | None = None) -> AuthState:
    if not auth.refreshToken:
        raise RuntimeError("No refresh token is available for the current session.")
    resolved = _resolve_oauth_options(
        {
            "apiBaseUrl": apiBaseUrl or auth.apiBaseUrl,
            "issuerUrl": issuerUrl or auth.issuerUrl,
            "clientId": clientId or auth.clientId,
            "audience": audience or auth.audience,
            "scopes": scopes or auth.scopes,
        }
    )
    discovery = discover_oauth(resolved)
    token_payload = _post_form(
        discovery.tokenEndpoint,
        {
            "grant_type": "refresh_token",
            "refresh_token": auth.refreshToken,
            "client_id": str(resolved["clientId"]),
            **({"resource": str(resolved["audience"])} if resolved.get("audience") else {}),
        },
    )
    if "refresh_token" not in token_payload:
        token_payload["refresh_token"] = auth.refreshToken
    if "scope" not in token_payload:
        token_payload["scope"] = " ".join(auth.scopes)
    if "token_type" not in token_payload:
        token_payload["token_type"] = auth.tokenType
    access_token = _require_token_value(token_payload, "access_token", "OAuth token refresh response")
    user_info = _fetch_user_info(discovery, access_token) or {
        "sub": auth.user.get("id") if auth.user else None,
        "email": auth.user.get("email") if auth.user else None,
        "name": auth.user.get("name") if auth.user else None,
    }
    next_state = _build_auth_state(token_payload, auth.method, resolved, discovery, user_info)
    write_auth_state(next_state)
    return next_state


def ensure_valid_auth_state(auth: AuthState) -> AuthState:
    if not is_expired(auth):
        return auth
    if not auth.refreshToken:
        raise RuntimeError("Your saved DecisionOps login has expired. Run `dops login` again.")
    try:
        return refresh_auth_state(auth)
    except RuntimeError as error:
        raise RuntimeError(
            "Your saved DecisionOps login has expired and could not be refreshed. Run `dops login` again.\n\n"
            f"Details: {error}"
        ) from error


def revoke_auth_state(auth: AuthState) -> None:
    discovery = discover_oauth(
        {
            "apiBaseUrl": auth.apiBaseUrl,
            "issuerUrl": auth.issuerUrl,
            "clientId": auth.clientId,
            "audience": auth.audience,
            "scopes": auth.scopes,
        }
    )
    if not discovery.revocationEndpoint:
        return
    try:
        _post_form(discovery.revocationEndpoint, {"client_id": auth.clientId, "token": auth.refreshToken or auth.accessToken})
    except RuntimeError:
        return

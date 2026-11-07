from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

_CONFIG_ERROR: str | None = None


def _append_config_error(message: str) -> None:
    global _CONFIG_ERROR
    _CONFIG_ERROR = f"{_CONFIG_ERROR}\n{message}" if _CONFIG_ERROR else message


PLACEHOLDER_ORG_ID = "org_123"
PLACEHOLDER_PROJECT_ID = "proj_456"
PLACEHOLDER_REPO_REF = "owner/repo"


def expand_home(value: str) -> str:
    if not value.startswith("~"):
        return value
    return str(Path(value).expanduser())


def decisionops_home() -> str:
    return expand_home(os.environ.get("DECISIONOPS_HOME", "~/.decisionops"))


def config_path() -> Path:
    configured = os.environ.get("DECISIONOPS_CONFIG_PATH")
    if configured:
        return Path(expand_home(configured))
    return Path(decisionops_home()) / "config.toml"


def _read_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf8"))


try:
    _CONFIG = _read_config()
except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
    _CONFIG = {}
    _CONFIG_ERROR = f"Invalid TOML in {config_path()}: {error}"


def _lookup_config_value(*paths: tuple[str, ...]) -> Any | None:
    for path in paths:
        current: Any = _CONFIG
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None:
            return current
    return None


def _string_value(env_name: str, default: str, *config_paths: tuple[str, ...]) -> str:
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    config_value = _lookup_config_value(*config_paths)
    if config_value is None:
        return default
    return str(config_value)


def _bool_value(env_name: str, default: bool, *config_paths: tuple[str, ...]) -> bool:
    env_value = os.environ.get(env_name)
    if env_value is not None:
        return env_value.strip().lower() in {"1", "true", "yes", "on"}
    config_value = _lookup_config_value(*config_paths)
    if isinstance(config_value, bool):
        return config_value
    if isinstance(config_value, str):
        return config_value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _int_value(env_name: str, default: int, *config_paths: tuple[str, ...]) -> int:
    env_value = os.environ.get(env_name)
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            _append_config_error(f"Invalid integer for {env_name}: {env_value!r}. Using default {default}.")
            return default
    config_value = _lookup_config_value(*config_paths)
    if config_value is None:
        return default
    try:
        return int(config_value)
    except (TypeError, ValueError):
        dotted_path = " or ".join(".".join(path) for path in config_paths)
        _append_config_error(
            f"Invalid integer for `{dotted_path}` in {config_path()}: {config_value!r}. Using default {default}."
        )
        return default


def _float_value(env_name: str, default: float, *config_paths: tuple[str, ...]) -> float:
    env_value = os.environ.get(env_name)
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            _append_config_error(f"Invalid number for {env_name}: {env_value!r}. Using default {default}.")
            return default
    config_value = _lookup_config_value(*config_paths)
    if config_value is None:
        return default
    try:
        return float(config_value)
    except (TypeError, ValueError):
        dotted_path = " or ".join(".".join(path) for path in config_paths)
        _append_config_error(
            f"Invalid number for `{dotted_path}` in {config_path()}: {config_value!r}. Using default {default}."
        )
        return default


def _list_value(env_name: str, default: list[str], *config_paths: tuple[str, ...]) -> list[str]:
    env_value = os.environ.get(env_name)
    if env_value:
        return [item.strip() for item in env_value.replace(",", " ").split() if item.strip()]
    config_value = _lookup_config_value(*config_paths)
    if isinstance(config_value, list):
        return [str(item).strip() for item in config_value if str(item).strip()]
    if isinstance(config_value, str):
        return [item.strip() for item in config_value.replace(",", " ").split() if item.strip()]
    return list(default)


DEFAULT_SKILL_NAME = _string_value("SKILL_NAME", "decision-ops", ("skill_name",), ("skill", "name"))
DEFAULT_MCP_SERVER_NAME = _string_value("MCP_SERVER_NAME", "decision-ops-mcp", ("mcp_server_name",), ("mcp", "server_name"))
DEFAULT_API_BASE_URL = _string_value(
    "DECISIONOPS_API_BASE_URL",
    "https://api.aidecisionops.com",
    ("api_base_url",),
    ("api", "base_url"),
)
DEFAULT_MCP_SERVER_URL = _string_value(
    "MCP_SERVER_URL",
    f"{DEFAULT_API_BASE_URL.rstrip('/')}/mcp",
    ("mcp_server_url",),
    ("mcp", "server_url"),
)
DEFAULT_OAUTH_ISSUER_URL = _string_value(
    "DECISIONOPS_OAUTH_ISSUER_URL",
    "https://auth.aidecisionops.com/oauth",
    ("oauth_issuer_url",),
    ("oauth", "issuer_url"),
)
DEFAULT_OAUTH_CLIENT_ID = _string_value(
    "DECISIONOPS_OAUTH_CLIENT_ID",
    "decisionops-cli",
    ("oauth_client_id",),
    ("oauth", "client_id"),
)
DEFAULT_OAUTH_SCOPES = _list_value(
    "DECISIONOPS_OAUTH_SCOPES",
    ["decisions:read", "decisions:write", "decisions:approve", "metrics:read", "admin:read", "admin:write"],
    ("oauth_scopes",),
    ("oauth", "scopes"),
)
DEFAULT_OAUTH_API_AUDIENCE = _string_value(
    "DECISIONOPS_OAUTH_AUDIENCE",
    "https://api.aidecisionops.com/v1",
    ("oauth_api_audience",),
    ("oauth", "audience"),
)
DEFAULT_HTTP_MAX_RETRIES = _int_value("DECISIONOPS_HTTP_MAX_RETRIES", 2, ("http_max_retries",), ("http", "max_retries"))
DEFAULT_HTTP_BACKOFF_SECONDS = _float_value(
    "DECISIONOPS_HTTP_BACKOFF_SECONDS",
    0.5,
    ("http_backoff_seconds",),
    ("http", "backoff_seconds"),
)
DEFAULT_VERBOSE = _bool_value("DOPS_VERBOSE", False, ("verbose",))
DEFAULT_DEBUG = _bool_value("DOPS_DEBUG", False, ("debug",))
DEFAULT_AUTH_TOKEN_ENV = _string_value(
    "DECISIONOPS_AUTH_TOKEN_ENV",
    "DECISIONOPS_ACCESS_TOKEN",
    ("auth_token_env",),
    ("auth", "token_env"),
)
DEFAULT_SKILL_REPO_URL = _string_value(
    "DECISIONOPS_SKILL_REPO_URL",
    "https://github.com/aidecisionops/skill.git",
    ("skill_repo_url",),
    ("skill", "repo_url"),
)
DEFAULT_SKILL_REPO_REF = _string_value(
    "DECISIONOPS_SKILL_REPO_REF",
    "main",
    ("skill_repo_ref",),
    ("skill", "repo_ref"),
)


def config_error() -> str | None:
    return _CONFIG_ERROR


def effective_config() -> dict[str, object]:
    return {
        "config_path": str(config_path()),
        "config_error": _CONFIG_ERROR,
        "api_base_url": DEFAULT_API_BASE_URL,
        "oauth_issuer_url": DEFAULT_OAUTH_ISSUER_URL,
        "oauth_client_id": DEFAULT_OAUTH_CLIENT_ID,
        "oauth_scopes": list(DEFAULT_OAUTH_SCOPES),
        "oauth_audience": DEFAULT_OAUTH_API_AUDIENCE,
        "skill_name": DEFAULT_SKILL_NAME,
        "mcp_server_name": DEFAULT_MCP_SERVER_NAME,
        "mcp_server_url": DEFAULT_MCP_SERVER_URL,
        "http_max_retries": DEFAULT_HTTP_MAX_RETRIES,
        "http_backoff_seconds": DEFAULT_HTTP_BACKOFF_SECONDS,
        "verbose": DEFAULT_VERBOSE,
        "debug": DEFAULT_DEBUG,
        "auth_token_env": DEFAULT_AUTH_TOKEN_ENV,
        "skill_repo_url": DEFAULT_SKILL_REPO_URL,
        "skill_repo_ref": DEFAULT_SKILL_REPO_REF,
    }

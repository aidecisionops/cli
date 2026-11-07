from __future__ import annotations

import sys

from .config import DEFAULT_DEBUG, DEFAULT_VERBOSE

_VERBOSE = DEFAULT_VERBOSE or DEFAULT_DEBUG
_DEBUG = DEFAULT_DEBUG


def set_diagnostics(*, verbose: bool = False, debug: bool = False) -> None:
    global _VERBOSE, _DEBUG
    _DEBUG = _DEBUG or debug
    _VERBOSE = _VERBOSE or verbose or _DEBUG


def is_verbose() -> bool:
    return _VERBOSE


def is_debug() -> bool:
    return _DEBUG


def emit_diagnostic(message: str) -> None:
    if not _VERBOSE:
        return
    print(f"[dops] {message}", file=sys.stderr)

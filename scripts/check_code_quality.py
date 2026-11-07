#!/usr/bin/env python3
"""AST-based code quality checker for the dops CLI.

Codifies patterns found across six rounds of manual code review.  Runs with
stdlib only — no external dependencies.  Designed to run in CI before tests.

Suppress individual findings with ``# noqa: DXXX`` on the offending line.

Exit codes:
    0  All clear (warnings are allowed).
    1  One or more errors found (categories A–D).
"""

from __future__ import annotations

import ast
import os
import re
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------

ERRORS = {"D001", "D002", "D003", "D005", "D006", "D007", "D011", "D012", "D013", "D015"}
WARNINGS = {"D016", "D017"}

DESCRIPTIONS: dict[str, str] = {
    "D001": "Bare except or except Exception without re-raise",
    "D002": "Assert used in non-test production code",
    "D003": "int()/float() conversion without ValueError handling",
    "D005": "TOCTOU: path.exists() then path.unlink() without missing_ok",
    "D006": "shutil.rmtree() without ignore_errors",
    "D007": "unlink() in finally block without try/except guard",
    "D011": "os.sys usage — use explicit import sys",
    "D012": "Exception swallowed without logging or re-raise",
    "D013": "json.loads() on external data without try/except",
    "D015": "subprocess call without timeout parameter",
    "D016": "Subparser with no set_defaults(func=...)",
    "D017": "add_argument() without help text",
}


@dataclass
class Finding:
    code: str
    path: str
    line: int
    detail: str = ""

    @property
    def is_error(self) -> bool:
        return self.code in ERRORS

    def __str__(self) -> str:
        kind = "error" if self.is_error else "warning"
        desc = DESCRIPTIONS.get(self.code, "")
        suffix = f" — {self.detail}" if self.detail else ""
        return f"{self.code} [{kind}] {self.path}:{self.line} — {desc}{suffix}"


# ---------------------------------------------------------------------------
# Noqa comment extraction
# ---------------------------------------------------------------------------

def _load_noqa_lines(filepath: str) -> dict[int, set[str]]:
    """Return {lineno: set_of_suppressed_codes} from ``# noqa: DXXX`` comments."""
    noqa: dict[int, set[str]] = {}
    try:
        with tokenize.open(filepath) as fh:
            for token_type, token_string, start, _end, _line in tokenize.generate_tokens(fh.readline):
                if token_type == tokenize.COMMENT:
                    match = re.search(r"#\s*noqa:\s*(D\d+(?:\s*,\s*D\d+)*)", token_string)
                    if match:
                        codes = {code.strip() for code in match.group(1).split(",")}
                        noqa[start[0]] = codes
    except (SyntaxError, tokenize.TokenizeError):
        pass
    return noqa


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------

@dataclass
class _VisitorState:
    filepath: str
    relpath: str
    noqa: dict[int, set[str]]
    findings: list[Finding] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)

    def add(self, code: str, line: int, detail: str = "") -> None:
        suppressed = self.noqa.get(line, set())
        if code in suppressed:
            return
        self.findings.append(Finding(code=code, path=self.relpath, line=line, detail=detail))


class DopsChecker(ast.NodeVisitor):
    """Walk a single module AST and record findings."""

    def __init__(self, state: _VisitorState) -> None:
        self.s = state
        # Track which lines are inside try/except blocks for context
        self._in_try_except: list[ast.Try] = []
        self._in_finally = False
        self._in_except_handler_depth = 0
        self._current_function: str | None = None

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _exception_name(node: ast.expr) -> str | None:
        """Extract the exception class name from an AST node.
        Handles both simple names (``Exception``) and dotted names (``json.JSONDecodeError``)."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _is_in_try_with(self, *exception_names: str) -> bool:
        """True if the current node is inside a try block whose except
        clause catches one of *exception_names*."""
        for try_node in self._in_try_except:
            for handler in try_node.handlers:
                if handler.type is None:
                    return True  # bare except catches everything
                name = self._exception_name(handler.type)
                if name and name in exception_names:
                    return True
                if isinstance(handler.type, ast.Tuple):
                    for elt in handler.type.elts:
                        elt_name = self._exception_name(elt)
                        if elt_name and elt_name in exception_names:
                            return True
        return False

    def _is_in_except_handler(self) -> bool:
        """True if currently visiting inside an except handler body."""
        return self._in_except_handler_depth > 0

    def _source_line(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.s.source_lines):
            return self.s.source_lines[lineno - 1]
        return ""

    # -- D001: bare except / except Exception without re-raise ---------------

    def _check_handler(self, handler: ast.ExceptHandler) -> None:
        is_bare = handler.type is None
        is_exception = isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
        if not (is_bare or is_exception):
            return
        # Allow if body contains a raise statement
        for child in ast.walk(handler):
            if isinstance(child, ast.Raise):
                return
        # Allow in entry-point functions (main, run) — they are the top-level handler
        if self._current_function in ("main", "run"):
            return
        self.s.add("D001", handler.lineno)

    # -- D002: assert in non-test code ----------------------------------------

    def visit_Assert(self, node: ast.Assert) -> None:  # noqa: N802
        self.s.add("D002", node.lineno)
        self.generic_visit(node)

    # -- D003: int()/float() without ValueError guard -------------------------

    def _check_int_float_call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name):
            return
        if node.func.id not in ("int", "float"):
            return
        if not node.args:
            return
        # Allow if already inside try/except ValueError
        if self._is_in_try_with("ValueError", "Exception"):
            return
        self.s.add("D003", node.lineno, f"{node.func.id}() conversion")

    # -- D005: TOCTOU path.exists() then path.unlink() -----------------------
    # Detected at function-body level: if node.exists() followed by unlink()
    # without missing_ok=True.

    def _check_toctou_unlink(self, body: list[ast.stmt]) -> None:
        for i, stmt in enumerate(body):
            if not isinstance(stmt, ast.If):
                continue
            # Check if the test is path.exists() or not path.exists()
            test = stmt.test
            if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                test = test.operand
            if not (isinstance(test, ast.Call)
                    and isinstance(test.func, ast.Attribute)
                    and test.func.attr == "exists"):
                continue
            # Search the if body and the next few statements for .unlink()
            search_nodes = list(stmt.body) + list(stmt.orelse) + body[i + 1: i + 4]
            for child in ast.walk(ast.Module(body=search_nodes, type_ignores=[])):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if child.func.attr == "unlink":
                        has_missing_ok = any(
                            kw.arg == "missing_ok" for kw in child.keywords
                        )
                        if not has_missing_ok:
                            self.s.add("D005", child.lineno)

    # -- D006: shutil.rmtree() without ignore_errors -------------------------

    def _check_rmtree(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Attribute):
            return
        if node.func.attr != "rmtree":
            return
        has_ignore = any(kw.arg == "ignore_errors" for kw in node.keywords)
        if has_ignore:
            return
        # Allow if already inside a try/except (caller handles errors)
        if self._is_in_try_with("OSError", "Exception", "RuntimeError"):
            return
        self.s.add("D006", node.lineno)

    # -- D007: unlink in finally without try/except ---------------------------

    def _check_finally_unlink(self, finally_body: list[ast.stmt]) -> None:
        for node in ast.walk(ast.Module(body=finally_body, type_ignores=[])):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("unlink",):
                    # Check if it's wrapped in its own try/except
                    for stmt in finally_body:
                        if isinstance(stmt, ast.Try):
                            for child in ast.walk(stmt):
                                if child is node:
                                    return  # already guarded
                    self.s.add("D007", node.lineno)

    # -- D011: os.sys usage ---------------------------------------------------

    def _check_os_sys(self, node: ast.Attribute) -> None:
        if (node.attr == "sys"
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"):
            self.s.add("D011", node.lineno)

    # -- D012: swallowed exceptions -------------------------------------------

    def _check_swallowed_exception(self, handler: ast.ExceptHandler) -> None:
        if handler.type is None:
            return  # bare except handled by D001
        # Only flag single generic exception types that are swallowed.
        # Multi-type tuples like (OSError, json.JSONDecodeError) show intent.
        if isinstance(handler.type, ast.Tuple):
            return  # multi-type catch shows deliberate handling
        if not isinstance(handler.type, ast.Name):
            return
        # Only flag broad types; specific types like ImportError, OSError are fine
        if handler.type.id not in ("Exception",):
            return
        # Check if body is just return None, return, or pass
        if len(handler.body) > 1:
            return
        stmt = handler.body[0]
        is_return_none = isinstance(stmt, ast.Return) and (
            stmt.value is None or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None)
        )
        is_pass = isinstance(stmt, ast.Pass)
        is_return_empty = isinstance(stmt, ast.Return) and (
            isinstance(stmt.value, ast.Constant) and stmt.value.value in ("", 0, False)
        )
        if not (is_return_none or is_pass or is_return_empty):
            return
        # Allow if there's an emit_diagnostic or logging call in the handler
        for child in ast.walk(handler):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name) and func.id in ("emit_diagnostic", "print", "logging"):
                    return
                if isinstance(func, ast.Attribute) and func.attr in ("emit_diagnostic", "warning", "error", "info", "debug"):
                    return
        self.s.add("D012", handler.lineno, "exception silently swallowed")

    # -- D013: json.loads() without try/except --------------------------------

    def _check_json_loads(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "loads":
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "json":
                # Allow if already inside a try/except that catches parse errors
                if self._is_in_try_with(
                    "JSONDecodeError", "json.JSONDecodeError", "ValueError",
                    "Exception", "RuntimeError",
                ):
                    return
                # Allow inside an except handler body — the handler itself
                # is already processing an error condition defensively
                if self._is_in_except_handler():
                    return
                self.s.add("D013", node.lineno)

    # -- D015: subprocess without timeout -------------------------------------

    def _check_subprocess_timeout(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Attribute):
            return
        if node.func.attr not in ("run", "call", "check_call", "check_output"):
            return
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            return
        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
        if has_timeout:
            return
        # Allow local git commands — they operate on filesystem, not network
        if node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.List) and first_arg.elts:
                first_elt = first_arg.elts[0]
                if isinstance(first_elt, ast.Constant) and first_elt.value == "git":
                    return
        self.s.add("D015", node.lineno)

    # -- D016: subparser without set_defaults(func=...) -----------------------
    # D017: add_argument without help
    # These are harder to detect via AST since they span multiple statements.
    # We use a simpler heuristic: search for add_parser() calls where the
    # returned variable never has set_defaults called on it.

    def _check_argparse_patterns(self, body: list[ast.stmt]) -> None:
        parser_vars: dict[str, int] = {}  # varname -> lineno of add_parser
        has_defaults: set[str] = set()

        for stmt in body:
            # Track: x = subparsers.add_parser(...)
            if (isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute)
                    and stmt.value.func.attr == "add_parser"):
                parser_vars[stmt.targets[0].id] = stmt.lineno

            # Track: x.set_defaults(func=...)
            if (isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute)
                    and stmt.value.func.attr == "set_defaults"):
                if isinstance(stmt.value.func.value, ast.Name):
                    has_defaults.add(stmt.value.func.value.id)

            # D017: x.add_argument(...) without help=
            if (isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute)
                    and stmt.value.func.attr == "add_argument"):
                call = stmt.value
                has_help = any(kw.arg == "help" for kw in call.keywords)
                has_action_version = any(
                    kw.arg == "action" and isinstance(kw.value, ast.Constant) and kw.value.value == "version"
                    for kw in call.keywords
                )
                if not has_help and not has_action_version:
                    self.s.add("D017", stmt.lineno)

        # Track parsers that create their own subparsers (parent parsers)
        has_subparsers: set[str] = set()
        for stmt in body:
            # x_subparsers = x.add_subparsers(...)
            if (isinstance(stmt, ast.Assign)
                    and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute)
                    and stmt.value.func.attr == "add_subparsers"):
                if isinstance(stmt.value.func.value, ast.Name):
                    has_subparsers.add(stmt.value.func.value.id)

        for var, lineno in parser_vars.items():
            if var not in has_defaults and var not in has_subparsers:
                self.s.add("D016", lineno, f"parser '{var}' has no set_defaults(func=...)")

    # -- visit dispatch -------------------------------------------------------

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        self._in_try_except.append(node)
        for child in node.body:
            self.visit(child)
        self._in_try_except.pop()

        for handler in node.handlers:
            self._check_handler(handler)
            self._check_swallowed_exception(handler)
            self._in_except_handler_depth += 1
            self.visit(handler)
            self._in_except_handler_depth -= 1

        for child in node.orelse:
            self.visit(child)

        if node.finalbody:
            self._check_finally_unlink(node.finalbody)
            old_finally = self._in_finally
            self._in_finally = True
            for child in node.finalbody:
                self.visit(child)
            self._in_finally = old_finally

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self._check_int_float_call(node)
        self._check_rmtree(node)
        self._check_json_loads(node)
        self._check_subprocess_timeout(node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        self._check_os_sys(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        old_func = self._current_function
        self._current_function = node.name
        self._check_toctou_unlink(node.body)
        self._check_argparse_patterns(node.body)
        self.generic_visit(node)
        self._current_function = old_func

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def check_file(filepath: str, relpath: str) -> list[Finding]:
    source = Path(filepath).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return [Finding(code="D000", path=relpath, line=1, detail="SyntaxError — cannot parse")]

    noqa = _load_noqa_lines(filepath)
    state = _VisitorState(
        filepath=filepath,
        relpath=relpath,
        noqa=noqa,
        source_lines=source.splitlines(),
    )
    checker = DopsChecker(state)
    checker.visit(tree)
    return state.findings


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    source_dir = root / "dops"

    if not source_dir.is_dir():
        print(f"Source directory not found: {source_dir}", file=sys.stderr)
        return 1

    all_findings: list[Finding] = []
    file_count = 0

    for py_file in sorted(source_dir.rglob("*.py")):
        relpath = str(py_file.relative_to(root))
        # Skip test files and __pycache__
        if "__pycache__" in relpath:
            continue
        file_count += 1
        all_findings.extend(check_file(str(py_file), relpath))

    errors = [f for f in all_findings if f.is_error]
    warnings = [f for f in all_findings if not f.is_error]

    if errors:
        print(f"\n{'=' * 60}")
        print(f"  {len(errors)} error(s) found across {file_count} files")
        print(f"{'=' * 60}\n")
        for finding in sorted(errors, key=lambda f: (f.path, f.line)):
            print(f"  {finding}")
        print()

    if warnings:
        print(f"  {len(warnings)} warning(s):\n")
        for finding in sorted(warnings, key=lambda f: (f.path, f.line)):
            print(f"  {finding}")
        print()

    if not errors and not warnings:
        print(f"All clear — {file_count} files checked, no findings.")

    if not errors:
        if warnings:
            print(f"Passed with {len(warnings)} warning(s).")
        return 0

    print("Fix errors above or suppress with # noqa: DXXX")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

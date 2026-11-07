#!/usr/bin/env python3
"""
Generates Pydantic v2 models from vendored JSON Schema files.
Supports --check mode to verify generated models are up-to-date.

Reads:  cli/schemas/*.schema.json
Writes: cli/dops/generated/*.py
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = CLI_ROOT / "schemas"
OUTPUT_DIR = CLI_ROOT / "dops" / "generated"

# Map schema file -> output Python module name
SCHEMA_MAP = {
    "decision-ops-api.schema.json": "api_models.py",
    "decision-schemas.schema.json": "enum_models.py",
    "governance.schema.json": "governance_models.py",
    "constraints-and-rules.schema.json": "constraint_models.py",
    "platform-types.schema.json": "platform_models.py",
}

BANNER = '"""Auto-generated from JSON Schema. Do not edit directly."""\n'

# datamodel-code-generator embeds a timestamp that changes every run.
# Strip it so --check comparisons are stable.
_TIMESTAMP_RE = re.compile(r"^#   timestamp: .+$", re.MULTILINE)


def _strip_timestamp(content: str) -> str:
    return _TIMESTAMP_RE.sub("#   timestamp: (stripped)", content)


def generate_one(schema_path: Path, output_path: Path) -> str:
    """Run datamodel-codegen on a single schema file, return generated content."""
    result = subprocess.run(
        [
            sys.executable, "-m", "datamodel_code_generator",
            "--input", str(schema_path),
            "--input-file-type", "jsonschema",
            "--output-model-type", "pydantic_v2.BaseModel",
            "--use-standard-collections",
            "--use-union-operator",
            "--target-python-version", "3.13",
            "--use-schema-description",
            "--collapse-root-models",
            "--enum-field-as-literal", "all",
            "--use-default",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Error generating {output_path.name}:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Pydantic models from JSON Schema")
    parser.add_argument("--check", action="store_true", help="Verify models are up-to-date without writing")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stale = False

    for schema_name, output_name in SCHEMA_MAP.items():
        schema_path = SCHEMAS_DIR / schema_name
        output_path = OUTPUT_DIR / output_name

        if not schema_path.exists():
            print(f"  Warning: schema not found: {schema_path}", file=sys.stderr)
            stale = True
            continue

        print(f"  Generating {output_name} from {schema_name}...")
        generated = generate_one(schema_path, output_path)

        if args.check:
            if not output_path.exists():
                print(f"  Missing: {output_name}", file=sys.stderr)
                stale = True
                continue
            current = output_path.read_text(encoding="utf8")
            if _strip_timestamp(current) != _strip_timestamp(generated):
                print(f"  Stale: {output_name}", file=sys.stderr)
                stale = True
            else:
                print(f"  OK: {output_name}")
        else:
            output_path.write_text(generated, encoding="utf8")
            print(f"  Wrote: {output_name}")

    # Write barrel __init__.py
    init_path = OUTPUT_DIR / "__init__.py"
    init_lines = [BANNER]
    for output_name in SCHEMA_MAP.values():
        module = output_name.removesuffix(".py")
        init_lines.append(f"from .{module} import *  # noqa: F401,F403")
    init_lines.append("")
    init_content = "\n".join(init_lines)

    if args.check:
        if not init_path.exists():
            print("  Missing: __init__.py", file=sys.stderr)
            stale = True
        elif init_path.read_text(encoding="utf8") != init_content:
            print("  Stale: __init__.py", file=sys.stderr)
            stale = True
        else:
            print("  OK: __init__.py")
    else:
        init_path.write_text(init_content, encoding="utf8")
        print("  Wrote: __init__.py")

    if args.check and stale:
        print("\nGenerated models are out of date. Run: python scripts/generate_models.py", file=sys.stderr)
        sys.exit(1)

    if args.check:
        print("\nAll generated models are up to date.")
    else:
        print("\nDone.")


if __name__ == "__main__":
    main()

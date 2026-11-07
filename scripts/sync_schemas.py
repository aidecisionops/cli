#!/usr/bin/env python3
"""
Copies JSON Schema files from sibling repos into cli/schemas/.
Supports --check mode to verify schemas are up-to-date without modifying files.

Sources:
  - decision-record/schemas/api/*.schema.json  (API contracts)
  - skill/schemas/platform-types.schema.json   (Platform types)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = CLI_ROOT / "schemas"

SOURCES = {
    # decision-record API schemas
    "decision-ops-api.schema.json": CLI_ROOT.parent / "decision-record" / "schemas" / "api" / "decision-ops-api.schema.json",
    "decision-schemas.schema.json": CLI_ROOT.parent / "decision-record" / "schemas" / "api" / "decision-schemas.schema.json",
    "governance.schema.json": CLI_ROOT.parent / "decision-record" / "schemas" / "api" / "governance.schema.json",
    "constraints-and-rules.schema.json": CLI_ROOT.parent / "decision-record" / "schemas" / "api" / "constraints-and-rules.schema.json",
    # skill platform schemas
    "platform-types.schema.json": CLI_ROOT.parent / "skill" / "schemas" / "platform-types.schema.json",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync JSON Schema files from sibling repos")
    parser.add_argument("--check", action="store_true", help="Verify schemas are up-to-date without writing")
    args = parser.parse_args()

    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    stale = False

    for dest_name, source_path in SOURCES.items():
        dest_path = SCHEMAS_DIR / dest_name

        if not source_path.exists():
            print(f"  Warning: source not found: {source_path}", file=sys.stderr)
            stale = True
            continue

        source_content = source_path.read_text(encoding="utf8")

        if args.check:
            if not dest_path.exists():
                print(f"  Missing: {dest_name}", file=sys.stderr)
                stale = True
                continue
            dest_content = dest_path.read_text(encoding="utf8")
            if dest_content != source_content:
                print(f"  Stale: {dest_name}", file=sys.stderr)
                stale = True
            else:
                print(f"  OK: {dest_name}")
        else:
            dest_path.write_text(source_content, encoding="utf8")
            print(f"  Synced: {dest_name}")

    if args.check and stale:
        print("\nSchemas are out of date. Run: python scripts/sync_schemas.py", file=sys.stderr)
        sys.exit(1)

    if args.check:
        print("\nAll schemas are up to date.")
    else:
        print("\nDone.")


if __name__ == "__main__":
    main()

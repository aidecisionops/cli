from __future__ import annotations

import argparse
import re

from .text_utils import levenshtein_distance


def _suggest_choice(input_value: str, choices: list[str]) -> str | None:
    if not input_value or not choices:
        return None
    best: tuple[str, int] | None = None
    for choice in choices:
        distance = levenshtein_distance(input_value.lower(), choice.lower())
        if best is None or distance < best[1]:
            best = (choice, distance)
    if best is None:
        return None
    max_distance = 1 if len(input_value) <= 5 else 2
    return best[0] if best[1] <= max_distance else None


class DopsHelpFormatter(argparse.RawDescriptionHelpFormatter):
    pass


class DopsArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        root_sections = getattr(self, "_root_help_sections", None)
        if root_sections:
            return _format_root_help(self, root_sections)
        return super().format_help()

    def error(self, message: str) -> None:
        invalid_choice = re.search(r"invalid choice: '([^']+)' \(choose from ([^)]+)\)", message)
        if invalid_choice:
            invalid_value = invalid_choice.group(1)
            choices = [item.strip().strip("'") for item in invalid_choice.group(2).split(",")]
            suggestion = _suggest_choice(invalid_value, choices)
            if suggestion:
                message = f"{message}. Did you mean '{suggestion}'?"
        super().error(message)


def _format_root_help(parser: argparse.ArgumentParser, root_sections: list[dict[str, object]]) -> str:
    lines: list[str] = [f"usage: {parser.prog} [-h] [--version] [--verbose] [--debug] <command> ...", ""]
    if parser.description:
        lines.append(parser.description)
        lines.append("")
    lines.append("Use dops to:")
    for section in root_sections:
        lines.append(f"  {section['title']}:")
        for command, summary in section["commands"]:  # type: ignore[index]
            lines.append(f"    {command.ljust(20)} {summary}")
        lines.append("")
    lines.append("Global options:")
    for action in parser._actions:
        if not action.option_strings or action.help == argparse.SUPPRESS:
            continue
        option_label = ", ".join(action.option_strings)
        lines.append(f"  {option_label.ljust(18)} {action.help}")
    if parser.epilog:
        lines.extend(["", parser.epilog])
    return "\n".join(lines).rstrip() + "\n"


def add_examples(parser: argparse.ArgumentParser, examples: list[str]) -> None:
    section = "Examples:\n" + "\n".join(f"  {example}" for example in examples)
    parser.epilog = f"{parser.epilog}\n\n{section}" if parser.epilog else section


def add_notes(parser: argparse.ArgumentParser, notes: list[str]) -> None:
    section = "Notes:\n" + "\n".join(f"  {note}" for note in notes)
    parser.epilog = f"{parser.epilog}\n\n{section}" if parser.epilog else section

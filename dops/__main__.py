from __future__ import annotations


def _load_main():
    from .cli import main

    return main


def run() -> int:
    try:
        return _load_main()()
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(run())

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def atomic_write_text(file_path: str | Path, value: str, *, encoding: str = "utf8", mode: int | None = None) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)  # noqa: D007
            except OSError:
                pass


def atomic_copy_dir(source_dir: str | Path, target_dir: str | Path) -> None:
    source_path = Path(source_dir)
    target_path = Path(target_dir)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_parent = Path(
        tempfile.mkdtemp(prefix=f".{target_path.name}.", suffix=".tmp", dir=target_path.parent)
    )
    temp_path = temp_parent / target_path.name
    backup_path = target_path.parent / f".{target_path.name}.backup"
    try:
        shutil.copytree(source_path, temp_path)
        if backup_path.exists():
            shutil.rmtree(backup_path, ignore_errors=True)
        if target_path.exists():
            os.replace(target_path, backup_path)
        os.replace(temp_path, target_path)
        shutil.rmtree(backup_path, ignore_errors=True)
    except Exception:
        shutil.rmtree(temp_parent, ignore_errors=True)
        if backup_path.exists() and not target_path.exists():
            os.replace(backup_path, target_path)
        raise
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)
        if backup_path.exists() and target_path.exists():
            shutil.rmtree(backup_path, ignore_errors=True)

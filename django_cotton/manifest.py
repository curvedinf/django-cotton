import json
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from django.conf import settings

from django_cotton.dependency_registry import (
    load_manifest as _load_registry_manifest,
    set_dependencies as _set_registry_dependencies,
)


@dataclass(frozen=True)
class ManifestEntry:
    compiled: str
    dependencies: Tuple[str, ...]
    mtime: float
    pure: bool = False


_manifest_loaded = False
_entries: Dict[str, ManifestEntry] = {}


def _ensure_loaded():
    global _manifest_loaded

    if _manifest_loaded:
        return

    path = getattr(settings, "COTTON_MANIFEST_PATH", None)
    if not path:
        _manifest_loaded = True
        return

    if not os.path.exists(path):
        _manifest_loaded = True
        return

    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        _manifest_loaded = True
        return

    entries = payload.get("templates", [])
    registry_seed = []

    for entry in entries:
        template_path = entry.get("path")
        compiled = entry.get("compiled")
        dependencies = tuple(entry.get("dependencies") or ())
        mtime = entry.get("mtime")
        pure = bool(entry.get("pure", False))

        if not template_path or compiled is None or mtime is None:
            continue

        _entries[template_path] = ManifestEntry(
            compiled=compiled,
            dependencies=dependencies,
            mtime=mtime,
            pure=pure,
        )
        registry_seed.append((template_path, dependencies))

    if registry_seed:
        _load_registry_manifest(registry_seed)

    _manifest_loaded = True


def get_precompiled(path: str) -> Optional[ManifestEntry]:
    _ensure_loaded()

    entry = _entries.get(path)
    if entry is None:
        return None

    try:
        current_mtime = os.path.getmtime(path)
    except OSError:
        return None

    if abs(current_mtime - entry.mtime) > 1e-6:
        # File changed since manifest generation; ignore stale entry.
        return None

    return entry


def store_entry(
    path: str, compiled: str, dependencies: Tuple[str, ...], mtime: float, pure: bool = False
) -> None:
    entry = ManifestEntry(compiled=compiled, dependencies=tuple(dependencies), mtime=mtime, pure=pure)
    _entries[path] = entry
    _set_registry_dependencies(path, dependencies)


def _reset_for_tests():
    global _manifest_loaded
    _entries.clear()
    _manifest_loaded = False

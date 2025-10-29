import threading
from typing import Iterable, Tuple

_dependency_map = {}
_lock = threading.Lock()


def set_dependencies(template_path: str, dependencies: Iterable[str]) -> None:
    deps = tuple(dict.fromkeys(dependencies))  # preserve order, remove dups
    with _lock:
        if deps:
            _dependency_map[template_path] = deps
        else:
            _dependency_map.pop(template_path, None)


def get_dependencies(template_path: str) -> Tuple[str, ...]:
    with _lock:
        deps = _dependency_map.get(template_path, ())
    return deps


def load_manifest(entries: Iterable[Tuple[str, Iterable[str]]]) -> None:
    with _lock:
        for template_path, deps in entries:
            deps_tuple = tuple(dict.fromkeys(deps))
            if deps_tuple:
                _dependency_map[template_path] = deps_tuple
            else:
                _dependency_map.pop(template_path, None)


def snapshot() -> dict:
    with _lock:
        return {path: deps for path, deps in _dependency_map.items()}

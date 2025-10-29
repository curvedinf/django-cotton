from __future__ import annotations

from typing import Dict, Iterable, Tuple

import pytest

from django_cotton.preload import preload_dependency_tree


class Graph:
    def __init__(self, mapping: Dict[str, Tuple[str, ...]]):
        self.mapping = mapping

    def deps(self, path: str) -> Tuple[str, ...]:
        return self.mapping.get(path, ())


def _resolver(name: str) -> str:
    return f"cotton/{name.replace('.', '/')}.html"


def test_preload_dependency_tree_transitive(monkeypatch):
    graph = Graph(
        {
            "cotton/root.html": ("button.primary", "button.secondary"),
            "cotton/button/primary.html": ("icon.star",),
            "cotton/icon/star.html": (),
            "cotton/button/secondary.html": (),
        }
    )

    monkeypatch.setattr("django_cotton.preload.get_dependencies", graph.deps)

    loaded: list[str] = []

    def load_template(path: str) -> None:
        loaded.append(path)

    seen = set()
    result = preload_dependency_tree(
        ["cotton/root.html"],
        resolve_component=_resolver,
        load_template=load_template,
        transitive=True,
        batch_executor=None,
        seen=seen,
    )

    assert loaded == [
        "cotton/root.html",
        "cotton/button/primary.html",
        "cotton/button/secondary.html",
        "cotton/icon/star.html",
    ]
    assert result == set(loaded)
    assert seen == set(loaded)


def test_preload_dependency_tree_single_tier(monkeypatch):
    graph = Graph({"cotton/root.html": ("child.one", "child.two")})
    monkeypatch.setattr("django_cotton.preload.get_dependencies", graph.deps)

    loaded: list[str] = []

    def load_template(path: str) -> None:
        loaded.append(path)

    result = preload_dependency_tree(
        ["cotton/root.html"],
        resolve_component=_resolver,
        load_template=load_template,
        transitive=False,
        batch_executor=None,
    )

    assert loaded == ["cotton/root.html"]
    assert result == {"cotton/root.html"}

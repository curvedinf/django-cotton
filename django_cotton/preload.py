from __future__ import annotations

from collections import deque
from typing import Callable, Iterable, Optional, Sequence, Set

from django_cotton.dependency_registry import get_dependencies


BatchExecutor = Callable[[Sequence[str], Callable[[str], None]], None]
ComponentResolver = Callable[[str], str]


def preload_dependency_tree(
    initial_paths: Iterable[str],
    *,
    resolve_component: ComponentResolver,
    load_template: Callable[[str], None],
    transitive: bool = True,
    batch_executor: Optional[BatchExecutor] = None,
    seen: Optional[Set[str]] = None,
) -> Set[str]:
    """
    Warm a set of templates and their dependencies.

    Parameters
    ----------
    initial_paths:
        Absolute template paths that should be loaded immediately.
    resolve_component:
        Callable that converts a component name (e.g. "buttons.primary") into a
        template path understood by Django's template loader.
    load_template:
        Callable that accepts a template path and performs the actual load.
        Exceptions should be handled internally so a missing template does not
        abort the entire preload.
    transitive:
        When True (default) walk the full dependency DAG breadth-first.
        Otherwise only the initial tier is preloaded.
    batch_executor:
        Optional callable used to run a batch of loads concurrently. When not
        provided each template path is loaded sequentially via ``load_template``.
    seen:
        Optional set of template paths that have already been preloaded. This
        set will be updated in-place when provided.

    Returns
    -------
    Set[str]
        The set of templates that were loaded by this invocation. This can be
        useful for bookkeeping in the caller.
    """

    queue = deque(initial_paths)
    visited = seen if seen is not None else set()
    newly_loaded: Set[str] = set()

    while queue:
        tier: list[str] = []
        for _ in range(len(queue)):
            path = queue.popleft()
            if path in visited:
                continue
            visited.add(path)
            newly_loaded.add(path)
            tier.append(path)

        if not tier:
            continue

        if batch_executor is not None:
            batch_executor(tier, load_template)
        else:
            for path in tier:
                load_template(path)

        if not transitive:
            continue

        for path in tier:
            dependencies = get_dependencies(path)
            if not dependencies:
                continue
            for component_name in dependencies:
                target_path = resolve_component(component_name)
                if target_path not in visited:
                    queue.append(target_path)

    return newly_loaded

import hashlib
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Tuple

from django.conf import settings
from django.template.loaders.base import Loader as BaseLoader
from django.core.exceptions import SuspiciousFileOperation
from django.template import TemplateDoesNotExist, Origin
from django.utils._os import safe_join
from django.template import Template
from django.apps import apps
from django.core.cache import cache

from django_cotton import manifest
from django_cotton.compiler_regex import CottonCompiler
from django_cotton.dependency_registry import set_dependencies


class Loader(BaseLoader):
    def __init__(self, engine, dirs=None):
        super().__init__(engine)
        self.cotton_compiler = CottonCompiler()
        self.cache_handler = CottonTemplateCacheHandler()
        self.dirs = dirs

    def get_contents(self, origin):
        cache_key = self.cache_handler.get_cache_key(origin)
        cached_payload = self.cache_handler.get_cached_template(cache_key)

        if cached_payload is not None:
            if isinstance(cached_payload, CachedTemplate):
                compiled = cached_payload.compiled
                dependencies = cached_payload.dependencies
            else:
                compiled, dependencies = cached_payload
            set_dependencies(origin.name, dependencies)
            return compiled

        manifest_entry = manifest.get_precompiled(origin.name)
        if manifest_entry is not None:
            compiled = manifest_entry.compiled
            dependencies = manifest_entry.dependencies
            self.cache_handler.cache_template(cache_key, compiled, dependencies)
            set_dependencies(origin.name, dependencies)
            return compiled

        template_string = self._get_template_string(origin.name)

        needs_processing = "<c-" in template_string or "{% cotton_verbatim" in template_string

        if needs_processing:
            dependencies = self.cotton_compiler.get_component_dependencies(template_string)
            compiled = self.cotton_compiler.process(template_string)
        else:
            dependencies = []
            compiled = template_string

        self.cache_handler.cache_template(cache_key, compiled, dependencies)
        set_dependencies(origin.name, dependencies)

        return compiled

    def get_template_from_string(self, template_string):
        """Create and return a Template object from a string. Used primarily for testing."""
        return Template(template_string, engine=self.engine)

    def _get_template_string(self, template_name):
        try:
            with open(template_name, "r", encoding=self.engine.file_charset) as f:
                return f.read()
        except FileNotFoundError as exc:
            raise TemplateDoesNotExist(template_name) from exc

    @lru_cache(maxsize=None)
    def get_dirs(self):
        """Retrieves possible locations of cotton directory"""
        dirs = list(self.dirs or self.engine.dirs)

        # Include any included installed app directories, e.g. project/app1/templates
        for app_config in apps.get_app_configs():
            template_dir = os.path.join(app_config.path, "templates")
            if os.path.isdir(template_dir):
                dirs.append(template_dir)

        # Check project root templates, e.g. project/templates
        base_dir = getattr(settings, "COTTON_BASE_DIR", None)
        if base_dir is None:
            base_dir = getattr(settings, "BASE_DIR", None)

        if base_dir is not None:
            root_template_dir = os.path.join(base_dir, "templates")
            if os.path.isdir(root_template_dir):
                dirs.append(root_template_dir)

        return dirs

    def reset(self):
        """Empty the template cache."""
        self.cache_handler.reset()

    def get_template_sources(self, template_name):
        """Return an Origin object pointing to an absolute path in each directory
        in template_dirs. For security reasons, if a path doesn't lie inside
        one of the template_dirs it is excluded from the result set."""
        for template_dir in self.get_dirs():
            try:
                name = safe_join(template_dir, template_name)
            except SuspiciousFileOperation:
                # The joined path was located outside of this template_dir
                # (it might be inside another one, so this isn't fatal).
                continue

            yield Origin(
                name=name,
                template_name=template_name,
                loader=self,
            )


@dataclass(frozen=True)
class CachedTemplate:
    compiled: str
    dependencies: Tuple[str, ...]


class CottonTemplateCacheHandler:
    """
    Handles caching of compiled cotton templates. Defaults to an in-process cache but can optionally
    delegate storage to Django's cache framework.
    """

    def __init__(self):
        strategy = getattr(settings, "COTTON_CACHE_STRATEGY", "local")
        if strategy not in {"local", "django"}:
            raise ValueError("COTTON_CACHE_STRATEGY must be 'local' or 'django'")

        self._use_django_cache = strategy == "django"
        self._cache_timeout = getattr(settings, "COTTON_CACHE_TIMEOUT", None)
        self._cache_prefix = getattr(settings, "COTTON_CACHE_PREFIX", "django_cotton:template:")
        self._local_cache = {}
        self._lock = threading.Lock()

    def get_cached_template(self, cache_key):
        if self._use_django_cache:
            payload = cache.get(cache_key)
            if payload is None:
                return None
            if isinstance(payload, CachedTemplate):
                return payload
            compiled, dependencies = payload
            return CachedTemplate(compiled=compiled, dependencies=tuple(dependencies))

        sentinel = cache.get(cache_key)
        with self._lock:
            if sentinel is None:
                self._local_cache.pop(cache_key, None)
                return None
            return self._local_cache.get(cache_key)

    def cache_template(self, cache_key, compiled_template, dependencies):
        payload = CachedTemplate(compiled=compiled_template, dependencies=tuple(dependencies))

        if self._use_django_cache:
            cache.set(cache_key, payload, timeout=self._cache_timeout)
        else:
            with self._lock:
                self._local_cache[cache_key] = payload
            cache.set(cache_key, True, timeout=self._cache_timeout)

    def get_cache_key(self, origin):
        try:
            source_hash = self.generate_hash([origin.name, str(os.path.getmtime(origin.name))])
        except FileNotFoundError:
            raise TemplateDoesNotExist(origin.name)

        return f"{self._cache_prefix}{source_hash}"

    def generate_hash(self, values: Iterable[str]):
        return hashlib.sha1("|".join(values).encode()).hexdigest()

    def reset(self):
        if self._use_django_cache:
            # We cannot safely flush a shared cache, rely on mtime-based invalidation.
            return

        with self._lock:
            self._local_cache.clear()

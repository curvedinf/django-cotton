import os
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.template.utils import get_app_template_dirs
from django.apps import apps

class Command(BaseCommand):
    help = "Discovers all cotton components and their dependencies."

    # Regex to find <c- tags, taken from compiler_regex.py
    tag_pattern = re.compile(
        r"<(/?)c-([^\s/>]+)((?:\s+[^\s/>\"'=<>`]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|\S+))?)*)\s*(/?)\s*>",
        re.DOTALL,
    )

    def handle(self, *args, **options):
        self.stdout.write("Discovering cotton components...")

        component_paths = self._find_component_paths()
        dependency_graph = self._build_dependency_graph(component_paths)

        self.stdout.write(self.style.SUCCESS("Component Dependency Graph:"))
        for component, dependencies in dependency_graph.items():
            self.stdout.write(f"- {component}:")
            if dependencies:
                for dep in dependencies:
                    self.stdout.write(f"  - {dep}")
            else:
                self.stdout.write("  (no dependencies)")

    def _find_component_paths(self):
        cotton_dir_name = getattr(settings, "COTTON_DIR", "cotton")
        component_paths = set()

        # 1. From settings.TEMPLATES
        template_dirs = []
        for tpl_setting in settings.TEMPLATES:
            template_dirs.extend(tpl_setting.get("DIRS", []))

        # 2. From installed apps
        for app_config in apps.get_app_configs():
            template_dir = os.path.join(app_config.path, "templates")
            if os.path.isdir(template_dir):
                template_dirs.append(template_dir)

        # 3. From project base dir
        base_dir = getattr(settings, "BASE_DIR", None)
        if base_dir:
            root_template_dir = os.path.join(base_dir, "templates")
            if os.path.isdir(root_template_dir):
                template_dirs.append(root_template_dir)

        for template_dir in set(template_dirs):
            cotton_dir = Path(template_dir) / cotton_dir_name
            if cotton_dir.is_dir():
                for root, _, files in os.walk(cotton_dir):
                    for file in files:
                        if file.endswith(".html"):
                            component_paths.add(Path(root) / file)

        return list(component_paths)

    def _build_dependency_graph(self, component_paths):
        graph = {}
        cotton_dir_name = getattr(settings, "COTTON_DIR", "cotton")

        for path in component_paths:
            try:
                # A better way to get the component name
                parts = path.parts
                try:
                    cotton_dir_index = parts.index(cotton_dir_name)
                    component_name_parts = parts[cotton_dir_index + 1:]
                    component_name = ".".join(p.replace('.html', '') for p in component_name_parts)
                except ValueError:
                    continue # cotton_dir_name not in path

                content = path.read_text()
                dependencies = set()

                for match in self.tag_pattern.finditer(content):
                    component_dep_name = match.group(2)
                    dependencies.add(component_dep_name)

                graph[component_name] = sorted(list(dependencies))
            except Exception as e:
                self.stderr.write(f"Error processing {path}: {e}")

        return graph

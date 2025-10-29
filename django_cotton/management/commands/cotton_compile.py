import json
import os
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from django_cotton import manifest
from django_cotton.compiler_regex import CottonCompiler
from django_cotton.component_paths import generate_component_template_path


class Command(BaseCommand):
    help = "Pre-compiles cotton templates and writes an optional manifest for runtime warmup."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            help="Path to write the manifest JSON. If omitted, the manifest is only loaded into the current process.",
        )
        parser.add_argument(
            "--include-non-components",
            action="store_true",
            help="Include templates without <c-…> tags in the manifest.",
        )

    def handle(self, *args, **options):
        compiler = CottonCompiler()
        include_non_components = options["include_non_components"]
        output_path = options.get("output")

        templates = self._discover_templates()
        if not templates:
            raise CommandError("No cotton templates were discovered.")

        compilation_results = []

        for template_path, template_name in templates:
            try:
                content = template_path.read_text(encoding="utf-8")
            except OSError as exc:
                self.stderr.write(f"Skipping {template_path}: {exc}")
                continue

            needs_processing = "<c-" in content or "{% cotton_verbatim" in content

            if not needs_processing and not include_non_components:
                continue

            dependencies = compiler.get_component_dependencies(content) if needs_processing else []
            compiled = compiler.process(content) if needs_processing else content
            mtime = template_path.stat().st_mtime
            compilation_results.append(
                {
                    "path": str(template_path),
                    "template_name": template_name,
                    "compiled": compiled,
                    "dependencies": dependencies,
                    "mtime": mtime,
                    "content": content,
                    "needs_processing": needs_processing,
                }
            )

            self.stdout.write(f"Compiled {template_path}")

        purity_map = self._compute_purity_map(compilation_results)

        manifest_entries = []
        for result in compilation_results:
            pure = purity_map.get(result["template_name"], False)
            manifest.store_entry(
                result["path"],
                result["compiled"],
                tuple(result["dependencies"]),
                result["mtime"],
                pure,
            )
            manifest_entries.append(
                {
                    "path": result["path"],
                    "compiled": result["compiled"],
                    "dependencies": result["dependencies"],
                    "mtime": result["mtime"],
                    "pure": pure,
                }
            )

        if output_path:
            output_dir = os.path.dirname(output_path)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            payload = {"templates": manifest_entries}

            try:
                with open(output_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
            except OSError as exc:
                raise CommandError(f"Unable to write manifest: {exc}") from exc

            self.stdout.write(self.style.SUCCESS(f"Manifest written to {output_path}"))

        self.stdout.write(self.style.SUCCESS("Cotton templates compiled."))

    def _discover_templates(self):
        cotton_dir_name = getattr(settings, "COTTON_DIR", "cotton")
        template_dirs = set()

        for tpl_setting in settings.TEMPLATES:
            template_dirs.update(tpl_setting.get("DIRS", []))

        for app_config in apps.get_app_configs():
            template_dir = os.path.join(app_config.path, "templates")
            if os.path.isdir(template_dir):
                template_dirs.add(template_dir)

        base_dir = getattr(settings, "BASE_DIR", None)
        if base_dir:
            root_template_dir = os.path.join(base_dir, "templates")
            if os.path.isdir(root_template_dir):
                template_dirs.add(root_template_dir)

        cotton_files = {}
        for template_dir in template_dirs:
            cotton_dir = Path(template_dir) / cotton_dir_name
            if cotton_dir.is_dir():
                for root, _, files in os.walk(cotton_dir):
                    for file in files:
                        if file.endswith(".html"):
                            path = Path(root) / file
                            try:
                                template_name = path.relative_to(template_dir).as_posix()
                            except ValueError:
                                continue
                            cotton_files[str(path)] = (path, template_name)

        return sorted(cotton_files.values(), key=lambda item: item[1])

    def _compute_purity_map(self, compilation_results):
        """
        Returns a mapping of template_name -> bool indicating whether the component
        can skip context isolation.
        """
        hazard_markers = [
            "cotton:impure",
            "{% include",
            "{% extends",
            "{% block ",
            "{% with ",
            "{% regroup",
            "{% autoescape",
        ]
        force_pure_marker = "cotton:pure"

        name_to_info = {}
        for result in compilation_results:
            name_to_info[result["template_name"]] = {
                "candidate": False,
                "dependencies": [],
                "pure": False,
            }

        for result in compilation_results:
            template_name = result["template_name"]
            content = result["content"]

            force_impure = any(marker in content for marker in ("cotton:impure", "cotton:pure=false"))
            force_pure = force_pure_marker in content

            candidate = False
            if not force_impure:
                if force_pure:
                    candidate = True
                else:
                    candidate = not any(marker in content for marker in hazard_markers)

            dependencies = []
            if result["needs_processing"]:
                for dep in result["dependencies"]:
                    dep_template_name = generate_component_template_path(dep, None)
                    dependencies.append(dep_template_name)

            info = name_to_info[template_name]
            info["candidate"] = candidate
            info["dependencies"] = dependencies

            if not candidate and force_pure:
                info["candidate"] = True

            for dep in dependencies:
                if dep not in name_to_info:
                    info["candidate"] = False
                    break

        changed = True
        while changed:
            changed = False
            for template_name, info in name_to_info.items():
                if info["pure"] or not info["candidate"]:
                    continue

                if all(name_to_info.get(dep, {}).get("pure", False) for dep in info["dependencies"]):
                    info["pure"] = True
                    changed = True

        return {name: info["pure"] for name, info in name_to_info.items()}

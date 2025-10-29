import json
import os
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from django_cotton import manifest
from django_cotton.compiler_regex import CottonCompiler


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

        manifest_entries = []

        for template_path in templates:
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

            manifest.store_entry(str(template_path), compiled, tuple(dependencies), mtime)
            manifest_entries.append(
                {
                    "path": str(template_path),
                    "compiled": compiled,
                    "dependencies": dependencies,
                    "mtime": mtime,
                }
            )

            self.stdout.write(f"Compiled {template_path}")

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

        cotton_files = set()
        for template_dir in template_dirs:
            cotton_dir = Path(template_dir) / cotton_dir_name
            if cotton_dir.is_dir():
                for root, _, files in os.walk(cotton_dir):
                    for file in files:
                        if file.endswith(".html"):
                            cotton_files.add(Path(root) / file)

        return sorted(cotton_files)

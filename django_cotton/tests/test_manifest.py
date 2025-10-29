import json

import pytest

from django_cotton import manifest
from django_cotton.dependency_registry import get_dependencies


@pytest.fixture(autouse=True)
def reset_manifest_state():
    manifest._reset_for_tests()
    yield
    manifest._reset_for_tests()


def test_manifest_loading_registers_dependencies(settings, tmp_path):
    template_file = tmp_path / "cotton" / "component.html"
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text("<c-child></c-child>", encoding="utf-8")
    mtime = template_file.stat().st_mtime

    manifest_file = tmp_path / "manifest.json"
    payload = {
        "templates": [
            {
                "path": str(template_file),
                "compiled": "compiled-value",
                "dependencies": ["child"],
                "mtime": mtime,
            }
        ]
    }
    manifest_file.write_text(json.dumps(payload), encoding="utf-8")

    settings.COTTON_MANIFEST_PATH = str(manifest_file)

    entry = manifest.get_precompiled(str(template_file))
    assert entry is not None
    assert entry.compiled == "compiled-value"
    assert entry.dependencies == ("child",)

    deps = get_dependencies(str(template_file))
    assert deps == ("child",)


def test_manifest_skips_stale_entries(settings, tmp_path):
    template_file = tmp_path / "component.html"
    template_file.write_text("<c-child></c-child>", encoding="utf-8")

    manifest_file = tmp_path / "manifest.json"
    payload = {
        "templates": [
            {
                "path": str(template_file),
                "compiled": "compiled-value",
                "dependencies": ["child"],
                "mtime": 0.0,
            }
        ]
    }
    manifest_file.write_text(json.dumps(payload), encoding="utf-8")

    settings.COTTON_MANIFEST_PATH = str(manifest_file)

    assert manifest.get_precompiled(str(template_file)) is None

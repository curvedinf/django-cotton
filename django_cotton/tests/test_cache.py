import pytest
from django.template import Origin

from django_cotton.cotton_loader import CottonTemplateCacheHandler


@pytest.fixture
def template_file(tmp_path):
    path = tmp_path / "cotton" / "component.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<c-button></c-button>", encoding="utf-8")
    return path


def test_local_cache_roundtrip(settings, template_file):
    settings.COTTON_CACHE_STRATEGY = "local"
    handler = CottonTemplateCacheHandler()

    origin = Origin(name=str(template_file), template_name="cotton/component.html", loader=None)
    cache_key = handler.get_cache_key(origin)

    assert handler.get_cached_template(cache_key) is None

    handler.cache_template(cache_key, "compiled", ["button"])
    cached = handler.get_cached_template(cache_key)
    assert cached.compiled == "compiled"
    assert cached.dependencies == ("button",)

    handler.reset()
    assert handler.get_cached_template(cache_key) is None


def test_django_cache_roundtrip(settings, template_file):
    settings.COTTON_CACHE_STRATEGY = "django"
    settings.COTTON_CACHE_PREFIX = "test:"

    handler = CottonTemplateCacheHandler()
    origin = Origin(name=str(template_file), template_name="cotton/component.html", loader=None)
    cache_key = handler.get_cache_key(origin)

    handler.cache_template(cache_key, "compiled-remote", ["button"])
    cached = handler.get_cached_template(cache_key)
    assert cached.compiled == "compiled-remote"
    assert cached.dependencies == ("button",)


def test_invalid_strategy(settings):
    settings.COTTON_CACHE_STRATEGY = "invalid"
    with pytest.raises(ValueError):
        CottonTemplateCacheHandler()

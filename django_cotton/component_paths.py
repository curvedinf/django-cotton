from __future__ import annotations

from functools import lru_cache
from typing import Optional

from django.conf import settings

from django_cotton.exceptions import CottonIncompleteDynamicComponentError


@lru_cache(maxsize=400)
def generate_component_template_path(component_name: str, is_: Optional[str]) -> str:
    """
    Compute the template path for a component name, matching Cotton's legacy rules.

    This helper centralises the path generation so Python and optional accelerators
    stay in sync. Results are cached because the same components are typically
    requested repeatedly within a render.
    """
    if component_name == "component":
        if is_ is None:
            raise CottonIncompleteDynamicComponentError(
                'Cotton error: "<c-component>" should be accompanied by an "is" attribute.'
            )
        component_name = is_

    component_tpl_path = component_name.replace(".", "/")

    snaked_cased_named = getattr(settings, "COTTON_SNAKE_CASED_NAMES", True)
    if snaked_cased_named:
        component_tpl_path = component_tpl_path.replace("-", "_")

    cotton_dir = getattr(settings, "COTTON_DIR", "cotton")
    return f"{cotton_dir}/{component_tpl_path}.html"

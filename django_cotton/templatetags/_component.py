from typing import Iterable, Union

from wove import weave

from django.conf import settings
from django.template import Library, TemplateDoesNotExist
from django.template.base import (
    Node,
)
from django.template.context import Context, RequestContext
from django.template.loader import get_template

from django_cotton import manifest
from django_cotton.utils import get_cotton_data
from django_cotton.templatetags import Attrs, DynamicAttr, UnprocessableDynamicAttr
from django_cotton.dependency_registry import get_dependencies
from django_cotton.component_paths import generate_component_template_path
from django_cotton.preload import preload_dependency_tree

register = Library()


class CottonComponentNode(Node):
    def __init__(self, component_name, nodelist, attrs, only):
        self.component_name = component_name
        self.nodelist = nodelist
        self.attrs = attrs
        self.template_cache = {}
        self.only = only

    def render(self, context):
        cotton_data = get_cotton_data(context)

        # Push a new component onto the stack
        component_data = {
            "key": self.component_name,
            "attrs": Attrs({}),
            "slots": {},
        }
        cotton_data["stack"].append(component_data)

        # Process simple attributes and boolean attributes
        force_pure = False
        force_impure = False
        for key, value in self.attrs.items():
            value = self._strip_quotes_safely(value)
            if key in {"cotton:pure", "cotton:impure"}:
                if isinstance(value, bool):
                    truthy = value
                else:
                    normalized = (value or "").lower()
                    truthy = normalized not in ("false", "0", "off", "no", "")
                if key == "cotton:pure":
                    if truthy:
                        force_pure = True
                    else:
                        force_impure = True
                else:
                    if truthy:
                        force_impure = True
                    else:
                        force_pure = True
                continue
            if value is True:  # Boolean attribute
                component_data["attrs"][key] = True
            elif key.startswith("::"):  # Escaping 1 colon e.g for shorthand alpine
                key = key[1:]
                component_data["attrs"][key] = value
            elif key.startswith(":"):
                key = key[1:]
                try:
                    resolved_value = DynamicAttr(value).resolve(context)
                except UnprocessableDynamicAttr:
                    component_data["attrs"].unprocessable(key)
                else:
                    # Handle ":attrs" specially
                    if key == "attrs":
                        component_data["attrs"].dict.update(resolved_value)
                    else:
                        component_data["attrs"][key] = resolved_value
            else:
                component_data["attrs"][key] = value

        # Render the nodelist to process any slot tags and vars
        default_slot = self.nodelist.render(context)

        # Prepare the cotton-specific data
        component_state = {
            **component_data["slots"],
            **component_data["attrs"].make_attrs_accessible(),
            "attrs": component_data["attrs"],
            "slot": default_slot,
            "cotton_data": cotton_data,
        }

        template = self._get_cached_template(context, component_data["attrs"])
        template_is_pure = self._template_is_pure(template)
        if force_impure:
            template_is_pure = False
        elif force_pure:
            template_is_pure = True

        cotton_data.setdefault("preloaded_components", set())
        self._preload_dependencies(template, cotton_data)

        if self.only:
            # Complete isolation
            output = template.render(Context(component_state))
        else:
            isolation_enabled = getattr(settings, "COTTON_ENABLE_CONTEXT_ISOLATION", False)
            if isolation_enabled and not template_is_pure:
                # Default - partial isolation
                new_context = self._create_partial_context(context, component_state)
                output = template.render(new_context)
            else:
                # Legacy - no isolation
                with context.push(component_state):
                    output = template.render(context)

        cotton_data["stack"].pop()

        return output

    def _get_cached_template(self, context, attrs):
        cache = context.render_context.get(self)
        if cache is None:
            cache = context.render_context[self] = {}

        template_path = self._generate_component_template_path(self.component_name, attrs.get("is"))

        if template_path in cache:
            return cache[template_path]

        # Check if the template was preloaded by a parent
        cotton_data = get_cotton_data(context)
        if "preloaded_templates" in cotton_data and template_path in cotton_data["preloaded_templates"]:
            compiled_content = cotton_data["preloaded_templates"][template_path]
            # We need to create a Template object from the compiled string
            from django.template import Template
            template = Template(compiled_content)
            self._mark_template(template, template_path)
            cache[template_path] = template
            return template

        # Try to get the primary template
        try:
            template = get_template(template_path)
            if hasattr(template, "template"):
                template = template.template
            self._mark_template(template, template_path)
            cache[template_path] = template
            return template
        except TemplateDoesNotExist:
            # If the primary template doesn't exist, try the fallback path (index.html)
            fallback_path = template_path.rsplit(".html", 1)[0] + "/index.html"

            # Check if the fallback template is already cached
            if fallback_path in cache:
                return cache[fallback_path]

            # Try to get the fallback template
            template = get_template(fallback_path)
            if hasattr(template, "template"):
                template = template.template
            self._mark_template(template, fallback_path)
            cache[fallback_path] = template
            return template

    def _create_partial_context(self, original_context, component_state):
        # Get the request object from the original context
        request = original_context.get("request")

        if request:
            # Create a new RequestContext
            new_context = RequestContext(request)

            # Add the component_state to the new context
            new_context.update(component_state)
        else:
            # If there's no request object, create a simple Context
            new_context = Context(component_state)

        return new_context

    @staticmethod
    def _generate_component_template_path(component_name: str, is_: Union[str, None]) -> str:
        """Generate the path to the template for the given component name."""
        return generate_component_template_path(component_name, is_)

    @staticmethod
    def _strip_quotes_safely(value):
        if type(value) is str and value.startswith('"') and value.endswith('"'):
            return value[1:-1]
        return value

    @staticmethod
    def _mark_template(template, template_path):
        if not getattr(template, "_cotton_template_path", None):
            setattr(template, "_cotton_template_path", template_path)
        origin = getattr(template, "origin", None)
        origin_name = getattr(origin, "name", None)
        if origin_name and not getattr(template, "_cotton_template_origin", None):
            setattr(template, "_cotton_template_origin", origin_name)

    def _template_is_pure(self, template):
        cached = getattr(template, "_cotton_template_pure", None)
        if cached is not None:
            return cached

        template_origin = getattr(template, "_cotton_template_origin", None)
        template_path = getattr(template, "_cotton_template_path", None)
        pure = False
        if template_origin:
            entry = manifest.get_precompiled(template_origin)
            if entry is not None:
                pure = getattr(entry, "pure", False)

        if template_path:
            entry = manifest.get_precompiled(template_path)
            if entry is not None:
                pure = getattr(entry, "pure", False) or pure

        setattr(template, "_cotton_template_pure", pure)
        return pure

    def _preload_dependencies(self, template, cotton_data):
        origin = getattr(template, "origin", None)
        template_path = getattr(origin, "name", None)
        if not template_path:
            return

        dependencies = get_dependencies(template_path)
        if not dependencies:
            return

        preloaded = cotton_data.setdefault("preloaded_components", set())

        initial_targets = []
        for dep in dependencies:
            target = self._generate_component_template_path(dep, None)
            if target not in preloaded:
                initial_targets.append(target)

        if not initial_targets:
            return

        preload_async = getattr(settings, "COTTON_ASYNC_PRELOAD", True)
        preload_transitive = getattr(settings, "COTTON_PRELOAD_TRANSITIVE", True)

        def load_template(path: str) -> None:
            try:
                get_template(path)
            except TemplateDoesNotExist:
                # Best effort preloading; ignore missing components.
                pass

        def batch_executor(batch: Iterable[str], loader) -> None:
            batch_list = list(batch)
            if not batch_list:
                return
            if not preload_async or len(batch_list) == 1:
                for item in batch_list:
                    loader(item)
                return

            with weave() as w:
                @w.do(batch_list)
                def preload_task(target):
                    loader(target)

            # Force evaluation to surface exceptions.
            w.result.preload_task

        newly_loaded = preload_dependency_tree(
            initial_targets,
            resolve_component=lambda name: self._generate_component_template_path(name, None),
            load_template=load_template,
            transitive=preload_transitive,
            batch_executor=batch_executor,
            seen=preloaded,
        )

        preloaded.update(newly_loaded)


def cotton_component(parser, token):
    """
    Parse a cotton component tag and return a CottonComponentNode.

    It accepts spaces inside quoted attributes for example if we want to pass valid json that contains spaces in values.

    @TODO Add support here for 'complex' attributes so we can eventually remove the need for the 'attr' tag. The idea
     here is to render `{{` and `{%` blocks in tags.
    """

    bits = token.split_contents()[1:]
    component_name = bits[0]
    attrs = {}
    only = False

    current_key = None
    current_value = []

    for bit in bits[1:]:
        if bit == "only":
            only = True
            continue

        if "=" in bit:
            # If we were building a previous value, store it
            if current_key:
                attrs[current_key] = " ".join(current_value)
                current_value = []

            # Start new key-value pair
            key, value = bit.split("=", 1)
            if value.startswith(("'", '"')):
                if value.endswith(("'", '"')) and value[0] == value[-1]:
                    # Complete quoted value
                    attrs[key] = value
                else:
                    # Start of quoted value
                    current_key = key
                    current_value = [value]
            else:
                # Simple unquoted value
                attrs[key] = value
        else:
            if current_key:
                # Continue building quoted value
                current_value.append(bit)
            else:
                # Boolean attribute
                attrs[bit] = True

    # Store any final value being built
    if current_key:
        attrs[current_key] = " ".join(current_value)

    nodelist = parser.parse(("endc",))
    parser.delete_first_token()

    return CottonComponentNode(component_name, nodelist, attrs, only)

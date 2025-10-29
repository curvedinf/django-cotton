import ast
from typing import Iterable, Union

from wove import weave

from django.conf import settings
from django.template import Library, TemplateDoesNotExist
from django.template.base import Node, Template, TemplateSyntaxError, Variable, VariableDoesNotExist
from django.template.context import Context, RequestContext
from django.template.loader import get_template

from django_cotton import manifest
from django_cotton.utils import get_cotton_data
from django_cotton.templatetags import Attrs, UnprocessableDynamicAttr
from django_cotton.dependency_registry import get_dependencies
from django_cotton.component_paths import generate_component_template_path
from django_cotton.preload import preload_dependency_tree

register = Library()


class PreparedDynamicValue:
    """Precompiled dynamic attribute resolver that avoids per-render object creation."""

    __slots__ = ("_raw", "_variable", "_template", "_literal_cached", "_literal_value")

    def __init__(self, raw):
        self._raw = raw
        self._variable = self._prepare_variable(raw) if isinstance(raw, str) else None
        self._template = None
        self._literal_cached = False
        self._literal_value = None

    def resolve(self, context):
        if not isinstance(self._raw, str):
            return self._raw

        if self._variable is not None:
            try:
                value = self._variable.resolve(context)
            except (VariableDoesNotExist, TemplateSyntaxError):
                value = None
            else:
                if isinstance(value, Attrs):
                    return value.attrs_dict()
                return value

        if self._raw == "":
            return True

        template_value = self._render_template(context)
        if template_value is not None:
            return template_value

        return self._literal_value_or_raise()

    def _prepare_variable(self, raw):
        try:
            return Variable(raw)
        except (TemplateSyntaxError, ValueError):
            return None

    def _render_template(self, context):
        if self._template is False:
            return None

        if self._template is None:
            try:
                self._template = Template(self._raw)
            except TemplateSyntaxError:
                self._template = False
                return None

        try:
            rendered = self._template.render(context)
        except TemplateSyntaxError:
            return None

        if rendered != self._raw:
            return rendered

        return None

    def _literal_value_or_raise(self):
        if not self._literal_cached:
            try:
                self._literal_value = ast.literal_eval(self._raw)
            except (ValueError, SyntaxError) as exc:
                raise UnprocessableDynamicAttr from exc
            self._literal_cached = True
        return self._literal_value


class CottonComponentNode(Node):
    def __init__(self, component_name, nodelist, attrs, only):
        self.component_name = component_name
        self.nodelist = nodelist
        self.template_cache = {}
        self.only = only
        (
            self._attr_steps,
            self._force_pure_default,
            self._force_impure_default,
            self._static_is_value,
            self._has_dynamic_is,
        ) = self._prepare_attribute_steps(attrs)
        self.attrs = attrs
        self._template_path_cache = {}
        if not self._has_dynamic_is and (self.component_name != "component" or self._static_is_value is not None):
            primary = self._generate_component_template_path(self.component_name, self._static_is_value)
            fallback = self._compute_fallback_path(primary)
            self._template_path_cache[self._static_is_value] = (primary, fallback)

    def render(self, context):
        cotton_data = get_cotton_data(context)

        # Push a new component onto the stack
        component_data = {
            "key": self.component_name,
            "attrs": Attrs({}),
            "slots": {},
        }
        cotton_data["stack"].append(component_data)

        force_pure = self._force_pure_default
        force_impure = self._force_impure_default

        component_attrs = component_data["attrs"]
        for step_type, key, payload in self._attr_steps:
            if step_type == "static":
                component_attrs[key] = payload
                continue

            try:
                resolved_value = payload.resolve(context)
            except UnprocessableDynamicAttr:
                component_attrs.unprocessable(key)
                continue

            if step_type == "spread":
                component_attrs.dict.update(resolved_value)
            else:  # dynamic
                component_attrs[key] = resolved_value

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

        template_path, fallback_path = self._resolve_template_paths(attrs)

        if template_path in cache:
            return cache[template_path]

        # Check if the template was preloaded by a parent
        cotton_data = get_cotton_data(context)
        if "preloaded_templates" in cotton_data and template_path in cotton_data["preloaded_templates"]:
            compiled_content = cotton_data["preloaded_templates"][template_path]
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
        request = None
        if hasattr(original_context, "get"):
            request = original_context.get("request")
        elif hasattr(original_context, "request"):
            request = original_context.request

        if request:
            # Create a new RequestContext
            new_context = RequestContext(request)

            # Add the component_state to the new context
            new_context.update(component_state)
        else:
            # If there's no request object, create a simple Context
            new_context = Context(component_state)

        return new_context

    def _prepare_attribute_steps(self, attrs):
        steps = []
        force_pure = False
        force_impure = False
        static_is_value = None
        has_dynamic_is = False

        for key, raw_value in attrs.items():
            value = self._strip_quotes_safely(raw_value) if isinstance(raw_value, str) else raw_value

            if key in {"cotton:pure", "cotton:impure"}:
                truthy = self._is_truthy(value)
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

            if value is True:
                steps.append(("static", key, True))
                if key == "is":
                    static_is_value = True
                continue

            if key.startswith("::"):
                actual_key = key[1:]
                steps.append(("static", actual_key, value))
                if actual_key == "is":
                    static_is_value = value
                continue

            if key.startswith(":"):
                actual_key = key[1:]
                resolver = self._compile_dynamic_value(value)
                if actual_key == "attrs":
                    steps.append(("spread", actual_key, resolver))
                else:
                    steps.append(("dynamic", actual_key, resolver))
                    if actual_key == "is":
                        has_dynamic_is = True
                continue

            steps.append(("static", key, value))
            if key == "is":
                static_is_value = value

        return steps, force_pure, force_impure, static_is_value, has_dynamic_is

    def _resolve_template_paths(self, attrs):
        attrs_dict = self._extract_attrs_dict(attrs)
        if attrs_dict is not None:
            is_value = attrs_dict.get("is")
        elif hasattr(attrs, "get"):
            is_value = attrs.get("is")
        else:
            is_value = None
        cache_key = is_value
        cached = self._template_path_cache.get(cache_key)
        if cached:
            return cached

        template_path = self._generate_component_template_path(self.component_name, is_value)
        fallback_path = self._compute_fallback_path(template_path)
        self._template_path_cache[cache_key] = (template_path, fallback_path)
        return template_path, fallback_path

    @staticmethod
    def _compile_dynamic_value(value):
        if isinstance(value, PreparedDynamicValue):
            return value
        return PreparedDynamicValue(value)

    @staticmethod
    def _compute_fallback_path(template_path: str) -> str:
        if ".html" in template_path:
            base, _ = template_path.rsplit(".html", 1)
            return f"{base}/index.html"
        return f"{template_path.rstrip('/')}/index.html"

    @staticmethod
    def _extract_attrs_dict(attrs):
        if isinstance(attrs, Attrs):
            return attrs.dict
        if isinstance(attrs, dict):
            return attrs
        return None

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
    def _is_truthy(value):
        if isinstance(value, bool):
            return value
        normalized = (value or "").lower()
        return normalized not in ("false", "0", "off", "no", "")

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

        dependency_cache = cotton_data.setdefault("preloaded_dependency_origins", set())
        if template_path in dependency_cache:
            return

        dependencies = get_dependencies(template_path)
        if not dependencies:
            dependency_cache.add(template_path)
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
        dependency_cache.add(template_path)


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

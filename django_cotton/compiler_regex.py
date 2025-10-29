import re
from typing import List, Tuple

from django.conf import settings

try:
    from django_cotton._fastcompiler import get_dependencies as fast_get_dependencies
    from django_cotton._fastcompiler import process as fast_process
    from django_cotton._fastcompiler import process_with_dependencies as fast_process_with_dependencies
except ImportError:  # pragma: no cover - optional acceleration
    fast_get_dependencies = None
    fast_process = None
    fast_process_with_dependencies = None


class Tag:
    tag_pattern = re.compile(
        r"<(/?)c-([^\s/>]+)((?:\s+[^\s/>\"'=<>`]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|\S+))?)*)\s*(/?)\s*>",
        re.DOTALL,
    )
    attr_pattern = re.compile(r'([^\s/>\"\'=<>`]+)(?:\s*=\s*(?:(["\'])(.*?)\2|(\S+)))?', re.DOTALL)

    def __init__(self, match: re.Match):
        self.html = match.group(0)
        self.tag_name = f"c-{match.group(2)}"
        self.attrs = match.group(3) or ""
        self.is_closing = bool(match.group(1))
        self.is_self_closing = bool(match.group(4))

    def get_template_tag(self) -> str:
        """Convert a cotton tag to a Django template tag"""
        if self.tag_name == "c-vars":
            return ""  # c-vars tags will be handled separately
        elif self.tag_name == "c-slot":
            return self._process_slot()
        elif self.tag_name.startswith("c-"):
            return self._process_component()
        else:
            return self.html

    def _process_slot(self) -> str:
        """Convert a c-slot tag to a Django template slot tag"""
        if self.is_closing:
            return "{% endslot %}"
        name_match = re.search(r'name=(["\'])(.*?)\1', self.attrs, re.DOTALL)
        if not name_match:
            raise ValueError(f"c-slot tag must have a name attribute: {self.html}")
        slot_name = name_match.group(2)
        return f"{{% slot {slot_name} %}}"

    def _process_component(self) -> str:
        """Convert a c- component tag to a Django template component tag"""
        component_name = self.tag_name[2:]
        if self.is_closing:
            return "{% endc %}"
        processed_attrs, extracted_attrs = self._process_attributes()
        opening_tag = f"{{% c {component_name}{processed_attrs} %}}"
        if self.is_self_closing:
            return f"{opening_tag}{extracted_attrs}{{% endc %}}"
        return f"{opening_tag}{extracted_attrs}"

    def _process_attributes(self) -> Tuple[str, str]:
        """Move any complex attributes to the {% attr %} tag"""
        processed_attrs = []
        extracted_attrs = []

        for match in self.attr_pattern.finditer(self.attrs):
            key, quote, value, unquoted_value = match.groups()
            if value is None and unquoted_value is None:
                processed_attrs.append(key)
            else:
                actual_value = value if value is not None else unquoted_value
                if any(s in actual_value for s in ("{{", "{%", "=", "__COTTON_IGNORE_")):
                    extracted_attrs.append(f"{{% attr {key} %}}{actual_value}{{% endattr %}}")
                else:
                    processed_attrs.append(f'{key}="{actual_value}"')

        attrs_string = " " + " ".join(processed_attrs) if processed_attrs else ""
        return attrs_string, "".join(extracted_attrs)


class CottonCompiler:
    def __init__(self):
        self.c_vars_pattern = re.compile(r"<c-vars\s([^>]*)(?:/>|>(.*?)</c-vars>)", re.DOTALL)
        self.ignore_pattern = re.compile(
            # cotton_verbatim isnt a real template tag, it's just a way to ignore <c-* tags from being compiled
            r"({%\s*cotton_verbatim\s*%}.*?{%\s*endcotton_verbatim\s*%}|"
            # Ignore both forms of comments
            r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}|"
            # Ignore django template tags and variables
            r"{{.*?}}|{%.*?%})",
            re.DOTALL,
        )
        self.cotton_verbatim_pattern = re.compile(
            r"{%\s*cotton_verbatim\s*%}(.*?){%\s*endcotton_verbatim\s*%}", re.DOTALL
        )
        accel_setting = getattr(settings, "COTTON_USE_ACCELERATOR", False)
        if isinstance(accel_setting, str) and accel_setting.lower() == "auto":
            use_accel = bool(fast_process and fast_get_dependencies)
        else:
            use_accel = bool(accel_setting and fast_process and fast_get_dependencies)

        self._use_accelerator = use_accel
        self._has_combined_accelerator = bool(self._use_accelerator and fast_process_with_dependencies)

    def exclude_ignorables(self, html: str) -> Tuple[str, List[Tuple[str, str]]]:
        ignorables = []

        def replace_ignorable(match):
            placeholder = f"__COTTON_IGNORE_{len(ignorables)}__"
            ignorables.append((placeholder, match.group(0)))
            return placeholder

        processed_html = self.ignore_pattern.sub(replace_ignorable, html)
        return processed_html, ignorables

    def restore_ignorables(self, html: str, ignorables: List[Tuple[str, str]]) -> str:
        for placeholder, content in ignorables:
            if content.strip().startswith("{% cotton_verbatim %}"):
                # Extract content between cotton_verbatim tags, we don't want to leave these in
                match = self.cotton_verbatim_pattern.search(content)
                if match:
                    content = match.group(1)
            html = html.replace(placeholder, content)
        return html

    def get_replacements(self, html: str) -> List[Tuple[str, str]]:
        replacements = []
        for match in Tag.tag_pattern.finditer(html):
            tag = Tag(match)
            try:
                template_tag = tag.get_template_tag()
                if template_tag != tag.html:
                    replacements.append((tag.html, template_tag))
            except ValueError as e:
                # Find the line number of the error
                position = match.start()
                line_number = html[:position].count("\n") + 1
                raise ValueError(f"Error in template at line {line_number}: {str(e)}") from e

        return replacements

    def process_c_vars(self, html: str) -> Tuple[str, str]:
        """
        Extract c-vars content and remove c-vars tags from the html.
        Raises ValueError if more than one c-vars tag is found.
        """
        # Find all matches of c-vars tags
        matches = list(self.c_vars_pattern.finditer(html))

        if len(matches) > 1:
            raise ValueError(
                "Multiple c-vars tags found in component template. Only one c-vars tag is allowed per template."
            )

        # Process single c-vars tag if present
        match = matches[0] if matches else None
        if match:
            attrs = match.group(1)
            vars_content = f"{{% vars {attrs.strip()} %}}"
            html = self.c_vars_pattern.sub("", html)  # Remove all c-vars tags
            return vars_content, html

        return "", html

    def get_component_dependencies(self, html: str) -> List[str]:
        if self._use_accelerator:
            try:
                result = fast_get_dependencies(html)
                return list(result)
            except ValueError:
                # Fall back to the pure-Python implementation if the accelerator
                # cannot parse the template (e.g. malformed c-vars blocks).
                pass

        dependencies = []
        processed_html, _ = self.exclude_ignorables(html)
        for match in Tag.tag_pattern.finditer(processed_html):
            is_closing = bool(match.group(1))
            if is_closing:
                continue

            component_name_part = match.group(2)
            if component_name_part.startswith("__COTTON_IGNORE_"):
                continue

            tag_name = f"c-{component_name_part}"
            attrs = match.group(3) or ""

            if tag_name == "c-component":
                # Dynamic component, look for 'is' attribute
                is_match = re.search(r'is=(["\'])(.*?)\1', attrs)
                if is_match:
                    component_name = is_match.group(2)
                    # We can only handle static component names here
                    if not component_name.startswith("__COTTON_IGNORE_"):
                        dependencies.append(component_name)
            elif tag_name.startswith("c-") and tag_name not in ["c-vars", "c-slot"]:
                component_name = tag_name[2:]
                dependencies.append(component_name)

        # Preserve order while removing duplicates
        seen = set()
        ordered = []
        for dep in dependencies:
            if dep not in seen:
                ordered.append(dep)
                seen.add(dep)

        return ordered

    def process(self, html: str) -> str:
        """Putting it all together"""
        if self._use_accelerator:
            try:
                return fast_process(html)
            except ValueError:
                # Fall back to the Python implementation if the accelerator fails.
                pass

        processed_html, ignorables = self.exclude_ignorables(html)
        vars_content, processed_html = self.process_c_vars(processed_html)
        replacements = self.get_replacements(processed_html)
        for original, replacement in replacements:
            processed_html = processed_html.replace(original, replacement)
        if vars_content:
            processed_html = f"{vars_content}{processed_html}{{% endvars %}}"
        return self.restore_ignorables(processed_html, ignorables)

    def compile_with_dependencies(self, html: str) -> Tuple[str, Tuple[str, ...]]:
        """
        Return the compiled template string along with the component dependency list.

        When the optional Rust accelerator is present we offload the heavy work
        to it. Otherwise we can optionally use Wove to run the pure-Python
        compilation and dependency discovery in parallel.
        """
        if self._has_combined_accelerator:
            try:
                compiled, deps = fast_process_with_dependencies(html)
                return compiled, tuple(deps)
            except ValueError:
                # Fallback handled below.
                pass

        parallel_compile = getattr(settings, "COTTON_PARALLEL_COMPILE", True)
        if parallel_compile and not self._has_combined_accelerator:
            from wove import weave  # Imported lazily to avoid overhead during import time.

            with weave() as w:
                @w.do
                def compiled_template():
                    return self.process(html)

                @w.do
                def component_dependencies():
                    return tuple(self.get_component_dependencies(html))

            compiled = w.result.compiled_template
            dependencies = tuple(w.result.component_dependencies)
            return compiled, dependencies

        dependencies = tuple(self.get_component_dependencies(html))
        compiled = self.process(html)
        return compiled, dependencies

from django import template
from django.utils.html import format_html_join
from django.template import Node

from django_cotton.templatetags._component import cotton_component, CottonComponentNode
from django_cotton.templatetags._vars import cotton_cvars
from django_cotton.templatetags._slot import cotton_slot
from django_cotton.templatetags._attr import cotton_attr
from ..concurrent_loader import load_and_compile_templates
from ..utils import get_cotton_data


class PreloadNode(Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist
        self.child_component_nodes = [
            node for node in nodelist if isinstance(node, CottonComponentNode)
        ]

    def render(self, context):
        cotton_data = get_cotton_data(context)

        template_paths_to_load = []
        for node in self.child_component_nodes:
            is_attr = node.attrs.get("is", None)
            if is_attr:
                # This is a simplified version of quote stripping
                if is_attr.startswith(('"', "'")) and is_attr.endswith(('"', "'")):
                    is_attr = is_attr[1:-1]

            template_path = node._generate_component_template_path(node.component_name, is_attr)
            template_paths_to_load.append(template_path)

        if "preloaded_templates" not in cotton_data:
            cotton_data["preloaded_templates"] = {}

        paths_to_fetch = [p for p in template_paths_to_load if p not in cotton_data["preloaded_templates"]]
        if paths_to_fetch:
            compiled_templates = load_and_compile_templates(paths_to_fetch)
            cotton_data["preloaded_templates"].update(compiled_templates)

        return self.nodelist.render(context)

def cotton_preload(parser, token):
    nodelist = parser.parse(("end_cotton_preload",))
    parser.delete_first_token()
    return PreloadNode(nodelist)


register = template.Library()
register.tag("c", cotton_component)
register.tag("slot", cotton_slot)
register.tag("vars", cotton_cvars)
register.tag("attr", cotton_attr)
register.tag("cotton_preload", cotton_preload)
register.tag("end_cotton_preload", lambda parser, token: None) # Dummy tag for parsing


@register.filter
def merge(attrs, args):
    # attrs is expected to be a dictionary of existing attributes
    # args is a string of additional attributes to merge, e.g., "class:extra-class"
    for arg in args.split(","):
        key, value = arg.split(":", 1)
        if key in attrs:
            attrs[key] = value + " " + attrs[key]
        else:
            attrs[key] = value
    return format_html_join(" ", '{0}="{1}"', attrs.items())


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)

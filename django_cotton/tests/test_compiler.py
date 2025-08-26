
from django_cotton.compiler_regex import CottonCompiler
from django_cotton.tests.utils import CottonTestCase, get_compiled


class CompileTests(CottonTestCase):
    def test_get_component_dependencies(self):
        compiler = CottonCompiler()
        html = """
            <c-component is="static-component" />
            <c-component is="{{ dynamic_component }}" />
            <c-another-component />
            <c-a.b.c />
            <div>
                <c-nested-component />
            </div>
            <c-self-closing />
        """
        dependencies = compiler.get_component_dependencies(html)
        self.assertEqual(
            sorted(dependencies),
            sorted(['static-component', 'another-component', 'a.b.c', 'nested-component', 'self-closing'])
        )

    def test_regex_compile(self):
        self.create_template(
            "cotton/new_compiler.html",
            """
                <c-vars attrs1="111" />
                <div>
                    default: {{ slot }}
                    named: {{ named }}
                    lexed: {{ lexed }}
                </div>
            """,
        )

        self.create_template(
            "new_compiler_view.html",
            """
                <c-new-compiler
                    lexed="{{ some_var }}"
                >
                    Default!
                    <c-slot name="named">Named!</c-slot>
                </c-new-compiler>
            """,
            "view/",
            context={"some_var": "value"},
        )

        with self.settings(ROOT_URLCONF=self.url_conf()):
            response = self.client.get("/view/")
            self.assertContains(response, "default: \n                    Default!")
            self.assertContains(response, "named: Named!")
            self.assertContains(response, "lexed: value")

    def test_compile_stage_ignores_django_vars_and_tags(self):
        compiled = get_compiled(
            """
            {# I'm a comment with a cotton tag <c-vars /> #}
            {% comment %}I'm a django comment with a cotton tag <c-hello />{% endcomment %}
            {{ '<c-vars />'|safe }}
            {% cotton_verbatim %}<c-ignoreme />{% endcotton_verbatim %}
        """
        )

        self.assertTrue(
            "{# I'm a comment with a cotton tag <c-vars /> #}" in compiled,
            "Compilation should ignore comments",
        )

        self.assertTrue(
            "{% comment %}I'm a django comment with a cotton tag <c-hello />{% endcomment %}"
            in compiled,
            "Compilation should ignore comments",
        )

        self.assertTrue(
            "{{ '<c-vars />'|safe }}" in compiled,
            "Compilation should not touch the internals of variables or tags",
        )

        self.assertTrue(
            "<c-ignoreme />" in compiled,
            "{% cotton_verbatim %} contents should be left untouched",
        )

        self.assertTrue(
            "{% cotton_verbatim %}" not in compiled,
            "Compilation should not leave {% cotton_verbatim %} tags in the output",
        )

    def test_raises_error_on_duplicate_cvars(self):
        with self.assertRaises(ValueError) as cm:
            get_compiled(
                """
                <c-vars />
                <c-vars />
            """
            )

        self.assertEqual(
            str(cm.exception),
            "Multiple c-vars tags found in component template. Only one c-vars tag is allowed per template.",
        )

    def test_raises_on_slots_without_name(self):
        with self.assertRaises(ValueError) as cm:
            get_compiled(
                """
                <c-slot />
            """
            )

        self.assertTrue(
            "c-slot tag must have a name attribute:" in str(cm.exception),
        )

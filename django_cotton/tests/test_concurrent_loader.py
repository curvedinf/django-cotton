from django_cotton.tests.utils import CottonTestCase
from django_cotton.concurrent_loader import load_and_compile_templates


class ConcurrentLoaderTests(CottonTestCase):
    def test_load_and_compile_templates(self):
        # Create a template with cotton tags
        compiled_path = self.create_template(
            "cotton/test_component.html",
            """
            <c-vars my_var="hello" />
            <div>{{ my_var }}</div>
            """,
        )
        # Create a template without cotton tags
        plain_path = self.create_template(
            "cotton/plain_template.html", "<div>Just a plain template</div>"
        )

        template_paths = [compiled_path, plain_path]

        compiled_templates = load_and_compile_templates(template_paths)

        # Check the compiled template
        self.assertIn(compiled_path, compiled_templates)
        self.assertIn(
            '{% vars my_var="hello" %}', compiled_templates[compiled_path]
        )
        self.assertIn("{% endvars %}", compiled_templates[compiled_path])

        # Check the plain template (should be unchanged)
        self.assertIn(plain_path, compiled_templates)
        self.assertEqual(
            compiled_templates[plain_path], "<div>Just a plain template</div>"
        )

    def test_load_and_compile_with_error(self):
        # Test with a non-existent file
        template_paths = ["non_existent_file.html"]
        compiled_templates = load_and_compile_templates(template_paths)
        self.assertIn("non_existent_file.html", compiled_templates)
        self.assertIn("No such file or directory", compiled_templates["non_existent_file.html"])

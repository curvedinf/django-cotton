from django.test import override_settings

from django_cotton.tests.utils import CottonTestCase, get_rendered


class MiscComponentTests(CottonTestCase):
    def test_cotton_directory_can_be_configured(self):
        custom_dir = "components"

        self.create_template(
            f"{custom_dir}/custom_directory.html",
            """<div class="i-am-component">{{ slot }}</div>""",
        )

        self.create_template(
            "custom_directory_view.html",
            """<c-custom-directory>Hello, World!</c-custom-directory>""",
            "view/",
        )

        # Override URLconf
        with self.settings(ROOT_URLCONF=self.url_conf(), COTTON_DIR=custom_dir):
            response = self.client.get("/view/")
            self.assertContains(response, '<div class="i-am-component">')
            self.assertContains(response, "Hello, World!")

    def test_loader_scans_all_app_directories(self):
        self.create_template(
            "cotton/app_outside_of_dirs.html",
            "My template path was not specified in settings!",
        )
        self.create_template(
            "app_outside_of_dirs_view.html", """<c-app-outside-of-dirs />""", "view/"
        )

        with self.settings(ROOT_URLCONF=self.url_conf()):
            response = self.client.get("/view/")

            self.assertContains(
                response,
                """My template path was not specified in settings!""",
            )

    @override_settings(COTTON_SNAKE_CASED_NAMES=False)
    def test_hyphen_naming_convention(self):
        self.create_template(
            "cotton/some-subfolder/hyphen-naming-convention.html",
            "I have a hyphenated component name",
        )

        html = """
            <c-some-subfolder.hyphen-naming-convention />
        """

        rendered = get_rendered(html)

        self.assertTrue("I have a hyphenated component name" in rendered)

    def test_multiple_app_subdirectory_access(self):
        self.create_template(
            "cotton/app_dir.html",
            "I'm from app templates!",
        )
        self.create_template(
            "cotton/project_root.html",
            "I'm from project root templates!",
        )
        self.create_template(
            "cotton/app2/sub.html",
            "i'm sub in project root",
        )

        html = """
            <c-app-dir />
            <c-project-root />
            <c-app2.sub />
        """

        rendered = get_rendered(html)

        self.assertTrue("I'm from app templates!" in rendered)
        self.assertTrue("I'm from project root templates!" in rendered)
        self.assertTrue("i'm sub in project root" in rendered)

    def test_index_file_access(self):
        self.create_template(
            "cotton/accordion/index.html",
            "I'm an index file!",
        )

        html = """
            <c-accordion />
        """

        rendered = get_rendered(html)

        self.assertTrue("I'm an index file!" in rendered)

    def test_get_item_filter(self):
        rendered = get_rendered(
            "{{ my_dict|get_item:'my_key' }}",
            {"my_dict": {"my_key": "my_value"}},
        )
        self.assertEqual(rendered, "my_value")

    def test_merge_filter(self):
        # Test merging with an existing key
        rendered = get_rendered(
            '{{ attrs|merge:"class:extra-class" }}',
            {"attrs": {"class": "initial-class"}},
        )
        self.assertIn('class="extra-class initial-class"', rendered)

    def test_eval_string_invalid_string(self):
        from django_cotton.utils import eval_string

        self.assertEqual(eval_string("not a literal"), "not a literal")

    def test_ensure_quoted_already_quoted(self):
        from django_cotton.utils import ensure_quoted

        self.assertEqual(ensure_quoted('"already quoted"'), '"already quoted"')

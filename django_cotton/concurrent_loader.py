from wove import weave
from django_cotton.compiler_regex import CottonCompiler

def load_and_compile_templates(template_paths):
    """
    Loads and compiles a list of templates concurrently using wove.
    """
    compiled_templates = {}

    with weave() as w:
        compiler = CottonCompiler()

        @w.do(template_paths)
        def compiled_template(path):
            try:
                with open(path, "r") as f:
                    content = f.read()

                if "<c-" not in content and "{% cotton_verbatim" not in content:
                    return path, content
                else:
                    processed_content = compiler.process(content)
                    return path, processed_content
            except Exception as e:
                # In a real-world scenario, you might want better error handling
                return path, str(e)

    # The result of a mapped task is a list of its return values
    for path, content in w.result.compiled_template:
        compiled_templates[path] = content

    return compiled_templates

"""Derived class attributes, lambda-body edits, docstring prose and entry points (standard library only)."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from selector import Selector, baseline_tree, lambda_body_edit, prose_only_docstring_edit  # noqa: E402
from test_selector import diff  # noqa: E402
from tools.import_time import PACKAGE  # noqa: E402

CORE = f"{PACKAGE}/core.py"
MAIN = f"{PACKAGE}/__main__.py"

spec = importlib.util.spec_from_file_location("build_map", ROOT / "tools/build_map.py")
map_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(map_module)


class NoImportTime:
    def __init__(self, ran=()):
        self.ran = set(ran)

    def runs_during_import(self, path, lines):
        return bool(self.ran & set(lines))

    def owners(self, edits):
        return []

    def setup_owners(self, edits):
        return []


def build(root, text, lines, attrs=(), derived=()):
    """Write the package's core.py, a map from the given observations, and return (tests, map path)."""
    (root / PACKAGE).mkdir()
    (root / "tests").mkdir()
    source = root / CORE
    source.write_text(text)
    model = map_module.SourceModel(root)
    rec = map_module.Recorder(root, model, None)
    rec.tests = ["A", "B", "C"]
    for symbol, tests in attrs:
        rec.attrs[(CORE, symbol)].update(tests)
    for owner, tests in derived:
        rec.derived_reads[owner].update(tests)

    class Observations:
        def measured_files(self):
            return [str(source)]

        def contexts_by_lineno(self, filename):
            return lines

    output = root / "map.json.gz"
    map_module.write_map(root, model, rec, Observations(), output)
    return [{"number": i, "id": name, "seconds": .1} for i, name in enumerate(rec.tests)], output


class DerivedAttributeTests(unittest.TestCase):
    def test_attribute_set_at_class_creation_counts_for_every_class_in_the_mro(self):
        class Base:
            REQUIRED = frozenset()

            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)
                cls.REQUIRED = frozenset(k for k, v in cls.ARGS.items() if v)

        class Child(Base):
            ARGS = {"this": False}

        key = ("source.py", "Base.REQUIRED")
        model = types.SimpleNamespace(attr_names={"REQUIRED"},
                                      class_fields={(Base.__module__, Base.__qualname__, "REQUIRED"): key})
        rec = map_module.Recorder(Path("/tmp"), model, None)
        rec.current = "test_example"
        rec.record_attr(Child, "REQUIRED")
        self.assertNotIn(key, rec.attrs)
        # No hook known to the model: not followed (runtime caches and the like).
        self.assertEqual(rec.derived_reads, {})
        model.creation_hooks = {(Base.__module__, Base.__qualname__): None}
        rec = map_module.Recorder(Path("/tmp"), model, None)
        rec.current = "test_example"
        rec.record_attr(Child, "REQUIRED")
        self.assertIn("test_example", rec.derived_reads[(Child.__module__, Child.__qualname__, map_module.ALL_NAMES)])
        model.creation_hooks = {(Base.__module__, Base.__qualname__): frozenset({"ARGS"})}
        rec = map_module.Recorder(Path("/tmp"), model, None)
        rec.current = "test_example"
        rec.record_attr(Child, "REQUIRED")
        self.assertIn("test_example", rec.derived_reads[(Child.__module__, Child.__qualname__, frozenset({"ARGS"}))])

    def test_derived_reader_selected_for_a_class_body_assignment(self):
        text = "class Child:\n    ARGS = {'this': False}\n    NAME = 'x'\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests, path = build(root, text, {}, attrs=[("Child.NAME", {"C"})],
                                derived=[((f"{PACKAGE}.core", "Child", frozenset({"ARGS"})), {"B"})])
            selector = Selector(tests, root, path)
            patch = diff(CORE, text, text.replace("False", "True"))
            self.assertEqual(selector.select(patch), 0b010)
            # The hook does not read NAME, so its derived reader is not selected for it.
            self.assertEqual(selector.select(diff(CORE, text, text.replace("'x'", "'y'"))), 0b100)

    def test_creation_reads(self):
        import ast
        def reads(source):
            return map_module.creation_reads(ast.parse(source).body[0])
        self.assertEqual(reads("def h(cls):\n    cls.A = {k for k in cls.B}\n    getattr(cls, 'C', None)\n"),
                         frozenset({"B", "C"}))
        self.assertIsNone(reads("def h(cls):\n    helper(cls)\n"))
        self.assertIsNone(reads("def h(cls):\n    getattr(cls, name)\n"))
        meta = ast.parse("def __new__(mcs, name, bases, attrs):\n"
                         "    klass = super().__new__(mcs, name, bases, attrs)\n"
                         "    REGISTRY[name] = klass\n"
                         "    klass.T = trie(klass.MAPPING)\n"
                         "    klass.P = klass.__dict__.get('Parser', None)\n"
                         "    return klass\n").body[0]
        self.assertEqual(map_module.creation_reads(meta, metaclass=True) - {"__dict__"}, {"MAPPING", "Parser"})
        leaky = ast.parse("def __new__(mcs, *a):\n    klass = super().__new__(mcs, *a)\n    setup(klass)\n").body[0]
        self.assertIsNone(map_module.creation_reads(leaky, metaclass=True))


class LambdaBodyTests(unittest.TestCase):
    TEXT = ("class P:\n"
            "    TABLE = {\n"
            "        'a': lambda args: build(\n"
            "            args[0],\n"
            "        ),\n"
            "        'b': lambda args: other(args),\n"
            "    }\n")
    # Line 4 ran only in test A (a call); every test read the table.
    LINES = {2: ["A", "B", "C"], 4: ["A"]}

    def selector(self, root, **options):
        tests, path = build(root, self.TEXT, self.LINES, attrs=[("P.TABLE", {"A", "B", "C"})])
        return Selector(tests, root, path, **options)

    def test_body_edit_selects_only_callers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patch = diff(CORE, self.TEXT, self.TEXT.replace("args[0]", "args[1]"))
            self.assertEqual(self.selector(root).select(patch), 0b111)
            selector = self.selector_again(root, lambda_bodies=True, import_effects=NoImportTime())
            self.assertEqual(selector.select(patch), 0b001)

    def selector_again(self, root, **options):
        tests = [{"number": i, "id": name, "seconds": .1} for i, name in enumerate(["A", "B", "C"])]
        return Selector(tests, root, root / "map.json.gz", **options)

    def test_lambda_run_during_import_keeps_general_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.selector(root)
            patch = diff(CORE, self.TEXT, self.TEXT.replace("args[0]", "args[1]"))
            selector = self.selector_again(root, lambda_bodies=True, import_effects=NoImportTime({4}))
            self.assertEqual(selector.select(patch), 0b111)

    def test_uncalled_lambda_keeps_general_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.selector(root)
            patch = diff(CORE, self.TEXT, self.TEXT.replace("other(args)", "other(args, 1)"))
            selector = self.selector_again(root, lambda_bodies=True, import_effects=NoImportTime())
            self.assertEqual(selector.select(patch), 0b111)

    def test_lambda_detection(self):
        def span(old, new):
            return lambda_body_edit(baseline_tree(old), baseline_tree(new))
        self.assertEqual(span(self.TEXT, self.TEXT.replace("args[0]", "args[1]")), (3, 5))
        # Arguments and defaults are evaluated when the lambda is created.
        self.assertIsNone(span(self.TEXT, self.TEXT.replace("lambda args: build", "lambda args=1: build")))
        # An edit outside the lambda as well.
        self.assertIsNone(span(self.TEXT, self.TEXT.replace("args[0]", "args[1]").replace("'b'", "'c'")))
        # Called where it is defined.
        called = "X = (lambda: 1)()\n"
        self.assertIsNone(span(called, called.replace("1", "2")))
        # Inside a function the ordinary line rules already apply.
        nested = "def f():\n    return lambda: 1\n"
        self.assertIsNone(span(nested, nested.replace("1", "2")))


class DocstringTests(unittest.TestCase):
    OLD = 'def f():\n    """Return one."""\n    return 1\n'

    def check(self, new):
        return prose_only_docstring_edit(baseline_tree(self.OLD), baseline_tree(new))

    def test_prose_edit(self):
        self.assertTrue(self.check(self.OLD.replace("Return one.", "Return the number one.")))

    def test_doctest_examples_and_emptied_docstrings_count(self):
        self.assertFalse(self.check(self.OLD.replace("Return one.", ">>> f()\\n    1")))
        self.assertFalse(self.check(self.OLD.replace("Return one.", "")))
        self.assertFalse(self.check(self.OLD.replace("    return 1", "    return 2").replace("one.", "two.")))
        self.assertFalse(self.check(self.OLD.replace('    """Return one."""\n', "")))

    def test_selector_treats_prose_like_a_comment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests, path = build(root, self.OLD, {2: ["A"], 3: ["A"]})
            patch = diff(CORE, self.OLD, self.OLD.replace("Return one.", "Return 1."))
            self.assertNotEqual(Selector(tests, root, path).select(patch), 0)
            self.assertEqual(Selector(tests, root, path, docstring_edits=True).select(patch), 0)


class EntryPointTests(unittest.TestCase):
    def test_unmapped_main_selects_only_process_starting_tests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests, path = build(root, "X = 1\n", {1: ["A"]})
            main = "import sys\nprint(sys.argv)\n"
            (root / MAIN).write_text(main)
            patch = diff(MAIN, main, main.replace("argv", "argv[1:]"))
            selector = Selector(tests, root, path)
            self.assertEqual(selector.select(patch), selector.all)
            selector = Selector(tests, root, path, entry_point_edits=True)
            self.assertEqual(selector.select(patch), selector.external)
            broken = diff(MAIN, main, main.replace("print(sys.argv)", "print(sys.argv"))
            self.assertEqual(selector.select(broken), selector.all)


if __name__ == "__main__":
    unittest.main()

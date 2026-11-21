"""Key reads are recorded where a tracked mapping is consumed, not where it is copied."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from tools.build_map import Recorder, SourceModel, write_map
from tools.instrument import Instrumentation

CORE = '''
class Core:
    def __init__(self, escapes, single, extra=None):
        self.escapes = escapes
        self.single = single
        self.extra = extra

    def lookup(self, char):
        escapes = self.escapes
        return escapes.get(char)

    def subscript(self, char):
        return self.escapes[char]

    def present(self, char):
        return char in self.escapes

    def size(self):
        return len(self.single)

    def truthy(self):
        return bool(self.escapes) and 1

    def leak(self):
        return [self.escapes]
'''

SETTINGS = '''
from sqlglot.core import Core

class Settings:
    ESCAPES = {"x": 1, "u": 2}
    SINGLE = {"a": 1}

    def core(self):
        return Core(escapes=self.ESCAPES, single=self.SINGLE)

    def positional(self):
        return Core(self.ESCAPES, self.SINGLE)

    def foreign(self):
        return dict(escapes=self.ESCAPES)

class Child(Settings):
    pass

DERIVED = "x" in Settings.ESCAPES
'''


class AliasReadsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        package = self.root / "sqlglot"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "core.py").write_text(CORE)
        (package / "settings.py").write_text(SETTINGS)
        self.model = SourceModel(self.root)
        self.rec = Recorder(self.root, self.model, None)
        observer = Instrumentation(self.root, self.rec)
        observer.install()
        self.addCleanup(observer.restore)
        saved = {name: module for name, module in sys.modules.items() if name.split(".")[0] == "sqlglot"}
        for name in saved:
            del sys.modules[name]
        sys.path.insert(0, str(self.root))
        def cleanup():
            sys.path.remove(str(self.root))
            for name in [n for n in sys.modules if n.split(".")[0] == "sqlglot"]:
                del sys.modules[name]
            sys.modules.update(saved)
        self.addCleanup(cleanup)
        importlib.invalidate_caches()
        self.settings = importlib.import_module("sqlglot.settings")
        self.symbol = ("sqlglot/settings.py", "Settings.ESCAPES")
        self.single = ("sqlglot/settings.py", "Settings.SINGLE")

    def run_as(self, test, function, phase="call"):
        self.rec.current, self.rec.phase = test, phase
        try:
            return function()
        finally:
            self.rec.current, self.rec.phase = None, "collection"

    def test_alias_names_follow_keywords_and_assignments(self):
        self.assertTrue({"escapes", "single"} <= self.rec.dictionary_names)
        self.assertIn("escapes", self.rec.parameter_names["Core"])

    def test_import_time_reads_reach_every_test(self):
        self.assertIn(None, self.rec.dictionary_presence[self.symbol]["x"])

    def test_transfer_is_not_a_read_and_consumers_are(self):
        core = self.run_as("build", lambda: self.settings.Child().core())
        self.assertNotIn("build", {c for group in self.rec.dictionary_reads[self.symbol].values() for c in group})
        self.assertIn("build", self.rec.dictionary_transfers[self.symbol])
        self.assertEqual(self.run_as("lookup", lambda: core.lookup("x")), 1)
        self.assertEqual(self.run_as("subscript", lambda: core.subscript("u")), 2)
        self.assertTrue(self.run_as("present", lambda: core.present("u")))
        self.assertEqual(self.run_as("size", core.size), 1)
        self.assertEqual(self.run_as("truthy", core.truthy), 1)
        self.run_as("leak", core.leak)
        reads = self.rec.dictionary_reads[self.symbol]
        self.assertEqual(reads["x"], {"lookup"})
        self.assertEqual(reads["u"], {"subscript"})
        self.assertEqual(reads[None], {"truthy", "leak"})
        self.assertIn("present", self.rec.dictionary_presence[self.symbol]["u"])
        self.assertEqual(self.rec.dictionary_reads[self.single][None], {"size"})

    def test_untracked_destinations_read_every_key(self):
        self.run_as("positional", lambda: self.settings.Settings().positional())
        self.run_as("foreign", lambda: self.settings.Settings().foreign())
        self.run_as("copied", lambda: copy.deepcopy(self.settings.Settings.ESCAPES))
        self.run_as("shallow", lambda: copy.copy(self.settings.Settings.ESCAPES))
        self.assertTrue({"positional", "foreign", "copied", "shallow"} <= self.rec.dictionary_reads[self.symbol][None])

    def test_setup_scopes(self):
        core = self.run_as("t::C::a", lambda: self.settings.Settings().core())
        def setUpClass():
            return core.lookup("x")
        def setUp():
            return core.lookup("u")
        def fixture():
            return core.subscript("x")
        self.run_as("t::C::a", setUpClass, phase="setup")
        self.run_as("t::C::b", setUp, phase="setup")
        self.run_as("t::C::b", fixture, phase="setup")
        reads = self.rec.dictionary_reads[self.symbol]
        self.assertEqual(reads["x"], {("class", "t::C"), None})
        self.assertEqual(reads["u"], {"t::C::b"})

    def test_serialized_entry(self):
        core = self.run_as("t::build", lambda: self.settings.Settings().core())
        self.run_as("t::read", lambda: core.lookup("u"))
        self.rec.tests = ["t::build", "t::read", "t::other"]
        self.rec.attrs[self.symbol] |= {"t::build", "t::other"}
        data = Mock()
        data.measured_files.return_value = []
        destination = self.root / "map.json"
        write_map(self.root, self.model, self.rec, data, destination)
        entry = json.loads(destination.read_text())["files"]["sqlglot/settings.py"]["dictionary_reads"]["Settings.ESCAPES"]
        everyone = format(7, "x")
        self.assertEqual(entry["keys"], {"u": "2"})
        self.assertEqual(entry["presence"]["x"], everyone)
        self.assertEqual(entry["transferred"], "3")
        # The import-time membership read already reaches every test.
        self.assertEqual(entry["unobserved"], "0")
        self.rec.dictionary_presence.clear()
        write_map(self.root, self.model, self.rec, data, destination)
        entry = json.loads(destination.read_text())["files"]["sqlglot/settings.py"]["dictionary_reads"]["Settings.ESCAPES"]
        # t::other used the symbol without any observed read or transfer.
        self.assertEqual(entry["unobserved"], "4")


if __name__ == "__main__":
    unittest.main()


REGISTRY = '''
class Registry:
    _classes = {}

    @classmethod
    def load(cls, key):
        if key not in cls._classes:
            cls._classes[key] = key.upper()
        return cls._classes[key]
'''


class RegistryWriterTest(unittest.TestCase):
    def test_writer_lines_reach_later_readers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sqlglot").mkdir()
            path = root / "sqlglot" / "registry.py"
            path.write_text(REGISTRY)
            model = SourceModel(root)
            rec = Recorder(root, model, None)
            observer = Instrumentation(root, rec)
            observer.install()
            try:
                spec = importlib.util.spec_from_file_location("sqlglot.registry", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for test in ("first", "later"):
                    rec.current, rec.phase = test, "call"
                    module.Registry.load("duck")
                rec.current, rec.phase = None, "collection"
            finally:
                observer.restore()
            symbol = ("sqlglot/registry.py", "Registry._classes")
            self.assertTrue(rec.dictionary_writers[symbol])
            rec.tests = ["first", "later", "other"]
            data = Mock()
            data.measured_files.return_value = [str(path)]
            # Only the first test executed the store line (8); the later one hit the cache.
            data.contexts_by_lineno.return_value = {7: ["first", "later"], 8: ["first"], 9: ["first", "later"]}
            destination = root / "map.json"
            write_map(root, model, rec, data, destination)
            lines = json.loads(destination.read_text())["files"]["sqlglot/registry.py"]["lines"]
            self.assertEqual(int(lines["8"], 16) & 0b11, 0b11)
            self.assertFalse(int(lines["8"], 16) & 0b100)

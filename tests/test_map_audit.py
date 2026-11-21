"""Small unchanged-baseline experiments for dependency attribution."""
import importlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from tools.build_map import Recorder, SourceModel
from tools.instrument import Instrumentation


class DependencyAudit(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        folder = self.root / "sqlglot"
        folder.mkdir()
        path = folder / "audit_example.py"
        path.write_text("""
from functools import lru_cache
class Config:
    FLAG = 17
def read():
    return Config.FLAG
@lru_cache(None)
def cached():
    return Config.FLAG
def __getattr__(key):
    if key == "deferred":
        return Config.FLAG
    raise AttributeError(key)
class Meta(type):
    def __new__(meta, name, bases, attrs):
        attrs["shared"] = Config.FLAG
        return type.__new__(meta, name, bases, attrs)
class Base:
    def __init_subclass__(cls):
        cls.copied = Config.FLAG
def make_class():
    class Child(Base, metaclass=Meta):
        pass
    return Child
""")
        self.rec = Recorder(self.root, SourceModel(self.root), None)
        inst = Instrumentation(self.root, self.rec)
        inst.install()
        self.addCleanup(inst.restore)
        spec = importlib.util.spec_from_file_location("sqlglot.audit_example", path)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.rec.current, self.rec.phase = "first", "call"

    @property
    def users(self):
        return self.rec.attrs["sqlglot/audit_example.py", "Config.FLAG"]

    def test_joined_thread_executes_in_active_test(self):
        thread = threading.Thread(target=self.mod.read)
        thread.start()
        thread.join()
        self.assertEqual(self.users, {"first"})

    def test_background_resource_reads_keep_all_tests(self):
        data = self.root / "data.txt"
        data.touch()
        worker = threading.Thread(target=self.rec.opened, args=(data,))
        worker.start()
        worker.join()
        self.assertEqual(self.rec.reads["data.txt"], {("all", "")})

    def test_cache_hit_skips_body_observation(self):
        self.mod.cached()
        self.rec.current = "second"
        self.mod.cached()
        self.assertEqual(self.mod.cached.cache_info().hits, 1)
        self.assertEqual(self.users, {"first"})

    def test_module_getattr_runs_in_reader(self):
        self.assertEqual(self.mod.deferred, 17)
        self.assertEqual(self.users, {"first"})

    def test_metaclass_and_init_subclass_each_observed_at_creation(self):
        self.mod.Meta("ViaMeta", (), {})
        self.rec.current = "second"
        type("ViaHook", (self.mod.Base,), {})
        self.assertEqual(self.users, {"first", "second"})

    def test_shared_class_state_loses_future_readers(self):
        child = self.mod.make_class()
        self.rec.current = "second"
        self.assertEqual((child.shared, child.copied), (17, 17))
        self.assertEqual(self.users, {"first"})

    def test_filesystem_resources_generate_read_audit_event(self):
        # Isolate the permanent audit hook from the parent test runner.
        import subprocess
        script = """
from pathlib import Path
from tools.build_map import Recorder, SourceModel
import sys, importlib.resources
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
rec = Recorder(root, SourceModel(root), None)
rec.current, rec.phase = "example", "call"
sys.addaudithook(rec.audit)
assert importlib.resources.files("resource_example").joinpath("sample.txt").read_text() == "abc"
assert ("test","example") in rec.reads["resource_example/sample.txt"]
"""
        package = self.root / "resource_example"
        package.mkdir()
        (package / "__init__.py").touch()
        (package / "sample.txt").write_text("abc")
        subprocess.run([sys.executable, "-c", script, str(self.root)],
                       cwd=Path(__file__).resolve().parents[1], check=True)

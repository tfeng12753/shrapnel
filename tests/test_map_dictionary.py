import importlib.util
import json
import threading
from unittest.mock import Mock
from pathlib import Path
import tempfile
import unittest

from tools.build_map import Recorder, SourceModel, write_map
from tools.instrument import Instrumentation


class DictionaryReadsTest(unittest.TestCase):
    def test_realistic_reads_preserve_dict_identity_and_order(self):
        source = """
TABLE = {"x": 1, "y": 2}
class Config:
    DATA = {"x": 1, "known": None}
class Other:
    DATA = {"x": 2}
def run(key, config):
    return TABLE[key()], config.DATA.get(key()), key() in TABLE
def get_default(key, default):
    return TABLE.get(key(), default())
def indirect(dict):
    return dict.get(TABLE, "x")
def membership(key, owner):
    return key() not in owner().DATA
def descriptors():
    return dict.__getitem__(TABLE, "x"), TABLE.__getitem__("y"), dict.get(TABLE, "y")
def broad():
    return len(TABLE), TABLE.copy(), list(TABLE.items()), list(TABLE), id(TABLE)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sqlglot").mkdir()
            (root / "sqlglot/sample.py").write_text(source)
            recorder = Recorder(root, SourceModel(root), None)
            observer = Instrumentation(root, recorder)
            observer.install()
            self.addCleanup(observer.restore)
            spec = importlib.util.spec_from_file_location("sqlglot.sample", root / "sqlglot/sample.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            namespace = vars(module)
            recorder.current, recorder.phase = "first", "call"
            keys = iter(["x", "known", "y"])
            self.assertEqual(namespace["run"](keys.__next__, namespace["Config"]), (1, None, True))
            self.assertEqual(recorder.dictionary_reads["sqlglot/sample.py", "TABLE"], {"x": {"first"}, "y": {"first"}})
            self.assertEqual(recorder.dictionary_reads["sqlglot/sample.py", "Config.DATA"]["known"], {"first"})
            self.assertEqual(namespace["descriptors"](), (1, 2, 2))
            self.assertNotIn(None, recorder.dictionary_reads["sqlglot/sample.py", "TABLE"])
            self.assertNotIn(("sqlglot/sample.py", "Other.DATA"), recorder.dictionary_reads)
            recorder.record_dictionary(namespace["TABLE"], "unbounded-input")
            self.assertIn("first", recorder.dictionary_reads["sqlglot/sample.py", "TABLE"][None])
            recorder.current = "second"
            self.assertEqual(namespace["broad"]()[-1], id(namespace["TABLE"]))
            self.assertEqual(recorder.dictionary_reads["sqlglot/sample.py", "TABLE"][None], {"first", "second"})
            calls = []
            def once(tag, value):
                def argument():
                    calls.append(tag)
                    return value
                return argument
            self.assertEqual(namespace["get_default"](once("key", "x"), once("default", 99)), 1)
            self.assertEqual(calls, ["key", "default"])
            calls.clear()
            self.assertTrue(namespace["membership"](once("key", "absent"), once("owner", namespace["Config"])))
            self.assertEqual(calls, ["key", "owner"])
            class Shadow:
                @staticmethod
                def get(mapping, key):
                    return len(mapping)
            recorder.current = "shadow"
            self.assertEqual(namespace["indirect"](Shadow), 2)
            self.assertIn("shadow", recorder.dictionary_reads["sqlglot/sample.py", "TABLE"][None])
            recorder.phase = "setup"
            recorder.record_dictionary(namespace["TABLE"], ("compound",))
            self.assertIn(None, recorder.dictionary_reads["sqlglot/sample.py", "TABLE"][None])


    def test_serialized_masks_include_unknown_users(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pkg = root / "sqlglot"
            pkg.mkdir()
            path = pkg / "constants.py"
            path.write_text("DATA = {'x': 'old'}")
            model = SourceModel(root)
            rec = Recorder(root, model, None)
            rec.tests = ["direct", "opaque", "other"]
            symbol = ("sqlglot/constants.py", "DATA")
            table = {"x": "old"}
            rec.bound_dictionaries[id(table)] = (table, (symbol,))
            rec.current, rec.phase = "direct", "call"
            rec.record_dictionary(table, "x")
            rec.current = "opaque"
            rec.record_dictionary(table, None)
            data = Mock()
            data.measured_files.return_value = [str(path)]
            data.contexts_by_lineno.return_value = {1: rec.tests}
            dest = root / "map.json"
            write_map(root, model, rec, data, dest)
            info = json.loads(dest.read_text())["files"][symbol[0]]["dictionary_reads"]["DATA"]
            self.assertEqual((info["keys"], info["whole"], info["unobserved"]), ({"x": "1"}, "2", "4"))
            thread = threading.Thread(target=rec.record_dictionary, args=(table, "x"))
            thread.start()
            thread.join()
            self.assertIn(None, rec.dictionary_reads[symbol]["x"])

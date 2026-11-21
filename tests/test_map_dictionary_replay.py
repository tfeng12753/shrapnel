import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.import_closure import REVISION
from tools.map_dictionary_index import build_index, scalar_sites
from tools.map_dictionary_replay import combined_replay, dictionary_update, mapped_literal_change
from tools.mutants import diff


class DictionaryReplayTests(unittest.TestCase):
    def test_only_one_native_scalar_value_in_a_known_dictionary(self):
        base = "class T:\n    opts = {'é': True, 'b': 1}\n"
        records = {(2, 2)}
        for after, known, expected in [
            (base.replace("True", "False"), records, True),
            (base.replace("1", "10"), records, True),
            (base.replace("True", "False"), {}, False),
            (base.replace("'é'", "'x'"), records, False),
            (base.replace("True", "1"), records, False),
            (base.replace("True", "choose()"), records, False),
            (base.replace("True", "False").replace("1", "10"), records, False),
            (base.replace("True", "False").replace("class T", "class Other"), records, False),
        ]:
            with self.subTest(after=after, known=known):
                self.assertEqual(mapped_literal_change(base, after, scalar_sites(base, known), 2), expected)

    def test_duplicate_and_dynamic_keys_do_not_qualify(self):
        records = {(1, 1)}
        for before in ("opts = {'a': 1, 'a': 2}", "opts = {key: 1}", "opts = {**existing, 'a': 1}"):
            self.assertFalse(mapped_literal_change(before, before.replace("1", "3"), scalar_sites(before, records), 1))

    def test_digest_and_revision_bind_the_index_to_the_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            name = "sqlglot/dictionary.py"
            path = root / name
            path.parent.mkdir()
            before = "DATA = {'a': True}\n"
            after = before.replace("True", "False")
            path.write_text(before)
            data = {"revision": REVISION, "version": 1, "complete": True, "files": {
                name: {"dictionary_reads": {"DATA": {"start": 1, "end": 1}}}
            }}
            map_path = root / "map.json"
            map_path.write_text(json.dumps(data))
            index = build_index(map_path, root)
            change = diff(name, before, after)
            self.assertEqual(dictionary_update(root, change, index["files"]), {name: after})
            path.write_text(before + "# newer checkout\n")
            self.assertEqual(dictionary_update(root, change, index["files"]), {})
            data["complete"] = False
            map_path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                build_index(map_path, root)

    def test_slow_verified_import_prevents_a_second_speculative_launch(self):
        args = SimpleNamespace(import_closure="closure.json", cpu_budget=2)
        result = {"verified_passes": ["example"], "removable_simulated_ms": 2368}
        with (patch("tools.map_dictionary_replay.replay_import", return_value=result),
              patch("tools.map_dictionary_replay.cpu_seconds", side_effect=[3.0, 5.1]),
              patch("tools.map_dictionary_replay.replay") as extra):
            observed = combined_replay(args)
        extra.assert_not_called()
        self.assertEqual(observed["imports"], result)
        self.assertEqual(observed["dictionaries"]["verified_passes"], [])

    def test_check_cost_prevents_a_partial_second_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "commits").mkdir()
            for name in ("cheap", "costly"):
                (root / "commits" / f"{name}.patch").write_text("synthetic")
            (root / "tests.json").write_text(json.dumps({"tests": [
                {"number": 0, "id": "tests/test_demo.py::test_a", "seconds": 0.01},
                {"number": 1, "id": "tests/test_demo.py::test_b", "seconds": 0.2},
            ]}))
            schedule = root / "schedule"
            schedule.write_text('{"commit":"costly","workers":[[1]]}\n{"commit":"cheap","workers":[[0]]}\n')
            index = root / "index.json"
            index.write_text(json.dumps({"revision": REVISION, "format": "dictionary-values-v1", "files": {}}))
            args = SimpleNamespace(data=root, source=root, schedule=schedule, index=index,
                                   import_closure=None, cpu_budget=2)
            clock = 0.0

            def check(source, updates, items, wall_seconds):
                nonlocal clock
                clock += 1.25
                return True

            with (patch("tools.map_dictionary_replay.cpu_seconds", side_effect=lambda: clock),
                  patch("tools.map_dictionary_replay.time.monotonic", side_effect=lambda: clock),
                  patch("tools.map_dictionary_replay.dictionary_update", return_value={"config.py": "x=0"}),
                  patch("tools.map_dictionary_replay.check_items", side_effect=check)):
                self.assertEqual(combined_replay(args), {"attempted": ["cheap"], "verified_passes": ["cheap"],
                                                        "removable_simulated_ms": 2010})

import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from dict_keys import catalog_digest
from selector import REVISION, Selector
from tools.mutants import diff
from tools.pack_key_reads import pack


class KeyReadSelectionTests(TestCase):
    def test_expression_argument_schema_change_keeps_class_users(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            name = "sqlglot/expressions/example.py"
            source = root / name
            source.parent.mkdir(parents=True)
            before = 'class Node:\n    arg_types = {"condition": False}\n'
            source.write_text(before)
            catalog = [{"id": letter, "number": i, "seconds": .1} for i, letter in enumerate("abc")]
            base = root / "base.json"
            base.write_text(json.dumps({"revision": REVISION, "version": 1, "complete": True,
                                        "tests": ["a", "b", "c"], "files": {
                                            name: {"lines": {"2": "1"}, "regions": [[1, 2, "class", "3"]]}}}))
            changed = diff(name, before, before.replace("False", "True"))
            self.assertEqual(Selector(catalog, root, base).select(changed), 1)
            self.assertEqual(Selector(catalog, root, base, dict_key_refinement=True).select(changed), 1)
            self.assertEqual(Selector(catalog, root, base, expression_schema_coverage=True).select(changed), 3)

    def test_packed_record_retains_users_unexplained_in_either_seed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            name = "library.py"
            (root / name).write_text('KEYS = {"a": 1, "b": 2}\n')
            common = {"revision": REVISION, "version": 1, "complete": True, "tests": ["a", "b", "c"]}
            sources = [root / (name + ".gz") for name in ("base", "first", "second")]
            base = {"regions": [[1, 1, "value", "7"]]}
            defaults = {"start": 1, "end": 1, "whole": "0", "unobserved": "0", "transferred": "0",
                        "presence": {}, "lines": {}, "shared": []}
            first = {"dictionary_reads": {"KEYS": {**defaults, "keys": {"a": "1", "b": "2"}}}}
            second = {"dictionary_reads": {"KEYS": {**defaults, "keys": {"a": "1", "b": "4"}}}}
            for path, entry in zip(sources, (base, first, second)):
                path.write_bytes(gzip.compress(json.dumps({**common, "files": {name: entry}}).encode()))
            record = pack(root, *sources)["files"][name]["records"]["KEYS"]
            self.assertEqual(record["keys"], {"a": "1", "b": "6"})
            self.assertEqual(record["unobserved"], "6")

    def test_real_selector_uses_only_matching_observations_and_keeps_other_reasons(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            name = "library.py"
            before = 'KEYS = {"one": 1, "two": 2}\n'
            (root / name).write_text(before)
            (root / "other.txt").write_text("old\n")
            catalog = [{"id": letter, "number": i, "seconds": .1} for i, letter in enumerate("abcd")]
            names = [t["id"] for t in catalog]
            base = root / "base.json"
            base.write_text(json.dumps({"revision": REVISION, "version": 1, "complete": True, "tests": names,
                "external": "8", "files": {name: {"lines": {"1": "7"}, "readers": "4"},
                                            "other.txt": {"readers": "2"}}}))
            path = root / "reads.json.gz"
            file = {"sha256": hashlib.sha256(before.encode()).hexdigest(),
                    "records": {"KEYS": {"start": 1, "end": 1, "shared": [], "unobserved": "0",
                                         "whole": "0", "keys": {"one": "1", "two": "2"},
                                         "lines": {}, "transferred": "0", "presence": {}}}}
            index = {"revision": REVISION, "version": 1, "catalog": catalog_digest(names), "files": {name: file},
                     "map_sha256": hashlib.sha256(base.read_bytes()).hexdigest()}
            patch = diff(name, before, before.replace('"one": 1', '"one": 10'))

            def selected(other=""):
                selector = Selector(catalog, root, base, dict_key_refinement=True, key_reads_path=path)
                return selector.select(patch + other)

            path.write_bytes(gzip.compress(json.dumps(index).encode()))
            self.assertEqual(selected(), 13)
            self.assertEqual(selected(other=diff("other.txt", "old\n", "new\n")), 15)
            for record in ({**file, "sha256": "stale"}, {**file, "records": {"bad": {}}}):
                index["files"][name] = record
                path.write_bytes(gzip.compress(json.dumps(index).encode()))
                self.assertEqual(selected(), 15)
            path.write_bytes(b"invalid gzip")
            self.assertEqual(selected(), 15)

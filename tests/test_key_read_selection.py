import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from dict_keys import catalog_digest
from selector import REVISION, Selector
from tools.mutants import diff


class KeyReadSelectionTests(TestCase):
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

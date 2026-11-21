import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from tools.map_equality_replay import checked_child_work, digest, index_identity


class ChildWorkIndexTests(TestCase):
    def test_index_requires_matching_map_catalog_source_and_inventory(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            module = root / "m.py"
            catalog = root / "tests.json"
            mapping = root / "map"
            index = root / "index"
            module.write_text("x = 1")
            catalog.write_text("catalog")
            mapping.write_text("map")
            sel = Mock(source=root, files={"m.py": {}}, all=7)
            sel.child_work_mask.return_value = 5
            data = {"identity": index_identity(root, mapping), "mask": "2", "sources": {"m.py": digest(module)}}
            index.write_text(json.dumps(data))
            self.assertEqual(checked_child_work(index, sel, root, mapping), 2)
            sel.child_work_mask.assert_not_called()
            for path in (module, catalog, mapping):
                old = path.read_bytes()
                path.write_bytes(old + b"!")
                self.assertEqual(checked_child_work(index, sel, root, mapping), 5)
                path.write_bytes(old)
            for broken in (dict(data, sources={}), dict(data, mask="-1"), dict(data, sources=None)):
                index.write_text(json.dumps(broken))
                self.assertEqual(checked_child_work(index, sel, root, mapping), 5)

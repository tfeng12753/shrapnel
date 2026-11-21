"""Configuration round-trip and cached-map checks (standard library only)."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import map_fingerprint  # noqa: E402
from tools.competitive import BASE, VARIANTS, selector_options  # noqa: E402


def selector_config_keys():
    """Every key selector.py reads from selector-config.json."""
    return set(re.findall(r'config\.get\("(\w+)"', (ROOT / "selector.py").read_text()))


class ConfigRoundTripTests(unittest.TestCase):
    def test_benchmark_variants_cover_every_selector_setting(self):
        self.assertEqual(set(BASE), selector_config_keys())
        for name, config in VARIANTS.items():
            with self.subTest(name):
                self.assertEqual(set(config), set(BASE))

    def test_shipped_config_is_complete(self):
        shipped = json.loads((ROOT / "selector-config.json").read_text())
        self.assertEqual(set(shipped), selector_config_keys())

    def test_flags_pin_every_setting_regardless_of_shipped_file(self):
        import selector
        for name, config in VARIANTS.items():
            options = selector_options(config)
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                # A shipped file with every setting flipped must not leak into a flagged run.
                flipped = {"conservative_python": not config["conservative_python"], "child_work": not config["child_work"],
                           "relations": not config["relations"], "probe_budget": 99.0, "import_check_budget": 99.0}
                path = Path(tmp) / "config.json"
                path.write_text(json.dumps(flipped))
                resolved = selector.resolve_settings(["--config", str(path), *options])
                self.assertEqual(resolved, config)

    def test_incomplete_variant_is_rejected(self):
        with self.assertRaises(ValueError):
            selector_options({"relations": True})


class CachedMapTests(unittest.TestCase):
    def test_committed_map_matches_current_builder(self):
        problems = map_fingerprint.check(ROOT / "maps/dependencies.meta.json", ROOT / "maps/dependencies.json.gz", ROOT)
        self.assertEqual(problems, [], "rebuild the map with `make map` after changing the builder")

    def test_builder_change_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in map_fingerprint.BUILDER_FILES:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes((ROOT / name).read_bytes())
            self.assertEqual(map_fingerprint.builder_digest(root), map_fingerprint.builder_digest(ROOT))
            (root / "tools/instrument.py").write_text("# changed\n")
            problems = map_fingerprint.check(ROOT / "maps/dependencies.meta.json", ROOT / "maps/dependencies.json.gz", root)
            self.assertTrue(any("builder" in p for p in problems))

    def test_line_endings_do_not_change_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in map_fingerprint.BUILDER_FILES:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes((ROOT / name).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            self.assertEqual(map_fingerprint.builder_digest(root), map_fingerprint.builder_digest(ROOT))


if __name__ == "__main__":
    unittest.main()

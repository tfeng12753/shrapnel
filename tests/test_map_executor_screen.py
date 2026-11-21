import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import map_executor_fixture as fixture
from tools.map_executor_replay import check
from tools.map_executor_screen import screen_matches
from tools.mutants import diff


class QueryScreenTests(unittest.TestCase):
    def test_queries_that_cover_the_line_run_in_order_until_mismatch(self):
        screen = {"lines": {"optimizer.py": {"5": [1, 0]}},
                  "queries": [["second", "b"], ["first", "a"]]}
        seen = []
        def optimize(sql):
            seen.append(sql)
            return "changed"
        self.assertFalse(screen_matches(screen, "optimizer.py", 5, optimize))
        self.assertEqual(seen, ["first"])
        self.assertTrue(screen_matches(screen, "optimizer.py", 5, {"first": "a", "second": "b"}.__getitem__))

    def test_unobserved_line_goes_on_to_full_comparison(self):
        screen = {"lines": {}, "queries": []}
        self.assertTrue(screen_matches(screen, "unknown.py", 10, self.fail))


class FixtureInputsTests(unittest.TestCase):
    def test_only_omit_the_consumer_of_matching_ordered_inputs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            helpers = root / "tests/helpers.py"
            fixture_dir = helpers.parent / "fixtures"
            fixture_dir.mkdir(parents=True)
            helpers.write_text("""
from pathlib import Path
FIXTURES_DIR = Path(__file__).parent / 'fixtures'
def load_sql_fixture_pairs(path):
    for line in Path(path).read_text().splitlines():
        flag, sql, expected = line.split('|')
        yield {'execute': flag}, sql, expected
""")
            suite = root / "tests/test_executor.py"
            suite.write_text("""
class TestExecutor:
    def setUpClass(cls):
        cls.tpcds_sqls = list(load_sql_fixture_pairs("one.sql"))
        cls.tpch_sqls = list(load_sql_fixture_pairs("two.sql"))
""")
            name = "tests/fixtures/one.sql"
            before = "true|select 1|A\nfalse|select 2|B\n"
            (root / name).write_text(before)
            def decision(after, target="tpcds"):
                return check(root, diff(name, before, after), None, target, target.upper() + "_SCHEMA",
                             checks="fixture")
            with (patch.object(fixture, "SUITE_HASH", hashlib.sha256(suite.read_bytes()).hexdigest()),
                  patch.object(fixture, "LOADER_HASH", hashlib.sha256(helpers.read_bytes()).hexdigest())):
                self.assertTrue(decision(before.replace("|A", "|X")).startswith("candidate:"))
                self.assertIn("different executor fixture", decision(before.replace("|A", "|X"), "tpch"))
                for after in (before.replace("select 2", "select 3"), before.replace("false", "true"),
                              "".join(reversed(before.splitlines(keepends=True))), before.splitlines(True)[0],
                              before.replace("|A", "|unexpected|extra")):
                    with self.subTest(after=after):
                        self.assertTrue(decision(after).startswith("retain:"))
                helpers.write_text(helpers.read_text() + "\n# new loader\n")
                self.assertIn("unaudited fixture consumer", decision(before.replace("|A", "|X")))
            self.assertIn("unaudited fixture consumer", decision(before.replace("|A", "|X")))

    def test_metadata_order_is_preserved(self):
        a = fixture.executor_inputs([({"x": "1", "y": "2"}, "query", "expected")])
        b = fixture.executor_inputs([({"y": "2", "x": "1"}, "query", "expected")])
        self.assertNotEqual(a, b)


class CompleteAuditTests(unittest.TestCase):
    def test_an_early_match_cannot_authorize_omission(self):
        import json
        from types import SimpleNamespace
        from tools.map_executor_plan import checkout_digest, digest

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            name = "sqlglot/optimizer/example.py"
            old = "def f(x):\n    return x < 1\n"
            (source / name).parent.mkdir(parents=True)
            (source / name).write_text(old)
            reference = {"queries": [], "context": []}
            evidence = {"checkout": checkout_digest(source), "snapshot": digest(reference),
                        "modules": ["sqlglot/executor/python.py"]}
            for seed in (1, 2):
                (root / f"plan-base-{seed}.json").write_text(json.dumps(reference))
                (root / f"plan-deps-{seed}.json").write_text(json.dumps(evidence))
            (root / "plan-screen.json").write_text("{}")
            mutation = diff(name, old, old.replace("<", "<="))
            with patch("tools.map_executor_replay.subprocess.run",
                       side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)]) as run:
                self.assertIn("retain: optimizer", check(source, mutation, root, "tpcds", "TPCDS_SCHEMA"))
                self.assertEqual(run.call_count, 2)
            with patch("tools.map_executor_replay.subprocess.run", return_value=SimpleNamespace(returncode=1)) as run:
                self.assertIn("retain: query screen", check(source, mutation, root, "tpcds", "TPCDS_SCHEMA"))
                self.assertEqual(run.call_count, 1)

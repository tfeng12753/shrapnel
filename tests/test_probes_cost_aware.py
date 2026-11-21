import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from native_probes import verify
from selector import Selector, resolve_settings
from test_advanced import diff


class CostAwareProbeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        name = "sqlglot/subject.py"
        (self.root / name).parent.mkdir()
        (self.root / name).write_text("VALUE = 1\n")
        self.diff = diff(name, "VALUE = 1\n", "VALUE = 2\n")
        self.tests = [
            {"id": "tests/test_subject.py::Case::test_slow", "number": 0, "seconds": .395},
            {"id": "tests/test_subject.py::Case::test_fast", "number": 1, "seconds": .025},
        ]
        self.selector = Selector(self.tests, self.root, self.root / "missing.json")
        self.selector.native = {test["id"] for test in self.tests}

    def test_full_pass_can_remove_selection_with_slower_test(self):
        with patch("native_probes.verify", return_value=(list(self.selector.native), .1)) as run:
            self.assertEqual(self.selector.probe(self.diff, 3, 2), (3, 0))
            run.assert_not_called()
            mask, charge = self.selector.probe(self.diff, 3, 2, cost_aware=True)
            self.assertEqual(mask, 0)
            self.assertAlmostEqual(charge, 1.94)
            self.assertAlmostEqual(run.call_args.kwargs["cpu_seconds"], charge)
            self.assertGreater(run.call_args.kwargs["wall_seconds"], charge)
            self.assertTrue(run.call_args.kwargs["strict_cpu"])

    def test_partial_pass_or_timeout_keeps_whole_selection_and_charges_reservation(self):
        for passed, cpu in [([], 1.0), ([self.tests[0]["id"]], .2)]:
            with self.subTest(passed=passed), patch("native_probes.verify", return_value=(passed, cpu)):
                mask, charge = self.selector.probe(self.diff, 3, 2, cost_aware=True)
                self.assertEqual(mask, 3)
            self.assertAlmostEqual(charge, 1.94)

    def test_admission_requires_budget_and_sufficient_savings(self):
        with patch("native_probes.verify") as run:
            self.assertEqual(self.selector.probe(self.diff, 3, 1.3, cost_aware=True), (3, 0))
            # Each test is cheap, but the entire worker must pass to save time.
            self.tests[:] = [{"id": str(n), "number": n, "seconds": .005} for n in range(6)]
            self.selector.native = {t["id"] for t in self.tests}
            self.assertEqual(self.selector.probe(self.diff, 63, 2, cost_aware=True), (63, 0))
            run.assert_not_called()

    def test_unknown_or_unverifiable_selection_is_retained(self):
        with patch("native_probes.verify") as run:
            self.assertEqual(self.selector.probe(self.diff * 2, 3, 2, cost_aware=True), (3, 0))
            self.selector.external = 1
            self.assertEqual(self.selector.probe(self.diff, 3, 2, cost_aware=True), (3, 0))
            self.selector.external = 0
            self.selector.files["sqlglot/subject.py"] = {"readers": 1}
            self.assertEqual(self.selector.probe(self.diff, 3, 2, cost_aware=True), (3, 0))
            self.selector.files.clear()
            self.selector.native.remove(self.tests[0]["id"])
            self.assertEqual(self.selector.probe(self.diff, 3, 2, cost_aware=True), (3, 0))
            run.assert_not_called()

    def test_optional_nested_configuration_and_cli_override(self):
        config = self.root / "config.json"
        budget = {"seconds": 8, "cost_aware": True}
        config.write_text(json.dumps({"probe_budget": budget}))
        args = ["--config", str(config)]
        self.assertEqual(resolve_settings(args)["probe_budget"], budget)
        self.assertEqual(resolve_settings([*args, "--probe-budget", "4"])["probe_budget"], 4)
        self.assertEqual(resolve_settings([*args, "--no-cost-aware-probes"])["probe_budget"], 8)
        self.assertEqual(
            resolve_settings([*args, "--probe-budget", "2", "--cost-aware-probes"])["probe_budget"],
            {"seconds": 2, "cost_aware": True},
        )

    def run_native(self, source, tests=None, **limits):
        (self.root / "tests").mkdir()
        (self.root / "tests/__init__.py").touch()
        (self.root / "tests/test_subject.py").write_text(source)
        return verify(self.root, {}, self.tests[:1] if tests is None else tests, **limits)

    def test_skipped_test_cannot_confirm_a_pass(self):
        passed, _ = self.run_native(
            "import unittest\n"
            "class Case(unittest.TestCase):\n"
            "    @unittest.skip('requires container environment')\n"
            "    def test_slow(self): pass\n"
        )
        self.assertEqual(passed, [])

    def test_cpu_deadline_ignores_blocked_time(self):
        passed, _ = self.run_native(
            "import time, unittest\n"
            "class Case(unittest.TestCase):\n"
            "    def test_slow(self): time.sleep(.25)\n",
            strict_cpu=True, wall_seconds=2.0, cpu_seconds=.15,
        )
        self.assertEqual(passed, [self.tests[0]["id"]])

    def test_cpu_deadline_keeps_busy_test(self):
        passed, cpu = self.run_native(
            "import unittest\n"
            "class Case(unittest.TestCase):\n"
            "    def test_slow(self):\n"
            "        while True: pass\n",
            strict_cpu=True, wall_seconds=2.0, cpu_seconds=.15,
        )
        self.assertEqual(passed, [])
        self.assertLess(cpu, .3)

    def test_attempted_subprocess_invalidates_a_successful_batch(self):
        passed, _ = self.run_native(
            "import subprocess, unittest\n"
            "class Case(unittest.TestCase):\n"
            "    def test_slow(self):\n"
            "        try: subprocess.run(['/bin/true'], check=True)\n"
            "        except RuntimeError: pass\n",
            strict_cpu=True,
        )
        self.assertEqual(passed, [])

    def test_whole_batch_runs_in_worker_order_and_stops_on_failure(self):
        passed, _ = self.run_native(
            "import pathlib, unittest\n"
            "class Case(unittest.TestCase):\n"
            "    def test_slow(self): self.fail('stop the batch')\n"
            "    def test_fast(self): pathlib.Path('continued').touch()\n",
            tests=self.tests, strict_cpu=True,
        )
        self.assertEqual(passed, [])
        self.assertFalse((self.root / "continued").exists())

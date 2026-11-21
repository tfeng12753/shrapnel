import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import map_executor_trial as trial
from tools.mutants import diff


class ExecutorTrialTests(unittest.TestCase):
    def test_failed_validation_retains_item_and_reports_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "commits").mkdir()
            (root / "tests.json").write_text(json.dumps({"tests": [
                {"number": 0, "id": trial.ITEM, "seconds": 21.454}]}))
            (root / "m.py").write_text("x = 1\n")
            (root / "commits/a.patch").write_text(diff("m.py", "x = 1\n", "x = 2\n"))
            args = SimpleNamespace(source=root, data=root, cache=root, map=root / "map", verify=True)
            original = {"commit": "a", "workers": [[0]]}

            def selector(*unused, stdout, **kwargs):
                stdout.write(json.dumps(original) + "\n")

            with (patch.object(trial, "prepare", return_value=root),
                  patch.object(trial.subprocess, "run", side_effect=selector),
                  patch.object(trial, "check", return_value="candidate: both snapshots matched"),
                  patch.object(trial, "check_items", return_value=False)):
                self.assertTrue(trial.run(args, root))
            self.assertEqual(json.loads((root / "trial.jsonl").read_text()), original)
            report = json.loads((root / "report.json").read_text())
            self.assertFalse(report["decisions"][0]["verified"])
            self.assertEqual(report["baseline_schedule_seconds"], report["trial_schedule_seconds"])

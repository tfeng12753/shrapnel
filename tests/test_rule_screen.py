import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class RuleScreenTests(unittest.TestCase):
    @unittest.skipUnless(Path("/opt/sqlglot/sqlglot").is_dir(), "requires the pinned checkout")
    def test_complete_capture_and_inconclusive_records(self):
        script = Path(__file__).resolve().parents[1] / "tools/map_rule_replay.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            snapshot = base / "snapshot.json"
            snapshot.write_text(json.dumps({"queries": [[{}, "SELECT 1", ["boundary"], None],
                                                         [{}, "SELECT 2", ["boundary"], None]]}))
            folder = base / "rules"
            cmd = [sys.executable, str(script), "--source", "/opt/sqlglot", "--schema", "TPCDS_SCHEMA",
                   "--snapshot", str(snapshot), "--folder", str(folder)]
            env = dict(os.environ, PYTHONHASHSEED="1")
            subprocess.run(cmd, env=env, check=True)
            replay = cmd + ["--module", "sqlglot/optimizer/pushdown_projections.py"]
            self.assertEqual(subprocess.run(replay, env=env).returncode, 0)
            self.assertNotEqual(subprocess.run(replay, env=dict(env, PYTHONHASHSEED="2")).returncode, 0)
            path = folder / "manifest.json"
            original = json.loads(path.read_text())
            for replacement in ({"queries": original["queries"][:1]}, {"tables": [["missing"], []]},
                                {"snapshot": "stale"}):
                path.write_text(json.dumps({**original, **replacement}))
                self.assertNotEqual(subprocess.run(replay, env=env).returncode, 0)

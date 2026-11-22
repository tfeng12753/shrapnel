"""Time complete baseline items through the selector's isolated pytest runner."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isolated_pytest import cpu_seconds, passed
from selector import REVISION


def measure(source, catalog):
    name = "sqlglot/__init__.py"
    unchanged = (source / name).read_text()
    timings = {}
    for item in json.loads(catalog.read_text())["tests"]:
        if item["seconds"] < 5:
            continue
        samples = []
        for _ in range(3):
            wall, cpu = time.monotonic(), cpu_seconds()
            if not passed(source, name, unchanged, item["id"], 100):
                break
            samples.append((time.monotonic() - wall, cpu_seconds() - cpu))
        if len(samples) == 3:
            timings[item["id"]] = {key: round(statistics.median(values), 3)
                                   for key, values in zip(("wall", "cpu"), zip(*samples))}
        print(item["id"], timings.get(item["id"], "inconclusive"), file=sys.stderr, flush=True)
    return {"revision": REVISION, "items": timings}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(measure(args.source, args.catalog), indent=2) + "\n")

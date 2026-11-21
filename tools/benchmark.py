#!/usr/bin/env python3
"""Local, CPU-accounted scoring; sample answers stay outside the selector."""
import argparse
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from score import evaluate_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "sample")
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--map", type=Path, default=ROOT / "dependencies.json.gz")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "output.jsonl")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    tests = json.loads((args.data / "tests.json").read_text())["tests"]
    answers = json.loads((args.data / "answers.json").read_text())["commits"]
    runs = []
    for i in range(args.runs):
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "selector.py"), "--data", str(args.data), "--source", str(args.source), "--map", str(args.map)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
        if result.returncode:
            sys.stderr.buffer.write(result.stderr)
            raise SystemExit(f"selector exited {result.returncode}")
        if len(result.stdout) > 2_000_000:
            raise SystemExit("stdout exceeds 2 MB")
        evaluation = evaluate_run(result.stdout, tests, answers)
        if not evaluation["ok"]:
            raise SystemExit(str(evaluation))
        record = {"run": i + 1, "simulated_seconds": evaluation["simulated_milliseconds"] / 1000,
                  "cpu_seconds": cpu, "wall_seconds": time.monotonic() - started,
                  "misses": len(evaluation["missed_commits"]), "stdout_bytes": len(result.stdout)}
        record["total_seconds"] = record["simulated_seconds"] + cpu
        runs.append(record)
        print(json.dumps(record))
        args.output.write_bytes(result.stdout)
    total = statistics.median(run["total_seconds"] for run in runs)
    report = {"dependency_map_present": args.map.is_file(), "runs": runs, "median_total_seconds": total,
              "rounded_median_seconds": int(total * 10 + .5) / 10,
              "caveat": "Local Python/CPU measurement; Docker/cgroup scorer limits are not reproduced."}
    print(f"Median total: {report['rounded_median_seconds']:.1f} s")
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build once, compare refinements, and export baseline evidence for profiling."""
import argparse
import gzip
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from score import evaluate_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="blast-radius-entry")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--no-export", action="store_true")
    args = parser.parse_args()
    if not args.skip_build:
        started = time.monotonic()
        subprocess.run(["docker", "build", "--platform", "linux/amd64", "-t", args.image, str(ROOT)], check=True, timeout=600)
        print(f"Build completed in {time.monotonic() - started:.1f} seconds (600-second limit).", flush=True)
    data = ROOT / "sample"
    tests = json.loads((data / "tests.json").read_text())["tests"]
    answers = json.loads((data / "answers.json").read_text())["commits"]
    variants = {"coverage": ["--no-relations", "--probe-budget", "0"],
                "relations": ["--relations", "--probe-budget", "0"], "relations_and_probes": ["--relations", "--probe-budget", "8"]}
    report, outputs = {}, {}
    for variant, options in variants.items():
        runs = []
        for i in range(args.runs):
            name = "blast-radius-bench-" + uuid.uuid4().hex[:12]
            command = ["docker", "run", "--rm", "--name", name, "--platform", "linux/amd64", "--network", "none",
                "--cpus", "2", "--memory", "4096m", "--memory-swap", "4096m", "--pids-limit", "256", "--read-only",
                "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "65534:65534", "--env", "HOME=/tmp", "--volume", str(data / "commits") + ":/data/commits:ro",
                "--volume", str(data / "tests.json") + ":/data/tests.json:ro", args.image,
                "python", "/app/tools/timed_run.py", *options]
            started = time.monotonic()
            try:
                process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=190)
            finally:
                subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if process.returncode:
                sys.stderr.buffer.write(process.stderr)
                raise SystemExit(f"{variant}: container failed ({process.returncode})")
            if len(process.stdout) > 2_000_000:
                raise SystemExit("stdout exceeds 2 MB")
            evaluation = evaluate_run(process.stdout, tests, answers)
            if not evaluation["ok"]:
                raise SystemExit(str(evaluation))
            timings = [json.loads(line.removeprefix("BR_TIMING ")) for line in process.stderr.decode().splitlines() if line.startswith("BR_TIMING ")]
            if not timings:
                raise SystemExit("missing container CPU measurement")
            cpu = timings[-1]["cpu_seconds"]
            run = {"simulated_seconds": evaluation["simulated_milliseconds"] / 1000, "cpu_seconds": cpu,
                "misses": len(evaluation["missed_commits"]), "missed_commits": evaluation["missed_commits"],
                "wall_seconds": time.monotonic() - started}
            run["total_seconds"] = run["simulated_seconds"] + cpu
            runs.append(run)
            outputs[f"{variant}-{i+1}.jsonl"] = process.stdout
            print(f"{variant} run {i+1}: total={run['total_seconds']:.3f}s simulated={run['simulated_seconds']:.3f}s CPU={cpu:.3f}s misses={run['misses']}", flush=True)
        report[variant] = {"runs": runs, "median_total_seconds": statistics.median(run["total_seconds"] for run in runs)}
    # A missed commit is never hidden by a favorable average over other commits.
    valid = {key: value for key, value in report.items() if all(run["misses"] == 0 for run in value["runs"])}
    report["best_zero_miss_variant"] = min(valid, key=lambda key: valid[key]["median_total_seconds"]) if valid else None
    report["measurement_note"] = "CPU includes a small Python benchmark wrapper; official cgroup accounting may differ."
    payload = json.dumps(report, indent=2).encode()
    (ROOT / "competitive-results.json").write_bytes(payload + b"\n")
    print("Best zero-miss variant:", report["best_zero_miss_variant"])
    best = report["best_zero_miss_variant"]
    if best is not None:
        config = {"relations": best != "coverage", "probe_budget": 8.0 if best == "relations_and_probes" else 0.0}
        (ROOT / "selector-config.json").write_text(json.dumps(config, separators=(",", ":")) + "\n")
        print("Updated global selector configuration; rebuild before submission.")
    else:
        print("No variant had zero misses. The exported evidence is needed to debug dependencies.")
    if args.no_export:
        return
    container = subprocess.check_output(["docker", "create", args.image], text=True).strip()
    try:
        with tempfile.TemporaryDirectory() as directory:
            map_path = Path(directory) / "dependencies.json.gz"
            subprocess.run(["docker", "cp", container + ":/app/dependencies.json.gz", str(map_path)], check=True)
            raw_map = gzip.decompress(map_path.read_bytes())
            if len(raw_map) <= 18_000_000:
                (ROOT / "maps/dependencies.json").write_bytes(raw_map)
                print("Cached plain baseline dependency map for subsequent builds.")
            source_tar = subprocess.check_output(["docker", "cp", container + ":/opt/sqlglot/.", "-"])
            destination = ROOT / "research-bundle.zip"
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("competitive-results.json", payload)
                archive.write(map_path, "dependencies.json.gz")
                archive.writestr("pinned-sqlglot.tar.gz", gzip.compress(source_tar, compresslevel=6))
                for name, output in outputs.items():
                    archive.writestr("outputs/" + name, output)
            print("Research bundle:", destination)
    finally:
        subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()

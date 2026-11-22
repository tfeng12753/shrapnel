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
from tools.profile_schedule import schedule_profile

# Every variant is a complete selector configuration. The winner is written to
# selector-config.json verbatim, so the shipped image runs exactly what was measured.
BASE = {"conservative_python": False, "child_work": False, "relations": False,
        "executor_fixtures": False, "executor_plan_budget": 0.0, "lazy_test_budget": 0.0,
        "probe_budget": 0.0, "import_check_budget": 6.0, "setup_check_budget": 8.0,
        "downstream_import_budget": 6.0,
        "typeddict_state_check": False,
        "schedule_admission": False,
        "exact_comparisons": False,
        "fixture_replay_budget": 0.0,
        "lambda_bodies": False,
        "docstring_edits": False,
        "entry_point_edits": False}
VARIANTS = {
    "conservative_python": {**BASE, "conservative_python": True, "import_check_budget": 0.0, "setup_check_budget": 0.0},
    "coverage": BASE,
    "child_work": {**BASE, "child_work": True},
    "relations": {**BASE, "relations": True},
    "relations_and_fixtures": {**BASE, "relations": True, "executor_fixtures": True},
    "relations_fixtures_and_lazy_test": {**BASE, "relations": True, "executor_fixtures": True, "lazy_test_budget": 3.0},
    "relations_and_probes": {**BASE, "relations": True, "probe_budget": 8.0},
    "relations_fixtures_lazy_and_fixture_replay": {**BASE, "relations": True, "executor_fixtures": True,
                                                   "lazy_test_budget": 3.0, "fixture_replay_budget": 8.0},
    "previous_shipped": {**BASE, "relations": True, "executor_fixtures": True, "lazy_test_budget": 3.0,
                         "typeddict_state_check": True},
    "quiet_edits": {**BASE, "relations": True, "executor_fixtures": True, "lazy_test_budget": 3.0,
                    "typeddict_state_check": True, "lambda_bodies": True, "docstring_edits": True,
                    "entry_point_edits": True},
}


def selector_options(config):
    """Command-line flags that reproduce a complete configuration, independent of the shipped file."""
    if set(config) != set(BASE):
        raise ValueError(f"incomplete or unknown selector configuration: {sorted(config)}")
    if config["conservative_python"] and config["child_work"]:
        raise ValueError("conservative_python and child_work are exclusive")
    mode = ("--conservative-python" if config["conservative_python"]
            else "--child-work" if config["child_work"] else "--no-conservative-python")
    return [mode, "--relations" if config["relations"] else "--no-relations",
            "--executor-fixtures" if config["executor_fixtures"] else "--no-executor-fixtures",
            "--lazy-test-budget", repr(float(config["lazy_test_budget"])),
            "--executor-plan-budget", repr(float(config["executor_plan_budget"])),
            "--probe-budget", repr(float(config["probe_budget"])),
            "--import-check-budget", repr(float(config["import_check_budget"])),
            "--setup-check-budget", repr(float(config["setup_check_budget"])),
            "--downstream-import-budget", repr(float(config["downstream_import_budget"])),
            "--typeddict-state-check" if config["typeddict_state_check"] else "--no-typeddict-state-check",
            "--schedule-admission" if config["schedule_admission"] else "--no-schedule-admission",
            "--exact-comparisons" if config["exact_comparisons"] else "--no-exact-comparisons",
            "--fixture-replay-budget", repr(float(config["fixture_replay_budget"])),
            "--lambda-bodies" if config["lambda_bodies"] else "--no-lambda-bodies",
            "--docstring-edits" if config["docstring_edits"] else "--no-docstring-edits",
            "--entry-point-edits" if config["entry_point_edits"] else "--no-entry-point-edits"]


def run_container(image, data, options, tests, answers, *, trace_pytest=False):
    name = "shrapnel-bench-" + uuid.uuid4().hex[:12]
    command = ["docker", "run", "--rm", "--name", name, "--platform", "linux/amd64", "--network", "none",
        "--cpus", "2", "--memory", "4096m", "--memory-swap", "4096m", "--pids-limit", "256", "--read-only",
        "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "65534:65534", "--env", "HOME=/tmp", "--volume", str(data / "commits") + ":/data/commits:ro",
        "--volume", str(data / "tests.json") + ":/data/tests.json:ro", image,
        "python", "/app/tools/timed_run.py", *options]
    if trace_pytest:
        command[2:2] = ["--env", "SHRAPNEL_TRACE_PYTEST=1"]
    started = time.monotonic()
    try:
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=190)
    finally:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if trace_pytest or process.returncode:
        sys.stderr.buffer.write(process.stderr)
    if process.returncode:
        raise SystemExit(f"container failed ({process.returncode})")
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
    return run, process.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="shrapnel")
    parser.add_argument("--executor-ab", action="store_true",
                        help="Compare the main, fixture and 12-CPU-second plan configurations without changing the config")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--no-export", action="store_true")
    parser.add_argument("--no-validate", action="store_true", help="skip rebuilding and re-running the saved winner")
    args = parser.parse_args()

    def build():
        started = time.monotonic()
        extra = ["--build-arg", "BUILD_EXECUTOR_PLANS=1"] if args.executor_ab else []
        subprocess.run(["docker", "build", "--platform", "linux/amd64", *extra, "-t", args.image, str(ROOT)],
                       check=True, timeout=600)
        print(f"Build completed in {time.monotonic() - started:.1f} seconds (600-second limit).", flush=True)

    if not args.skip_build:
        build()
    data = ROOT / "sample"
    tests = json.loads((data / "tests.json").read_text())["tests"]
    answers = json.loads((data / "answers.json").read_text())["commits"]
    report, outputs = {}, {}
    variants = VARIANTS
    if args.executor_ab:
        fixture_config = VARIANTS["relations_and_fixtures"]
        shipping = VARIANTS["relations_fixtures_and_lazy_test"]
        variants = {"main_settings": VARIANTS["relations"], "fixtures": fixture_config,
                    "shared_default": shipping, "shared_with_plans": {**shipping, "executor_plan_budget": 12.0}}
    for variant, config in variants.items():
        options = selector_options(config)
        runs = []
        for i in range(args.runs):
            run, stdout = run_container(args.image, data, options, tests, answers, trace_pytest=args.executor_ab)
            runs.append(run)
            outputs[f"{variant}-{i+1}.jsonl"] = stdout
            print(f"{variant} run {i+1}: total={run['total_seconds']:.3f}s simulated={run['simulated_seconds']:.3f}s CPU={run['cpu_seconds']:.3f}s misses={run['misses']}", flush=True)
        median = statistics.median(run["total_seconds"] for run in runs)
        index = min(range(len(runs)), key=lambda j: abs(runs[j]["total_seconds"] - median))
        output_name = f"{variant}-{index + 1}.jsonl"
        profile = schedule_profile(outputs[output_name], tests, answers)
        report[variant] = {"runs": runs, "median_total_seconds": median, "config": config, "selector_options": options,
                           "profile_output": output_name, "schedule_profile": profile}
        for patch in profile["patches"][:3]:
            print(f"  {patch['commit']}: {patch['excess_schedule_ms'] / 1000:+.3f}s vs failing-only schedule"
                  f" ({patch['extra_count']} extra tests, {len(patch['missing'])} missing)")
    # A missed commit is never hidden by a favorable average over other commits.
    valid = {key: value for key, value in report.items() if all(run["misses"] == 0 for run in value["runs"])}
    report["best_zero_miss_variant"] = min(valid, key=lambda key: valid[key]["median_total_seconds"]) if valid else None
    report["measurement_note"] = "CPU includes a small Python benchmark wrapper; cgroup accounting elsewhere may differ."
    payload = json.dumps(report, indent=2).encode()
    (ROOT / "variant-results.json").write_bytes(payload + b"\n")
    print("Best zero-miss variant:", report["best_zero_miss_variant"])
    if args.executor_ab:
        if len(valid) != len(variants):
            raise SystemExit("An executor configuration missed a public failure")
        return
    best = report["best_zero_miss_variant"]
    if best is not None:
        config = VARIANTS[best]
        # One key per line, so each agent's settings stay on their own lines.
        (ROOT / "selector-config.json").write_text(json.dumps(config, indent=2) + "\n")
        print("Wrote the measured configuration to selector-config.json:", config)
        if not args.no_validate:
            # The shipped default (no flags) must reproduce the measured selections.
            build()
            run, _ = run_container(args.image, data, [], tests, answers)
            measured = {r["simulated_seconds"] for r in report[best]["runs"]}
            if run["misses"] or run["simulated_seconds"] not in measured:
                raise SystemExit(f"rebuilt default does not reproduce {best}: {run} vs {sorted(measured)}")
            report["validated_default"] = run
            (ROOT / "variant-results.json").write_text(json.dumps(report, indent=2) + "\n")
            print(f"Validated rebuilt default: simulated={run['simulated_seconds']:.3f}s misses=0")
    else:
        print("No variant had zero misses. The exported evidence is needed to debug dependencies.")
    if args.no_export:
        return
    container = subprocess.check_output(["docker", "create", args.image], text=True).strip()
    try:
        with tempfile.TemporaryDirectory() as directory:
            map_path = Path(directory) / "dependencies.json.gz"
            subprocess.run(["docker", "cp", container + ":/app/dependencies.json.gz", str(map_path)], check=True)
            source_tar = subprocess.check_output(["docker", "cp", container + ":/opt/sqlglot/.", "-"])
            destination = ROOT / "research-bundle.zip"
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("variant-results.json", payload)
                archive.write(map_path, "dependencies.json.gz")
                archive.writestr("pinned-sqlglot.tar.gz", gzip.compress(source_tar, compresslevel=6))
                for name, output in outputs.items():
                    archive.writestr("outputs/" + name, output)
            print("Research bundle:", destination)
    finally:
        subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()

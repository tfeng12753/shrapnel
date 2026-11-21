"""Run a local selector comparison and optionally verify each proposed omission."""
import argparse
import errno
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from score import WORKER_COST_MILLISECONDS, _duration_milliseconds, evaluate_run
from selector import apply_change, parse_patch, split
from tools.map_executor_plan import checkout_digest, digest
from tools.map_executor_replay import check
from tools.map_import_replay import check_items

ROOT = Path(__file__).resolve().parents[1]
ITEM = "tests/test_executor.py::TestExecutor::test_execute_tpcds"
EXECUTORS = {
    ITEM: ("tpcds", "TPCDS_SCHEMA"),
    "tests/test_executor.py::TestExecutor::test_execute_tpch": ("tpch", "TPCH_SCHEMA"),
}


def cpu():
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return children.ru_utime + children.ru_stime + time.process_time()


def prepare(source, cache):
    versions = sorted((d.metadata["Name"], d.version) for d in distributions() if d.metadata["Name"])
    scripts = [Path(__file__).with_name(name).read_text() for name in
               ("map_executor_plan.py", "map_executor_screen.py", "import_closure.py")]
    key = digest([checkout_digest(source), sys.version, sys.platform, versions, scripts])
    dest = cache / key
    if dest.is_dir():
        return dest
    cache.mkdir(parents=True, exist_ok=True)
    print("Preparing executor baselines for both hash seeds.", file=sys.stderr)
    with tempfile.TemporaryDirectory(dir=cache) as temp:
        batch = Path(temp) / "prepared"
        batch.mkdir()
        for seed in (1, 2):
            subprocess.run([sys.executable, "-B", "-m", "tools.map_executor_plan", "--source", str(source),
                            "--fixture", "tpcds", "--schema", "TPCDS_SCHEMA", "--output",
                            str(batch / f"plan-base-{seed}.json"), "--observe-downstream",
                            str(batch / f"plan-deps-{seed}.json")], cwd=ROOT, check=True,
                           env=dict(os.environ, PYTHONHASHSEED=str(seed), SKIP_INTEGRATION="0",
                                    OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1"))
        subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("map_executor_screen.py").resolve()),
                        "--source", str(source), "--schema", "TPCDS_SCHEMA", "--snapshot",
                        str(batch / "plan-base-1.json"), "--output", str(batch / "plan-screen.json")],
                       cwd=ROOT, check=True, env=dict(os.environ, PYTHONHASHSEED="1"))
        subprocess.run([sys.executable, "-B", "-m", "tools.import_closure", "--source", str(source),
                        "--output", str(batch / "closure.json")], cwd=ROOT, check=True)
        try:
            batch.rename(dest)
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY) or not dest.is_dir():
                raise
    return dest


def cost(workers, tests):
    loads = [sum(_duration_milliseconds(tests[n]["seconds"]) for n in group) for group in workers if group]
    return max(loads, default=0) + WORKER_COST_MILLISECONDS * len(loads)


def write_baseline(args, catalog, source, data, baseline, prepared):
    if args.schedule:
        raw = args.schedule.read_bytes()
        commits = {p.stem: {"failing": []} for p in (data / "commits").glob("*.patch")}
        result = evaluate_run(raw, list(catalog.values()), commits)
        if not result["ok"]:
            raise ValueError(f"invalid baseline schedule: {result}")
        baseline.write_bytes(raw)
        return
    with tempfile.TemporaryDirectory() as directory:
        if prepared is None:
            closure = Path(directory) / "closure.json"
            subprocess.run([sys.executable, "-B", "-m", "tools.import_closure", "--source", str(source),
                            "--output", str(closure)], cwd=ROOT, check=True)
        else:
            closure = prepared / "closure.json"
        with baseline.open("w") as stream:
            subprocess.run([sys.executable, "-B", str(ROOT / "selector.py"), "--source", str(source),
                            "--data", str(data), "--map", str(args.map.resolve()), "--import-closure",
                            str(closure)], cwd=ROOT, stdout=stream, check=True)


def run(args, folder):
    source, data = args.source.resolve(), args.data.resolve()
    catalog = {test["number"]: test for test in json.loads((data / "tests.json").read_text())["tests"]}
    targets = {n: test["id"] for n, test in catalog.items() if test["id"] in EXECUTORS}
    prepared = prepare(source, args.cache.resolve()) if args.checks != "fixture" else None
    baseline = folder / "baseline.jsonl"
    write_baseline(args, catalog, source, data, baseline, prepared)
    rows = [json.loads(line) for line in baseline.read_text().splitlines()]
    decisions = []
    baseline_ms = trial_ms = 0
    verification_cpu = 0.
    failed = False
    start = cpu()
    with (folder / "trial.jsonl").open("w") as stream:
        for row in rows:
            workers = row["workers"]
            baseline_ms += cost(workers, catalog)
            selected = [n for group in workers for n in group]
            for target, item in targets.items():
                fixture, schema = EXECUTORS[item]
                if target not in selected or (fixture == "tpch" and args.checks == "optimizer"):
                    continue
                cid = row["commit"]
                patch = (data / "commits" / f"{cid}.patch").read_text()
                without_target = split([catalog[n] for n in selected if n != target])
                if cost(without_target, catalog) < cost(row["workers"], catalog):
                    decision = check(source, patch, prepared, fixture, schema, checks=args.checks)
                else:
                    decision = "retain: no marginal schedule savings"
                result = {"commit": cid, "item": item, "decision": decision}
                matched = decision.startswith("candidate:")
                if matched and args.verify:
                    t = cpu()
                    change, = parse_patch(patch)
                    name = change["old"]
                    after, _, _ = apply_change((source / name).read_text(), change)
                    try:
                        matched = check_items(source, {name: after}, [item], wall_seconds=240, item_timeout=180)
                    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as exc:
                        result["verification_error"] = type(exc).__name__
                        matched = False
                    result["verified"] = matched
                    verification_cpu += cpu() - t
                    failed |= not matched
                if matched:
                    row["workers"] = without_target
                    selected.remove(target)
                decisions.append(result)
            trial_ms += cost(row["workers"], catalog)
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    report = {"patches": len(rows), "baseline_schedule_seconds": baseline_ms / 1000,
              "trial_schedule_seconds": trial_ms / 1000, "audit_cpu_seconds": round(cpu() - start - verification_cpu, 3),
              "verification_cpu_seconds": round(verification_cpu, 3), "verification_requested": args.verify,
              "checks": args.checks, "decisions": decisions}
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--data", type=Path, default=ROOT / "sample")
    parser.add_argument("--map", type=Path, default=ROOT / "maps/dependencies.json.gz")
    parser.add_argument("--output", required=True, type=Path, help="New directory for both schedules and report")
    parser.add_argument("--cache", type=Path, help="Reusable baseline directory (default: beside output)")
    parser.add_argument("--schedule", type=Path, help="Use a saved schedule from the selector being compared")
    parser.add_argument("--checks", choices=("all", "optimizer", "fixture"), default="all")
    parser.add_argument("--verify", action="store_true", help="Run original pytest item on every candidate")
    args = parser.parse_args()
    output = args.output.resolve()
    args.cache = args.cache or output.parent / ".executor-baselines"
    if output.exists():
        parser.error("choose a new output directory")
    if (not (args.source / "tests/test_executor.py").is_file() or (not args.schedule and not args.map.is_file())
            or not (args.data / "tests.json").is_file() or not (args.data / "commits").is_dir()):
        parser.error("supply the pinned --source checkout, dependency --map and patch --data directory")
    if args.cache.resolve().is_relative_to(output):
        parser.error("keep the reusable cache outside the new output directory")
    if output.is_relative_to(args.source.resolve()) or args.cache.resolve().is_relative_to(args.source.resolve()):
        parser.error("keep results and cache outside the pinned source checkout")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        folder = Path(temp) / "results"
        folder.mkdir()
        failed = run(args, folder)
        folder.rename(output)
    print(output / "report.json")
    sys.exit(1 if failed else 0)

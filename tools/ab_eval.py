#!/usr/bin/env python3
"""Budget-faithful A/B comparison of two selector revisions on a mutant set.

    python3 tools/ab_eval.py main . --mutants ~/work/mutants --split holdout \
        --python ~/venv/bin/python --source ~/work/pinned --import-closure ~/work/import-closure.json \
        --work ab-work --out ab-eval.json

A revision is a git ref of this repository (extracted with `git archive`) or a directory holding
selector.py (e.g. `.` for the working tree). Mutants are split into fixed batches of --batch-size
patches (sorted ids, optional --seed shuffle), identical for both sides. Each batch is a fresh data
dir holding only the patches and the catalog, and is run by one selector process, as in a real run,
so per-run budgets and CPU are spent the same way. CPU includes child processes.

Scoring uses score.evaluate_run per batch. The oracle per commit is the cost floor of scheduling
exactly its failing tests: min over k=1..8 of max(longest, ceil(total/k)) + worker cost * k.
Per-batch raw outputs are stored under --work, so a rerun skips finished batches.

Exit status: 0 ok, 1 if B misses a commit that A does not, 2 if a batch failed or was rejected.
"""
import argparse
from collections import defaultdict
import hashlib
import io
import json
import os
from pathlib import Path
import random
import resource
import shlex
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from score import (evaluate_run, _duration_milliseconds, MAXIMUM_WORKERS,  # noqa: E402
                   MISS_PENALTY_MILLISECONDS, WORKER_COST_MILLISECONDS)
from tools.mutant_eval import load_ground_truth, schedule_milliseconds  # noqa: E402

BATCH_SIZE = 30
SIDES = ("a", "b")
# Container limits from the Makefile's RUN_FLAGS (volumes are added per batch).
RUN_FLAGS = ["--rm", "--platform", "linux/amd64", "--network", "none", "--cpus", "2",
             "--memory", "4096m", "--memory-swap", "4096m", "--pids-limit", "256", "--read-only",
             "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL",
             "--security-opt", "no-new-privileges", "--user", "65534:65534", "--env", "HOME=/tmp"]
# Same interpreter settings as the image.
LOCAL_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "1",
             "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
TIMING_PREFIX = "BR_TIMING "


# ---------------------------------------------------------------- scoring helpers

def oracle_milliseconds(failing, duration):
    """Cost floor of running exactly `failing`: min_k max(longest, ceil(total/k)) + worker cost * k."""
    loads = [duration[n] for n in failing if n in duration]
    if not loads:
        return 0
    longest, total = max(loads), sum(loads)
    return min(max(longest, -(-total // k)) + WORKER_COST_MILLISECONDS * k
               for k in range(1, MAXIMUM_WORKERS + 1))


def make_batches(commits, size=BATCH_SIZE, seed=None):
    """Deterministic chunks of sorted ids; a seed shuffles the order reproducibly first."""
    ordered = sorted(commits)
    if seed is not None:
        random.Random(seed).shuffle(ordered)
    return [ordered[i:i + size] for i in range(0, len(ordered), size)]


def load_mutants(mutants, split_name, include_suspicious, tests):
    """Ground truth via mutant_eval, with failing test ids (strings) mapped to catalog numbers."""
    answers, patches, skipped, baseline = load_ground_truth(mutants, split_name, include_suspicious)
    manifest = json.loads((mutants / "manifest.json").read_text())
    number_by_id = {t["id"]: t["number"] for t in tests}
    baseline_numbers = {number_by_id.get(x, x) for x in (baseline or {}).get("failing", [])}
    for commit in list(answers):
        mapped, unmapped = set(), []
        for item in answers[commit]["failing"]:
            if isinstance(item, str):
                if item in number_by_id:
                    mapped.add(number_by_id[item])
                else:
                    unmapped.append(item)
            else:
                mapped.add(item)
        if unmapped:
            skipped["suspicious"][commit] = [f"failing ids not in catalog: {unmapped[:5]}"]
            if not include_suspicious:
                del answers[commit], patches[commit]
                continue
        meta = manifest.get(commit, {})
        answers[commit] = {"failing": sorted(mapped - baseline_numbers),
                           "kind": answers[commit]["kind"], "family": meta.get("family") or "unknown"}
    return answers, patches, skipped, baseline


# ---------------------------------------------------------------- revisions

def materialize(spec, destination):
    """Return (directory, label) for a revision: an existing directory or a git ref of this repo."""
    path = Path(spec).expanduser()
    if path.is_dir() and (path / "selector.py").is_file():
        return path.resolve(), str(path.resolve())
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", f"{spec}^{{commit}}"],
                         capture_output=True, text=True, check=True).stdout.strip()
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", sha],
                             capture_output=True, check=True).stdout
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter="data")
    return destination, f"{spec}@{sha[:12]}"


def fingerprint(directory):
    """Hash of the top-level selector code and config that a run depends on."""
    digest = hashlib.sha256()
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix in (".py", ".json"):
            digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def file_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return f"missing:{path}"


def image_id(image):
    try:
        out = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or image
    except (OSError, subprocess.SubprocessError):
        return image


# ---------------------------------------------------------------- commands

def local_command(python, revision, data, source, map_path, closure, extra):
    return [str(python), str(Path(revision) / "selector.py"), "--data", str(data), "--source", str(source),
            "--map", str(map_path), "--import-closure", str(closure), *extra]


def docker_command(image, data, extra, name):
    """docker run under the Makefile limits; the in-image wrapper reports container CPU on stderr."""
    data = Path(data)
    return ["docker", "run", "--name", name, *RUN_FLAGS,
            "--volume", f"{data / 'commits'}:/data/commits:ro",
            "--volume", f"{data / 'tests.json'}:/data/tests.json:ro",
            image, "python", "/app/tools/timed_run.py", *extra]


def run_measured(command, timeout, cwd=None, env=None, cleanup=None):
    """Run once; CPU is the RUSAGE_CHILDREN delta (user + system of the process and its waited children)."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        returncode = "timeout"
    finally:
        if cleanup:
            cleanup()
    wall = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    return {"returncode": returncode, "stdout": stdout.decode("utf-8", errors="surrogateescape"),
            "stderr_tail": stderr.decode(errors="replace")[-4000:], "cpu_seconds": round(cpu, 6),
            "wall_seconds": round(wall, 3)}


def container_cpu(stderr_tail):
    timings = [line[len(TIMING_PREFIX):] for line in stderr_tail.splitlines() if line.startswith(TIMING_PREFIX)]
    return json.loads(timings[-1])["cpu_seconds"] if timings else None


# ---------------------------------------------------------------- running

def build_batch_dir(directory, commits, patches, catalog):
    """Only patches and the catalog go into a batch: mutant outcomes never reach the selector."""
    (directory / "commits").mkdir(parents=True)
    shutil.copyfile(catalog, directory / "tests.json")
    for commit in commits:
        shutil.copyfile(patches[commit], directory / "commits" / f"{commit}.patch")


class Side:
    def __init__(self, name, spec, args, tmp):
        self.name, self.spec = name, spec
        self.extra = shlex.split(getattr(args, f"{name}_args") or "")
        self.image = getattr(args, f"{name}_image") or args.image
        if self.image:
            self.revision, self.label = None, f"image:{self.image}"
            self.identity = {"image": image_id(self.image)}
        else:
            self.revision, self.label = materialize(spec, tmp / f"rev-{name}")
            map_path = args.map or (self.revision / "maps" / "dependencies.json.gz")
            if not Path(map_path).exists():
                map_path = ROOT / "maps" / "dependencies.json.gz"
            self.map = Path(map_path)
            self.identity = {"code": fingerprint(self.revision), "python": str(args.python),
                             "source": str(args.source), "map": file_sha256(self.map),
                             "import_closure": str(args.import_closure)}

    def command(self, data, args, name):
        if self.image:
            return docker_command(self.image, data, self.extra, name)
        return local_command(args.python, self.revision, data, args.source, self.map,
                             args.import_closure, self.extra)

    def run_once(self, data, args):
        if self.image:
            name = "shrapnel-ab-" + uuid.uuid4().hex[:12]

            def cleanup():
                subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run = run_measured(self.command(data, args, name), args.timeout, cleanup=cleanup)
            run["client_cpu_seconds"] = run["cpu_seconds"]
            run["cpu_seconds"] = container_cpu(run["stderr_tail"])
            if run["cpu_seconds"] is None and run["returncode"] == 0:
                run["returncode"] = "no container CPU measurement"
            return run
        env = {**os.environ, **LOCAL_ENV}
        return run_measured(self.command(data, args, None), args.timeout, cwd=self.revision, env=env)

    def batch_key(self, commits, catalog_sha, args):
        payload = {"identity": self.identity, "extra": self.extra, "commits": commits,
                   "catalog": catalog_sha, "repeats": args.repeats}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def run_side(side, batches, patches, args, work, tmp, catalog_sha):
    """Run (or reuse) every batch for one side; returns the stored batch records."""
    records = []
    for index, commits in enumerate(batches):
        path = work / side.name / f"batch-{index:04d}.json"
        key = side.batch_key(commits, catalog_sha, args)
        if path.exists():
            try:
                stored = json.loads(path.read_text())
            except ValueError:
                stored = None
            if stored and stored.get("key") == key and all(r["returncode"] == 0 for r in stored["runs"]):
                print(f"[{side.name}] batch {index + 1}/{len(batches)}: reused", file=sys.stderr)
                records.append(stored)
                continue
        data = tmp / f"batch-{side.name}-{index:04d}"
        if data.exists():
            shutil.rmtree(data)
        build_batch_dir(data, commits, patches, args.catalog)
        runs = []
        for repeat in range(args.repeats):
            run = side.run_once(data, args)
            runs.append(run)
            print(f"[{side.name}] batch {index + 1}/{len(batches)} run {repeat + 1}: exit={run['returncode']}"
                  f" cpu={run['cpu_seconds']}s wall={run['wall_seconds']}s", file=sys.stderr, flush=True)
            if run["returncode"] != 0:
                break
        shutil.rmtree(data, ignore_errors=True)
        record = {"key": key, "side": side.name, "label": side.label, "index": index, "commits": commits,
                  "runs": runs}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record) + "\n")
        temporary.replace(path)
        records.append(record)
    return records


# ---------------------------------------------------------------- scoring

def score_output(stdout, commits, answers, tests, duration):
    """evaluate_run on one batch plus per-commit costs that must add up to its simulated total."""
    batch_answers = {c: {"failing": answers[c]["failing"]} for c in commits}
    evaluation = evaluate_run(stdout.encode("utf-8", errors="surrogateescape"), tests, batch_answers)
    if not evaluation["ok"]:
        return evaluation, {}
    per_commit = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        commit, workers = row["commit"], row["workers"]
        selected = {n for w in workers for n in w}
        missed = sorted(set(answers[commit]["failing"]) - selected)
        schedule = schedule_milliseconds(workers, duration)
        per_commit[commit] = {"selected": len(selected), "workers": sum(1 for w in workers if w),
                              "missed_tests": missed, "schedule_ms": schedule,
                              "simulated_ms": schedule + (MISS_PENALTY_MILLISECONDS if missed else 0)}
    total = sum(c["simulated_ms"] for c in per_commit.values())
    if total != evaluation["simulated_milliseconds"]:
        raise SystemExit(f"per-commit costs {total} != evaluate_run {evaluation['simulated_milliseconds']}")
    return evaluation, per_commit


def pick_median(scored_runs):
    """Index of the run whose total (simulated + CPU) is the median, like a 3-run summary."""
    totals = [s["simulated_milliseconds"] / 1000 + r["cpu_seconds"] for r, s in scored_runs]
    median = statistics.median(totals)
    return min(range(len(totals)), key=lambda i: (abs(totals[i] - median), i))


def empty_bucket():
    return {"commits": 0, "misses": 0, "simulated_ms": 0, "oracle_ms": 0}


def finish_bucket(bucket):
    return {"commits": bucket["commits"], "misses": bucket["misses"],
            "simulated_seconds": bucket["simulated_ms"] / 1000, "oracle_seconds": bucket["oracle_ms"] / 1000,
            "excess_seconds": (bucket["simulated_ms"] - bucket["oracle_ms"]) / 1000}


def summarize_side(records, answers, tests, duration, batch_size):
    per_commit, errors, batches = {}, [], []
    cpu = wall = 0.0
    by_family, by_kind = defaultdict(empty_bucket), defaultdict(empty_bucket)
    simulated_ms = 0
    for record in records:
        commits = record["commits"]
        failed = [r for r in record["runs"] if r["returncode"] != 0]
        if failed or len(record["runs"]) == 0:
            errors.append({"batch": record["index"], "error": f"selector exit {failed[0]['returncode']}"
                           if failed else "no runs", "stderr_tail": failed[0]["stderr_tail"] if failed else ""})
            continue
        scored = [(run, *score_output(run["stdout"], commits, answers, tests, duration)) for run in record["runs"]]
        rejected = [s for _, s, _ in scored if not s["ok"]]
        if rejected:
            errors.append({"batch": record["index"], "error": f"output rejected: {rejected[0]}"})
            continue
        chosen = pick_median([(r, s) for r, s, _ in scored])
        run, evaluation, commit_rows = scored[chosen]
        simulated_ms += evaluation["simulated_milliseconds"]
        cpu += run["cpu_seconds"]
        wall += run["wall_seconds"]
        batches.append({"batch": record["index"], "commits": len(commits), "chosen_run": chosen,
                        "simulated_seconds": evaluation["simulated_milliseconds"] / 1000,
                        "cpu_seconds": run["cpu_seconds"], "wall_seconds": run["wall_seconds"],
                        "cpu_seconds_all_runs": [r["cpu_seconds"] for r, _, _ in scored],
                        "simulated_seconds_all_runs": [s["simulated_milliseconds"] / 1000 for _, s, _ in scored]})
        for commit, row in commit_rows.items():
            answer = answers[commit]
            row["oracle_ms"] = oracle_milliseconds(answer["failing"], duration)
            row["kind"], row["family"], row["failing"] = answer["kind"], answer["family"], len(answer["failing"])
            per_commit[commit] = row
            for bucket in (by_family[answer["family"]], by_kind[answer["kind"]]):
                bucket["commits"] += 1
                bucket["misses"] += bool(row["missed_tests"])
                bucket["simulated_ms"] += row["simulated_ms"]
                bucket["oracle_ms"] += row["oracle_ms"]
    oracle_ms = sum(r["oracle_ms"] for r in per_commit.values())
    missed = {c: r["missed_tests"] for c, r in sorted(per_commit.items()) if r["missed_tests"]}
    label = records[0]["label"] if records else None
    return {"label": label, "commits": len(per_commit), "misses": len(missed), "missed": missed,
            "simulated_seconds": simulated_ms / 1000, "oracle_seconds": oracle_ms / 1000,
            "excess_seconds": (simulated_ms - oracle_ms) / 1000, "cpu_seconds": round(cpu, 3),
            "cpu_seconds_per_batch": round(cpu / len(per_commit) * batch_size, 3) if per_commit else None,
            "total_seconds": round(simulated_ms / 1000 + cpu, 3), "wall_seconds": round(wall, 2),
            "by_family": {k: finish_bucket(v) for k, v in sorted(by_family.items())},
            "by_kind": {k: finish_bucket(v) for k, v in sorted(by_kind.items())},
            "batches": batches, "errors": errors, "per_commit": per_commit}


def compare(a, b):
    """Per-commit differences on commits both sides scored."""
    cheaper, worse, new_misses, fixed = [], [], {}, {}
    for commit in sorted(set(a["per_commit"]) & set(b["per_commit"])):
        ra, rb = a["per_commit"][commit], b["per_commit"][commit]
        delta = rb["simulated_ms"] - ra["simulated_ms"]
        row = {"commit": commit, "kind": ra["kind"], "family": ra["family"], "a_seconds": ra["simulated_ms"] / 1000,
               "b_seconds": rb["simulated_ms"] / 1000, "delta_seconds": delta / 1000,
               "a_selected": ra["selected"], "b_selected": rb["selected"]}
        if delta < 0:
            cheaper.append(row)
        elif delta > 0:
            worse.append(row)
        if rb["missed_tests"] and not ra["missed_tests"]:
            new_misses[commit] = rb["missed_tests"]
        if ra["missed_tests"] and not rb["missed_tests"]:
            fixed[commit] = ra["missed_tests"]
    cheaper.sort(key=lambda r: (r["delta_seconds"], r["commit"]))
    worse.sort(key=lambda r: (-r["delta_seconds"], r["commit"]))
    return {"b_cheaper": cheaper, "b_worse": worse, "new_misses_in_b": new_misses, "misses_fixed_in_b": fixed,
            "simulated_delta_seconds": round(b["simulated_seconds"] - a["simulated_seconds"], 3),
            "cpu_delta_seconds": round(b["cpu_seconds"] - a["cpu_seconds"], 3)}


# ---------------------------------------------------------------- report

def print_report(report, out=None):
    out = out or sys.stdout
    sides = report["sides"]
    header = (f"{'side':<4} {'commits':>7} {'misses':>6} {'simulated':>10} {'oracle':>9} {'excess':>9}"
              f" {'cpu':>8} {'cpu/' + str(report['batch_size']):>7} {'wall':>8}  revision")
    print(header, file=out)
    print("-" * len(header), file=out)
    for name in SIDES:
        s = sides[name]
        per = "-" if s["cpu_seconds_per_batch"] is None else f"{s['cpu_seconds_per_batch']:.2f}"
        print(f"{name:<4} {s['commits']:>7} {s['misses']:>6} {s['simulated_seconds']:>10.3f}"
              f" {s['oracle_seconds']:>9.3f} {s['excess_seconds']:>9.3f} {s['cpu_seconds']:>8.2f} {per:>7}"
              f" {s['wall_seconds']:>8.1f}  {s['label']}", file=out)
        for error in s["errors"]:
            print(f"     ERROR batch {error['batch']}: {error['error']}", file=out)
        for commit, tests in s["missed"].items():
            more = " ..." if len(tests) > 12 else ""
            print(f"     miss {commit} ({s['per_commit'][commit]['kind']}): {tests[:12]}{more}", file=out)
    for group in ("by_kind", "by_family"):
        keys = sorted(set(sides["a"][group]) | set(sides["b"][group]))
        if not keys:
            continue
        print(f"\n{group.replace('_', ' '):<14} {'commits':>7} {'A miss':>6} {'B miss':>6} {'A sim':>9} {'B sim':>9}"
              f" {'oracle':>9} {'A exc':>9} {'B exc':>9}", file=out)
        for key in keys:
            ba = sides["a"][group].get(key, finish_bucket(empty_bucket()))
            bb = sides["b"][group].get(key, finish_bucket(empty_bucket()))
            print(f"  {key[:12]:<12} {max(ba['commits'], bb['commits']):>7}"
                  f" {ba['misses']:>6} {bb['misses']:>6} {ba['simulated_seconds']:>9.3f} {bb['simulated_seconds']:>9.3f}"
                  f" {max(ba['oracle_seconds'], bb['oracle_seconds']):>9.3f} {ba['excess_seconds']:>9.3f}"
                  f" {bb['excess_seconds']:>9.3f}", file=out)
    diff = report["diff"]
    print(f"\nB - A: simulated {diff['simulated_delta_seconds']:+.3f} s, CPU {diff['cpu_delta_seconds']:+.3f} s;"
          f" B cheaper on {len(diff['b_cheaper'])}, worse on {len(diff['b_worse'])} commits", file=out)
    for title, rows in (("cheaper", diff["b_cheaper"]), ("worse", diff["b_worse"])):
        for row in rows[:10]:
            print(f"  B {title:<7} {row['commit']} ({row['kind']}/{row['family']}): {row['a_seconds']:.3f} ->"
                  f" {row['b_seconds']:.3f} ({row['delta_seconds']:+.3f})", file=out)
        if len(rows) > 10:
            print(f"  ... {len(rows) - 10} more {title}", file=out)
    for commit, tests in diff["new_misses_in_b"].items():
        print(f"  NEW MISS in B: {commit}: {tests[:12]}", file=out)
    for commit, tests in diff["misses_fixed_in_b"].items():
        print(f"  miss fixed in B: {commit}: {tests[:12]}", file=out)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    home = Path.home()
    parser.add_argument("a", help="baseline revision: git ref or directory with selector.py")
    parser.add_argument("b", help="candidate revision: git ref or directory with selector.py")
    parser.add_argument("--mutants", type=Path, default=home / "work" / "mutants")
    parser.add_argument("--split", choices=("all", "holdout", "tune"), default="all",
                        help="holdout = odd-numbered commits, tune = even-numbered")
    parser.add_argument("--include-suspicious", action="store_true")
    parser.add_argument("--catalog", type=Path, default=ROOT / "sample" / "tests.json")
    parser.add_argument("--python", default=sys.executable, help="interpreter for local runs")
    parser.add_argument("--source", type=Path, default=home / "work" / "pinned")
    parser.add_argument("--map", type=Path, help="default: each revision's maps/dependencies.json.gz")
    parser.add_argument("--import-closure", type=Path, default=home / "work" / "import-closure.json")
    parser.add_argument("--a-args", default="", help="extra selector flags for A, e.g. --a-args='--no-relations'")
    parser.add_argument("--b-args", default="", help="extra selector flags for B")
    parser.add_argument("--image", help="run both sides with docker under the container limits")
    parser.add_argument("--a-image", help="image for A (overrides --image)")
    parser.add_argument("--b-image", help="image for B (overrides --image)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--seed", type=int, help="shuffle ids before chunking (same batches for both sides)")
    parser.add_argument("--repeats", type=int, default=1, help="runs per batch; the median-total run is scored")
    parser.add_argument("--timeout", type=float, default=900, help="seconds per selector run")
    parser.add_argument("--work", type=Path, help="per-batch results; a rerun skips finished batches")
    parser.add_argument("--out", type=Path, help="JSON report")
    args = parser.parse_args(argv)
    if args.batch_size < 1 or args.repeats < 1:
        parser.error("--batch-size and --repeats must be positive")
    for name in ("mutants", "catalog", "source", "map", "import_closure", "work", "out"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.expanduser())
    return args


def main(argv=None):
    args = parse_args(argv)
    tests = json.loads(args.catalog.read_text())["tests"]
    duration = {t["number"]: _duration_milliseconds(t["seconds"]) for t in tests}
    catalog_sha = hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    answers, patches, skipped, baseline = load_mutants(args.mutants, args.split, args.include_suspicious, tests)
    for label in ("missing", "errored"):
        if skipped[label]:
            print(f"Skipped {len(skipped[label])} {label}: {' '.join(skipped[label][:20])}", file=sys.stderr)
    for commit, reasons in skipped["suspicious"].items():
        verb = "counted" if commit in answers else "excluded"
        print(f"SUSPICIOUS ({verb}) {commit}: {'; '.join(reasons)}", file=sys.stderr)
    if not answers:
        print("No mutants with results in this split; nothing to evaluate.", file=sys.stderr)
        return 2
    batches = make_batches(answers, args.batch_size, args.seed)
    with tempfile.TemporaryDirectory(prefix="ab-eval-") as tmp_name:
        tmp = Path(tmp_name)
        work = args.work or tmp / "work"
        work.mkdir(parents=True, exist_ok=True)
        sides = {name: Side(name, spec, args, tmp) for name, spec in zip(SIDES, (args.a, args.b))}
        records = {name: run_side(side, batches, patches, args, work, tmp, catalog_sha)
                   for name, side in sides.items()}
    summaries = {name: summarize_side(records[name], answers, tests, duration, args.batch_size) for name in SIDES}
    for name in SIDES:
        summaries[name]["extra_args"] = sides[name].extra
    report = {"split": args.split, "batch_size": args.batch_size, "seed": args.seed, "repeats": args.repeats,
              "mode": "image" if any(s.image for s in sides.values()) else "local",
              "batches": batches, "skipped": skipped, "baseline": baseline,
              "sides": summaries, "diff": compare(summaries["a"], summaries["b"])}
    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Report: {args.out}", file=sys.stderr)
    if summaries["a"]["errors"] or summaries["b"]["errors"]:
        return 2
    return 1 if report["diff"]["new_misses_in_b"] else 0


if __name__ == "__main__":
    sys.exit(main())

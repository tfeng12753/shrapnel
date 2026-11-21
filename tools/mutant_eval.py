#!/usr/bin/env python3
"""Score selector variants against the held-out mutant set (ground truth from tools/ground_truth.py).

    python3 tools/mutant_eval.py --source ~/work/sqlglot --map maps/dependencies.json.gz \
        --import-closure ~/work/import-closure.json --split holdout --out mutant-eval.json

Only mutants with a results/<id>.json are evaluated. Baseline failures (results/baseline.json)
are subtracted from every mutant. Mutants whose result looks broken (unmapped failure ids, or a
nonzero exit with no failures and no normal pytest summary) are reported as suspicious and left
out of scoring unless --include-suspicious is given.
"""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from score import evaluate_run, _duration_milliseconds, MISS_PENALTY_MILLISECONDS, WORKER_COST_MILLISECONDS  # noqa: E402
from selector import split  # noqa: E402
from tools.variants import VARIANTS, selector_options  # noqa: E402

KINDS = ("code", "fixture", "comment", "multi")
# Last line of a pytest -q run, e.g. "3 failed, 1200 passed, 2 skipped in 61.20s (0:01:01)".
PYTEST_SUMMARY = re.compile(
    r"\b\d+ (passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b.*\bin [\d.]+s"
    r"|no tests ran in [\d.]+s")


def commit_number(commit):
    """Trailing integer of a mutant id ("m01007" -> 1007), or None."""
    match = re.search(r"(\d+)$", commit)
    return int(match[1]) if match else None


def in_split(commit, split_name):
    if split_name == "all":
        return True
    number = commit_number(commit)
    if number is None:
        return False
    return number % 2 == (1 if split_name == "holdout" else 0)


def looks_like_pytest_summary(summary):
    return bool(PYTEST_SUMMARY.search(summary or ""))


def suspicion(result):
    """Reasons a ground-truth result should not be trusted (empty list = fine)."""
    reasons = []
    if result.get("unknown"):
        reasons.append(f"unmapped failure ids: {result['unknown'][:5]}")
    exit_code = result.get("exit")
    failing = result.get("failing") or []
    summary = result.get("summary", "")
    if exit_code not in (0, 1) and not failing and not looks_like_pytest_summary(summary):
        reasons.append(f"exit {exit_code} with no failures and abnormal summary: {summary[-160:]!r}")
    elif exit_code not in (0, 1) and not looks_like_pytest_summary(summary):
        reasons.append(f"exit {exit_code} with abnormal summary: {summary[-160:]!r}")
    if exit_code == 0 and failing:
        reasons.append("exit 0 but failures were recorded")
    if exit_code == 1 and not failing and not result.get("unknown"):
        reasons.append("exit 1 but no failures were recorded")
    return reasons


def schedule_milliseconds(workers, duration):
    occupied = [w for w in workers if w]
    if not occupied:
        return 0
    return max(sum(duration[n] for n in w) for w in occupied) + WORKER_COST_MILLISECONDS * len(occupied)


def oracle_milliseconds(failing, tests_by_number, duration):
    """Cost of scheduling exactly the failing tests with the selector's own packer."""
    return schedule_milliseconds(split([tests_by_number[n] for n in failing]), duration)


def load_ground_truth(mutants, split_name, include_suspicious):
    """Return (answers, patches, report-of-skips)."""
    results_dir = mutants / "results"
    manifest = json.loads((mutants / "manifest.json").read_text())
    skipped = {"missing": [], "errored": [], "suspicious": {}, "out_of_split": 0}
    baseline_failing, baseline_info = set(), None
    baseline_path = results_dir / "baseline.json"
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        baseline_failing = set(baseline.get("failing") or [])
        baseline_info = {"failing": sorted(baseline_failing), "exit": baseline.get("exit"),
                         "summary": baseline.get("summary"), "suspicious": suspicion(baseline)
                         if baseline_failing or baseline.get("exit") not in (0, 1) else []}
    answers, patches = {}, {}
    for commit in sorted(manifest):
        if not in_split(commit, split_name):
            skipped["out_of_split"] += 1
            continue
        patch = mutants / "commits" / f"{commit}.patch"
        result_path = results_dir / f"{commit}.json"
        if not result_path.exists():
            err = results_dir / f"{commit}.err"
            if err.exists() and err.stat().st_size:
                skipped["errored"].append(commit)
            else:
                skipped["missing"].append(commit)
            continue
        try:
            result = json.loads(result_path.read_text())
        except ValueError as error:
            skipped["errored"].append(commit)
            skipped["suspicious"][commit] = [f"unreadable result: {error}"]
            continue
        if not patch.exists():
            skipped["errored"].append(commit)
            continue
        reasons = suspicion(result)
        if reasons:
            skipped["suspicious"][commit] = reasons
            if not include_suspicious:
                continue
        failing = sorted(set(result.get("failing") or []) - baseline_failing)
        answers[commit] = {"failing": failing, "kind": manifest[commit].get("kind", "code")}
        patches[commit] = patch
    return answers, patches, skipped, baseline_info


def evaluate_variant(name, config, args, data, tests, answers):
    duration = {t["number"]: _duration_milliseconds(t["seconds"]) for t in tests}
    by_number = {t["number"]: t for t in tests}
    command = [sys.executable, str(ROOT / "selector.py"), "--data", str(data), "--source", str(args.source),
               "--map", str(args.map), "--import-closure", str(args.import_closure), *selector_options(config)]
    started = time.monotonic()
    process = subprocess.run(command, capture_output=True, timeout=args.timeout)
    wall = time.monotonic() - started
    if process.returncode:
        return {"ok": False, "error": f"selector exit {process.returncode}",
                "stderr": process.stderr.decode(errors="replace")[-2000:], "wall_seconds": wall}
    evaluation = evaluate_run(process.stdout, tests, answers)
    if not evaluation["ok"]:
        return {"ok": False, "error": evaluation, "wall_seconds": wall}
    per_commit, kinds = {}, defaultdict(lambda: {"commits": 0, "misses": 0, "simulated_seconds": 0.0,
                                                 "oracle_seconds": 0.0})
    total_check = 0
    for line in process.stdout.decode().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        commit, workers = row["commit"], row["workers"]
        selected = {n for w in workers for n in w}
        failing = answers[commit]["failing"]
        missed = sorted(set(failing) - selected)
        cost = schedule_milliseconds(workers, duration) + (MISS_PENALTY_MILLISECONDS if missed else 0)
        oracle = oracle_milliseconds(failing, by_number, duration)
        total_check += cost
        kind = answers[commit]["kind"]
        per_commit[commit] = {"kind": kind, "selected": len(selected), "failing": len(failing),
                              "missed_tests": missed, "simulated_seconds": cost / 1000,
                              "oracle_seconds": oracle / 1000}
        bucket = kinds[kind]
        bucket["commits"] += 1
        bucket["misses"] += bool(missed)
        bucket["simulated_seconds"] += cost / 1000
        bucket["oracle_seconds"] += oracle / 1000
    if total_check != evaluation["simulated_milliseconds"]:
        raise SystemExit(f"{name}: per-commit costs {total_check} != evaluate_run {evaluation['simulated_milliseconds']}")
    for bucket in kinds.values():
        bucket["simulated_seconds"] = round(bucket["simulated_seconds"], 3)
        bucket["oracle_seconds"] = round(bucket["oracle_seconds"], 3)
        bucket["excess_seconds"] = round(bucket["simulated_seconds"] - bucket["oracle_seconds"], 3)
    simulated = evaluation["simulated_milliseconds"] / 1000
    oracle = sum(c["oracle_seconds"] for c in per_commit.values())
    return {"ok": True, "config": config, "selector_options": selector_options(config), "commits": len(per_commit),
            "misses": len(evaluation["missed_commits"]),
            "missed": {c: per_commit[c]["missed_tests"] for c in evaluation["missed_commits"]},
            "simulated_seconds": simulated, "oracle_seconds": round(oracle, 3),
            "excess_seconds": round(simulated - oracle, 3), "wall_seconds": round(wall, 2),
            "by_kind": {k: kinds[k] for k in KINDS if k in kinds}, "per_commit": per_commit}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    home = Path.home()
    parser.add_argument("--mutants", type=Path, default=home / "work" / "mutants")
    parser.add_argument("--source", type=Path, default=home / "work" / "sqlglot")
    parser.add_argument("--map", type=Path, default=ROOT / "maps" / "dependencies.json.gz")
    parser.add_argument("--import-closure", type=Path, default=home / "work" / "import-closure.json")
    parser.add_argument("--catalog", type=Path, default=ROOT / "sample" / "tests.json")
    parser.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
    parser.add_argument("--split", choices=("all", "holdout", "tune"), default="all",
                        help="holdout = odd-numbered commits, tune = even-numbered")
    parser.add_argument("--include-suspicious", action="store_true")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--out", type=Path, default=Path("mutant-eval.json"))
    args = parser.parse_args(argv)
    for name in ("mutants", "source", "map", "import_closure", "catalog", "out"):
        setattr(args, name, getattr(args, name).expanduser())

    tests = json.loads(args.catalog.read_text())["tests"]
    answers, patches, skipped, baseline = load_ground_truth(args.mutants, args.split, args.include_suspicious)
    if baseline is None:
        print("WARNING: results/baseline.json missing; no environment failures subtracted", file=sys.stderr)
    elif baseline["failing"]:
        print(f"Subtracting {len(baseline['failing'])} baseline failures: {baseline['failing'][:10]}", file=sys.stderr)
    for label in ("missing", "errored"):
        if skipped[label]:
            print(f"Skipped {len(skipped[label])} {label}: {' '.join(skipped[label])}", file=sys.stderr)
    for commit, reasons in skipped["suspicious"].items():
        verb = "counted" if args.include_suspicious and commit in answers else "excluded"
        print(f"SUSPICIOUS ({verb}) {commit}: {'; '.join(reasons)}", file=sys.stderr)
    report = {"split": args.split, "evaluated_commits": sorted(answers), "skipped": skipped, "baseline": baseline,
              "kinds": {k: sum(a["kind"] == k for a in answers.values()) for k in KINDS}, "variants": {}}
    if not answers:
        print("No mutants with results in this split; nothing to evaluate.", file=sys.stderr)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            shutil.copyfile(args.catalog, data / "tests.json")
            (data / "commits").mkdir()
            for commit, patch in patches.items():
                shutil.copyfile(patch, data / "commits" / f"{commit}.patch")
            for name in args.variants:
                report["variants"][name] = evaluate_variant(name, VARIANTS[name], args, data, tests, answers)
        header = f"{'variant':<22} {'commits':>7} {'misses':>6} {'simulated':>10} {'oracle':>9} {'excess':>9}"
        print(header)
        print("-" * len(header))
        for name, run in report["variants"].items():
            if not run["ok"]:
                print(f"{name:<22} FAILED: {run['error']}")
                continue
            print(f"{name:<22} {run['commits']:>7} {run['misses']:>6} {run['simulated_seconds']:>10.3f}"
                  f" {run['oracle_seconds']:>9.3f} {run['excess_seconds']:>9.3f}")
            for commit, missed in run["missed"].items():
                print(f"    miss {commit} ({answers[commit]['kind']}): {missed[:12]}{' ...' if len(missed) > 12 else ''}")
        ok = {n: r for n, r in report["variants"].items() if r["ok"] and r["misses"] == 0}
        report["best_zero_miss_variant"] = min(ok, key=lambda n: ok[n]["simulated_seconds"]) if ok else None
        print("Best zero-miss variant:", report["best_zero_miss_variant"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

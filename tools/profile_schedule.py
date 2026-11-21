"""Offline sample diagnostics. The selector never receives labeled answers."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from score import MISS_PENALTY_MILLISECONDS, WORKER_COST_MILLISECONDS, evaluate_run
from selector import Selector, split


def schedule_profile(output, tests, answers):
    evaluation = evaluate_run(output, tests, answers)
    if not evaluation["ok"]:
        raise ValueError(f"Invalid schedule: {evaluation}")

    catalog = {t["number"]: t for t in tests}
    durations = {n: round(t["seconds"] * 1000) for n, t in catalog.items()}

    def loads_for(workers):
        return [sum(durations[n] for n in worker) for worker in workers]

    def cost(loads):
        return max(loads, default=0) + WORKER_COST_MILLISECONDS * len(loads)

    patches = []
    for line in output.decode().splitlines():
        item = json.loads(line)
        commit = item["commit"]
        workers = [[int(n) for n in w] for w in item["workers"] if w]
        loads = loads_for(workers)
        actual = cost(loads)
        failures = set(answers[commit]["failing"])
        selected = {n for w in workers for n in w}
        missing = sorted(failures - selected)
        reference = cost(loads_for(split([catalog[n] for n in failures])))
        extras = []
        for i, worker in enumerate(workers):
            other = max(loads[:i] + loads[i + 1:], default=0)
            for n in worker:
                if n in failures:
                    continue
                # Marginal savings with every other test left on its actual worker.
                saving = max(loads) - max(other, loads[i] - durations[n])
                if len(worker) == 1:
                    saving += WORKER_COST_MILLISECONDS
                extras.append((saving, durations[n], n))
        extras.sort(reverse=True)
        patches.append({
            "commit": commit, "schedule_ms": actual, "worker_load_ms": loads,
            "failing_only_schedule_ms": reference, "excess_schedule_ms": actual - reference,
            "selected": len(selected), "missing": missing, "extra_count": len(extras),
            "miss_penalty_ms": MISS_PENALTY_MILLISECONDS if missing else 0,
            "top_extra_tests": [
                {"id": catalog[n]["id"], "number": n, "duration_ms": duration,
                 "single_removal_saving_ms": saving}
                for saving, duration, n in extras[:3]],
        })
    patches.sort(key=lambda p: (-p["excess_schedule_ms"], p["commit"]))
    return {"simulated_ms": evaluation["simulated_milliseconds"],
            "missed_commits": evaluation["missed_commits"], "patches": patches}


def explain_profile(profile, selector, commits):
    for patch in profile["patches"]:
        extras = patch["top_extra_tests"]
        if not extras:
            continue
        for test in extras:
            test["static_selection_reasons"] = []

        def trace(mask, reason, path, location):
            for test in extras:
                if mask & (1 << test["number"]):
                    test["static_selection_reasons"].append(
                        {"reason": reason, "path": path, "location": location})

        selector.select((commits / (patch["commit"] + ".patch")).read_text(), trace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Selector JSONL from the run being profiled")
    parser.add_argument("--data", type=Path, default=ROOT / "sample")
    parser.add_argument("--source", type=Path, help="Pinned checkout from the same run")
    parser.add_argument("--map", type=Path, help="Map from the same run; omit both paths to skip replay")
    parser.add_argument("--no-relations", action="store_true")
    args = parser.parse_args()
    if bool(args.source) != bool(args.map):
        parser.error("supply both --source and --map to trace static selection")
    tests = json.loads((args.data / "tests.json").read_text())["tests"]
    answers = json.loads((args.data / "answers.json").read_text())["commits"]
    profile = schedule_profile(args.output.read_bytes(), tests, answers)
    if args.map:
        if not args.map.is_file() or not args.source.is_dir():
            parser.error("replay requires the original dependency map and pinned source directory")
        selector = Selector(tests, args.source, args.map)
        if args.no_relations:
            for entry in selector.files.values():
                entry["comparisons"].clear()
        explain_profile(profile, selector, args.data / "commits")
    print(json.dumps(profile, indent=2))


if __name__ == "__main__":
    main()

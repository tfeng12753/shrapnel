#!/usr/bin/env python3
"""Record the executor's Python code generation on the pinned checkout (image build time).

    python3 tools/generation_record.py --source /opt/sqlglot --output /app/generation-record.json.gz

For each supported executor test, the real test method runs in this process
after its class's real setUpClass, with process pools replaced by inline
execution (pickle round-trips kept). Every call of the executor's Python
generator is recorded: the generator instance it belongs to (a session), its
input expression (serialized), and the code it returned. Function and lambda
code of the pinned library is classified by where it starts executing: inside
a generator call only, or anywhere else (imports, class setup, planning,
execution, the reference database side). Each seed runs in a fresh
interpreter with that hash seed; the record is kept only if every seed
produced identical calls and the test passed.

A baseline replay of the recorded calls (as `generation_check.py` performs it
at selection time) is run once per seed to record its CPU and the global-state
change it causes, which a patched replay must reproduce.

Runs on the unmodified pinned checkout only. No sample patch, answer or mutant
is read.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
VERSION = 1
TESTS = (
    "tests/test_executor.py::TestExecutor::test_execute_tpch",
    "tests/test_executor.py::TestExecutor::test_execute_tpcds",
)
# Files whose content the record depends on beyond the library itself.
AUDITED = ("tests/test_executor.py", "tests/helpers.py")


def checkout_hashes(source):
    hashes = {}
    for path in sorted((source / "sqlglot").rglob("*.py")):
        hashes[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in AUDITED:
        hashes[name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
    return hashes


# ----------------------------------------------------------------- child side

def record_child(source, tests):
    """Run the tests in this process; return {test: {...}} (stdout JSON)."""
    source = source.resolve()
    library = str(source / "sqlglot") + os.sep
    monitoring = sys.monitoring
    tool = monitoring.PROFILER_ID
    monitoring.use_tool_id(tool, "generation-record")
    depth = [0]
    inside, outside = set(), set()
    disabled = monitoring.DISABLE

    def started(code, offset):
        filename = code.co_filename
        if not filename.startswith(library):
            return disabled
        key = f"{filename[len(str(source)) + 1:]}:{code.co_firstlineno}:{code.co_name}"
        if depth[0]:
            inside.add(key)
            if current[0] is not None:
                current[0].add(key)
            return None
        outside.add(key)
        # Nothing more to learn about code already seen outside a generator call.
        return disabled

    current = [None]
    events = monitoring.events
    monitoring.register_callback(tool, events.PY_START, started)
    monitoring.register_callback(tool, events.PY_RESUME, started)
    monitoring.set_events(tool, events.PY_START | events.PY_RESUME)

    os.chdir(source)
    sys.path.insert(0, str(source))
    os.environ["SKIP_INTEGRATION"] = "0"
    sys.dont_write_bytecode = True
    import importlib.util
    spec = importlib.util.spec_from_file_location("fixture_replay", ROOT / "fixture_replay.py")
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)

    from sqlglot import serde
    from sqlglot.generators.python import PythonGenerator

    sessions, calls = {}, []
    keep = []

    def generate(self, expression, copy=True):
        if id(self) not in sessions:
            sessions[id(self)] = len(sessions)
            keep.append(self)
        before = json.dumps(serde.dump(expression), sort_keys=True)
        session_keys = set()
        outer = current[0]
        current[0] = session_keys
        depth[0] += 1
        try:
            output = base_generate(self, expression, copy)
        finally:
            depth[0] -= 1
            current[0] = outer
        after = json.dumps(serde.dump(expression), sort_keys=True)
        calls.append({"session": sessions[id(self)], "input": before, "output": output,
                      "mutated": before != after, "keys": sorted(session_keys)})
        return output

    base_generate = PythonGenerator.generate
    PythonGenerator.generate = generate

    specs = {test: {} for test in tests}
    modules, classes = replay.prepare(source, specs)
    results = {}
    for test in tests:
        module_name, class_name, method = replay._split_id(test)
        sessions.clear()
        keep.clear()
        calls.clear()
        outcome = replay._Outcome()
        started_cpu = time.process_time()
        classes[(module_name, class_name)](method).run(outcome.result)
        results[test] = {"passed": outcome.passed(), "cpu": time.process_time() - started_cpu,
                         "calls": list(calls)}
    monitoring.set_events(tool, 0)
    monitoring.free_tool_id(tool)
    return {"tests": results, "inside": sorted(inside - outside), "outside": sorted(outside)}


def replay_child(source, record_path):
    """Baseline replay of every recorded call: {test: [cpu, state fingerprint, matched]}."""
    sys.path.insert(0, str(ROOT))
    import generation_check

    data = generation_check.load(record_path)
    result = {}
    for test in data["tests"]:
        matched, fingerprint, cpu = generation_check.replay_in_child(source, {}, data, test, fork=True)
        result[test] = [cpu, fingerprint, matched]
    return result


# ---------------------------------------------------------------- parent side

def run_child(args, seed, extra):
    env = dict(os.environ, PYTHONHASHSEED=str(seed), SKIP_INTEGRATION="0", PYTHONDONTWRITEBYTECODE="1",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    started = time.monotonic()
    completed = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--source", str(args.source),
                                *extra], env=env, stdout=subprocess.PIPE, timeout=args.timeout, check=True)
    print(f"seed {seed} {' '.join(extra[:1])}: {time.monotonic() - started:.1f} s", file=sys.stderr)
    return json.loads(completed.stdout)


def distinct_calls(records):
    """{input: (output, keys)} over every seed, or None if seeds disagree.

    Planning order follows hash-seeded set iteration, so seeds may issue the
    same calls in a different order. The generator keeps no per-call state
    the executor's calls use (each distinct input always returned the same
    code); a seed that produced a different set of calls, a different output
    for the same input, or mutated an input makes the test unrecorded.
    """
    merged = None
    for record in records:
        calls = {}
        for call in record["calls"]:
            if call["mutated"]:
                return None
            output, keys = calls.setdefault(call["input"], (call["output"], set()))
            if output != call["output"]:
                return None
            keys.update(call["keys"])
        if merged is None:
            merged = calls
        elif merged.keys() != calls.keys() or any(merged[k][0] != calls[k][0] for k in calls):
            return None
        else:
            for k, (_, keys) in calls.items():
                merged[k][1].update(keys)
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--output", type=Path, default=ROOT / "generation-record.json.gz")
    parser.add_argument("--seeds", default="1,2")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--replay", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.source = args.source.resolve()
    if args.child:
        json.dump(record_child(args.source, TESTS), sys.stdout)
        return
    if args.replay:
        json.dump(replay_child(args.source, args.replay), sys.stdout)
        return

    seeds = [int(s) for s in args.seeds.split(",") if s]
    runs = [run_child(args, seed, ["--child"]) for seed in seeds]
    tests = {}
    outside = set().union(*(set(r["outside"]) for r in runs))
    inside_list = sorted(set().union(*(set(r["inside"]) for r in runs)) - outside)
    index = {k: i for i, k in enumerate(inside_list)}
    for test in TESTS:
        records = [r["tests"][test] for r in runs]
        if not all(r["passed"] for r in records):
            print(f"{test}: did not pass on the pinned checkout; not recorded", file=sys.stderr)
            continue
        merged = distinct_calls(records)
        if merged is None:
            print(f"{test}: generator calls differ between hash seeds; not recorded", file=sys.stderr)
            continue
        # Calls in first-seen order of the first seed; keys as indices into the inside list.
        tests[test] = {"calls": [[text, output, sorted(index[k] for k in keys if k in index)]
                                 for text, (output, keys) in merged.items()],
                       "keys": sorted({k for _, keys in merged.values() for k in keys}),
                       "test_cpu": max(r["cpu"] for r in records)}
    data = {"version": VERSION, "checkout": checkout_hashes(args.source), "inside": inside_list,
            "outside": sorted(outside), "tests": tests}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))

    # Baseline replay: CPU and the global-state change a patched replay must reproduce.
    replays = [run_child(args, seed, ["--replay", str(args.output)]) for seed in seeds]
    for test in tests:
        outcomes = [r[test] for r in replays]
        if not tests[test]["calls"]:
            continue  # No generator call: nothing to replay.
        if not all(o[2] is True for o in outcomes) or len({o[1] for o in outcomes}) != 1 or outcomes[0][1] is None:
            print(f"{test}: baseline replay unstable; not recorded", file=sys.stderr)
            tests[test] = None
            continue
        tests[test]["replay_cpu"] = max(o[0] for o in outcomes)
        tests[test]["state"] = outcomes[0][1]
    data["tests"] = {t: v for t, v in tests.items() if v is not None}
    with gzip.open(args.output, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    for test, value in data["tests"].items():
        print(f"{test}: {len(value['calls'])} distinct calls, "
              f"replay {value.get('replay_cpu', 0):.2f} s CPU", file=sys.stderr)


if __name__ == "__main__":
    main()

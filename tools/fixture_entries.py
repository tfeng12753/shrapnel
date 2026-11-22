#!/usr/bin/env python3
"""Record per-entry execution for heavy fixture-driven tests (offline or at image build time).

    python3 tools/fixture_entries.py --source /opt/sqlglot --out fixture-entries.json.gz

Runs on the unmodified pinned checkout only. For each supported test, every
fixture entry is run alone in a forked child after the class's real setUpClass:
once without tracing (CPU, pass, global-state fingerprint) and once under
coverage (source lines of the pinned library it executes; the fingerprint must
repeat). Lines executed by imports, class setup or an empty run of the test are
recorded as the test's base. Nothing from sample patches or mutants is read.
"""
import argparse
import gzip
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"

TPCH = "tests/fixtures/optimizer/tpc-h/tpc-h.sql"
TPCDS = "tests/fixtures/optimizer/tpc-ds/tpc-ds.sql"
LOADER = "load_sql_fixture_pairs"
# attribute: list stored by setUpClass; otherwise the module-level loader is
# restricted when the test method calls it with loader_argument.
SPECS = {
    "tests/test_executor.py::TestExecutor::test_execute_tpch": {"fixture": TPCH, "attribute": "tpch_sqls"},
    "tests/test_executor.py::TestExecutor::test_execute_tpcds": {"fixture": TPCDS, "attribute": "tpcds_sqls"},
    "tests/test_executor.py::TestExecutor::test_optimized_tpch": {"fixture": TPCH, "attribute": "tpch_sqls"},
    "tests/test_optimizer.py::TestOptimizer::test_tpch": {"fixture": TPCH, "loader_argument": "optimizer/tpc-h/tpc-h.sql"},
    "tests/test_optimizer.py::TestOptimizer::test_tpcds": {"fixture": TPCDS, "loader_argument": "optimizer/tpc-ds/tpc-ds.sql"},
}
AUDITED = ("tests/helpers.py", "tests/test_executor.py", "tests/test_optimizer.py", TPCH, TPCDS)


def load_replay_module():
    # Import by path: this project's own tests/ package must not shadow the pinned one.
    spec = importlib.util.spec_from_file_location("fixture_replay", ROOT / "fixture_replay.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["fixture_replay"] = module
    spec.loader.exec_module(module)
    return module


def covered_lines(data, source):
    lines = {}
    for filename in data.measured_files():
        path = Path(filename).resolve()
        if not path.is_relative_to(source / "sqlglot"):
            continue
        executed = data.lines(filename) or []
        if executed:
            lines[path.relative_to(source).as_posix()] = sorted(executed)
    return lines


def clear_registry():
    from sqlglot.dialects.dialect import Dialect
    Dialect._classes.clear()


def start_coverage(source):
    import coverage
    cov = coverage.Coverage(data_file=None, branch=False, config_file=False,
                            include=[str(source / "sqlglot" / "*")])
    cov.start()
    return cov


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--out", type=Path, default=Path("fixture-entries.json.gz"))
    parser.add_argument("--tests", default="", help="comma-separated subset of supported test ids")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds per entry")
    args = parser.parse_args()
    source = args.source.resolve()
    if (source / ".pinned-revision").exists() and (source / ".pinned-revision").read_text().strip() != REVISION:
        sys.exit("pinned checkout revision mismatch")
    selected = [t for t in args.tests.split(",") if t] or list(SPECS)
    replay = load_replay_module()
    os.chdir(source)
    sys.path.insert(0, str(source))
    sys.dont_write_bytecode = True
    os.environ["SKIP_INTEGRATION"] = "0"

    specs = {}
    for test_id in selected:
        spec = dict(SPECS[test_id], loader=LOADER)
        specs[test_id] = spec

    cov = start_coverage(source)
    started = time.process_time()
    __import__("sqlglot")
    for test_id in specs:
        __import__(test_id.split("::")[0][:-3].replace("/", "."))
    import_cpu = time.process_time() - started
    setup_cpu = {}
    modules, classes = replay.prepare(source, specs, setup_cpu)
    cov.stop()
    global_base = covered_lines(cov.get_data(), source)
    del cov

    # Entry counts from the loaded fixtures (class attribute or a direct loader call).
    helpers = sys.modules["tests.helpers"]
    for test_id, spec in specs.items():
        rows = list(getattr(helpers, LOADER)(spec["fixture"].removeprefix("tests/fixtures/")))
        spec["entries"] = len(rows)

    # Pass 1, before anything is preloaded: every entry traced cold, and again with the
    # dialect registry cleared (tests that run earlier in the suite clear it). Lines an
    # entry runs only on these paths are never verified by a warm replay, so they become
    # base: edits there are refused. The same runs find the modules entries import lazily.
    lazy, cold = set(), {}
    for test_id, spec in specs.items():
        for entry in range(spec["entries"]):
            for cleared in (False, True):
                def traced_cold(entry=entry, spec=spec, test_id=test_id, cleared=cleared):
                    known = set(sys.modules)
                    if cleared:
                        clear_registry()
                    cov = start_coverage(source)
                    passed = replay.run_entry(modules, classes, spec, test_id, entry)
                    cov.stop()
                    imported = sorted(n for n in set(sys.modules) - known
                                      if n == "sqlglot" or n.startswith("sqlglot.") or n in replay.STATE_MODULES)
                    return [passed, imported, covered_lines(cov.get_data(), source)]
                found = replay.run_isolated(traced_cold, time.monotonic() + args.timeout)
                if found is None or found[0] is not True:
                    sys.exit(f"entry {entry} of {test_id} failed on the cold path (cleared={cleared})")
                lazy.update(found[1])
                lines = cold.setdefault((test_id, entry), {})
                for name, executed in found[2].items():
                    lines.setdefault(name, set()).update(executed)
    preloaded = sorted(lazy)
    # Preloading runs import-time code; it counts as base like every other import.
    cov = start_coverage(source)
    replay.preload(preloaded)
    cov.stop()
    for name, lines in covered_lines(cov.get_data(), source).items():
        global_base[name] = sorted(set(global_base.get(name, ())) | set(lines))
    del cov
    print(f"preloaded {len(preloaded)} lazily imported modules", file=sys.stderr, flush=True)

    tests = {}
    for test_id, spec in specs.items():
        print(f"recording {test_id}: {spec['entries']} entries", file=sys.stderr, flush=True)
        deadline = lambda: time.monotonic() + args.timeout  # noqa: E731

        def empty_run():
            cov = start_coverage(source)
            passed = replay.run_entry(modules, classes, spec, test_id, None)
            cov.stop()
            return [passed, covered_lines(cov.get_data(), source)]

        empty = replay.run_isolated(empty_run, deadline())
        if empty is None or empty[0] is not True:
            sys.exit(f"empty run failed for {test_id}")
        base = {name: set(lines) for name, lines in global_base.items()}
        for name, lines in empty[1].items():
            base.setdefault(name, set()).update(lines)

        entry_cpu, fingerprints, by_line = [], [], {}
        for entry in range(spec["entries"]):
            def plain(entry=entry):
                started = time.process_time()
                passed, fingerprint = replay.entry_outcome(modules, classes, spec, test_id, entry)
                cpu = time.process_time() - started
                return [passed, cpu, fingerprint]

            def traced(entry=entry):
                shared = sorted(classes.values(), key=lambda c: (c.__module__, c.__qualname__))
                before = replay.state_summary(shared)
                cov = start_coverage(source)
                passed = replay.run_entry(modules, classes, spec, test_id, entry)
                cov.stop()
                return [passed, covered_lines(cov.get_data(), source),
                        replay.state_fingerprint(before, replay.state_summary(shared))]

            first, second = replay.run_isolated(plain, deadline()), replay.run_isolated(traced, deadline())
            if first is None or second is None or first[0] is not True or second[0] is not True:
                sys.exit(f"entry {entry} of {test_id} did not pass on the pinned checkout")
            if not any(second[1].values()):
                # An entry without recorded lines would look unaffected by every edit.
                sys.exit(f"entry {entry} of {test_id} recorded no executed lines")
            entry_cpu.append(round(first[1], 3))
            # A fingerprint that does not repeat can never verify a replay.
            fingerprints.append(first[2] if first[2] is not None and first[2] == second[2] else None)
            for name, lines in cold[(test_id, entry)].items():
                extra = lines - set(second[1].get(name, ()))
                if extra:
                    base.setdefault(name, set()).update(extra)
            for name, lines in second[1].items():
                file_lines = by_line.setdefault(name, {})
                for line in lines:
                    file_lines.setdefault(str(line), []).append(entry)
        tests[test_id] = {
            **spec,
            "base": {name: sorted(lines) for name, lines in sorted(base.items())},
            "lines": {name: dict(sorted(lines.items(), key=lambda kv: int(kv[0]))) for name, lines in sorted(by_line.items())},
            "entry_cpu": entry_cpu,
            "fingerprints": fingerprints,
        }
        unstable = sum(f is None for f in fingerprints)
        print(f"  cpu {sum(entry_cpu):.2f}s, unstable fingerprints {unstable}", file=sys.stderr, flush=True)

    data = {
        "version": replay.VERSION,
        "revision": REVISION,
        "python": "%d.%d" % sys.version_info[:2],
        "files": {name: replay.sha256_file(source / name) for name in AUDITED},
        "library": replay.library_digest(source),
        "import_cpu": round(import_cpu, 3),
        "preload": preloaded,
        "setup_cpu": {k: round(v, 3) for k, v in setup_cpu.items()},
        "tests": tests,
    }
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    out = args.out if args.out.is_absolute() else ROOT / args.out
    with open(out, "wb") as f:
        # mtime=0 keeps the archive byte-identical for identical content.
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as archive:
            archive.write(payload)
    print(f"wrote {out}: {len(tests)} tests", file=sys.stderr)


if __name__ == "__main__":
    main()

# Selector and verification track

The branch starts at `f08f491` on `main`. Submit changes only through `overnight/selector-verification` and a draft PR. Preserve the shipped configuration, leave map generation to the other track, and use even-numbered mutants only.

- The pinned sqlglot source (`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`), generated import closure, and Python 3.13.5 environment with all 43 locked packages are in `/workspace/selector-bench` in this workspace.
- The [ground-truth run](https://github.com/tfeng12753/shrapnel/actions/runs/44888276378) is in progress. No artifact was available at kickoff.
- Next: implement whole-selection verification with deterministic budget admission and conservative timeout handling, inspect the map for boolean/literal evidence, harden relations, and run five public replays per configuration. Record exact configurations, map fingerprint, code revision, schedule costs, misses and CPU here as measurements finish.

## Whole-selection verification

The probe estimates CPU as 0.22 s plus 1.25 times catalog duration. It requires a zero-worker saving above three times that estimate and reserves a deadline of 0.5 s plus twice catalog duration, capped at 1.5 s per batch. The global budget charges that fixed reservation whether the probe passes, fails or times out. Admission is based on the normal sorted patch order, catalog timings and available budget. Only a complete batch of actual native unittest successes removes a selection. Skips, failures, unknown tests, unsupported overlays, and partial results retain it.

Enable with `--probe-budget 8 --cost-aware-probes` or `"probe_budget": {"seconds": 8.0, "cost_aware": true}`. The optional `cost_aware` child key defaults to false. The existing root key stays in `tools/competitive.py:BASE` with a numeric value. The config tests require BASE to match the checked-in JSON's exact root keys, so the nested form preserves both the benchmark schema and the shipped file. Numeric command-line budgets select the existing policy unless the new flag is also present.

82 unit tests pass. Public replay and official tune results are pending. For a fallback independent tune sample, generate 32 mutants at seed 42 and run only the 16 even IDs plus the unmodified baseline through `tools/ground_truth.py`. This is running in `/workspace/selector-bench/local-tune`. The GitHub run has 20 shards in progress. The next checkpoint will record what the current map can prove about boolean and literal edits.

## Boolean and literal map evidence

The cached v1 map has 727 comparison sites (`Is` 349, `IsNot` 252, `Gt` 71, `Lt` 27, `GtE` 16, `LtE` 12). Its file records contain only line, region, anchor, file-read, execution and comparison masks. There are no Boolean operation or literal value observations. Coverage cannot prove the value, type, evaluation history or side effects, so this branch leaves Boolean and literal selection coverage-based.

Builder contract for a future refinement:

- Version the evidence and identify sites by baseline path, AST operator and full source span, including the byte columns. Record baseline operator or exact typed literal, operand arity, relevant consumer and its context. Store masks of *every* observed state per test, and an unknown mask that includes uncovered executions, collection or shared-fixture execution, exceptions, instrumentation gaps and incomplete attribution. Preserve attribution inside child work.
- For an `and`/`or` swap, record which operands were actually evaluated, their proven builtin truth states, and whether the consumer uses their truth or the returned operand itself. Python may return a different object even if the truth states agree. A newly evaluated RHS may raise, mutate state or perform I/O. Treat skipped dynamic operands and custom truth conversion as unknown. A first safe domain is two operands in a truth-only context, with a skipped RHS statically proven total and side-effect-free for the operand domain. Compare short-circuit execution as well as results. Never evaluate an otherwise skipped RHS to collect evidence.
- For a Boolean or numeric literal, record per-site typed observations of a narrowly supported, pure consumer and the other exact builtin operands needed to recompute it. Require equal value **and type**, equal truth and exception behavior, and exclude identity, escaping values or values observable outside the consumer. Retain tests for uses through names, attributes, overloaded operations, mutable state, default arguments, shared setup, or missing data unless the full affected dependency path is proven.
- Comparison observations must mark unsupported operand types before invoking the old operator and mark exceptions, then preserve the original exception. Otherwise a test that observes a safe relation once and catches an exception from another execution of that same site is indistinguishable from a test with only the safe observation. Expose this completeness explicitly if ordering refinements are to rely on it.

Initial public replay at `cf8513c` in CPython 3.13.5 with the lockfile and generated import closure. Exact common settings were `{"conservative_python":false,"child_work":false,"import_check_budget":6.0}`, plus the indicated relations and probe settings. Map `builder_sha256=cc729b2e5ee84c1fb3fb7d656fbc8f03c9f6a362a6f6801d6407e9a9d48ad7d3`, `map_sha256=02198f60b3809b26d3f5f3fcfd55e9882880b1483c38e6d94d6c56021418b372`. Probe setting was `0.0`, `8.0`, or `{"seconds":8.0,"cost_aware":true}`.

| Relations | Probes | Simulated s | Misses | CPU s |
| --- | --- | ---: | ---: | ---: |
| Off | Off | 166.316 | 0 | 0.695492 |
| Off | Legacy | 164.286 | 0 | 1.102586 |
| Off | Cost-aware | 162.129 | 0 | 3.567381 |
| On | Off | 115.907 | 0 | 0.627968 |
| On | Legacy | 113.877 | 0 | 1.180990 |
| On | Cost-aware | 111.720 | 0 | 3.451928 |

Next: exercise relation states and multi-line/nested/mixed edits adversarially, then rerun the full public matrix and compare output digests.

## Comparison audit

The command-line `--relations` path now prunes isolated identity edits with usable evidence. It retains unknown or malformed states, uncovered users in the entire comparison span, and falls back to coverage for ordering edits, chains and combined edits. A comparison in a default argument, decorator or other non-function body conservatively keeps the full catalog. Ordering on the current map is unsafe because the observer runs the original operator before it records anything. A caught exception from a custom comparator can be hidden by a successful numeric execution of the same site in the same test. Existing Python callers that omit the constructor's relations argument retain the prior ordering behavior. Pass `relations=True` explicitly to exercise the guarded path. The harness covers unusual comparison types, partial ordering, None, nested comprehensions/lambdas, chains, multiline coverage and mixed edits. 91 unit tests pass.

Ground-truth audit: the published workflow's `tools/ground_truth.py` misses pytest 9.2 `SUBFAILED(...)` reports because its text regex only recognizes `SUBFAIL` followed by whitespace. A synthetic failing unittest subtest reproduced it. The original local `m42000` run also reported two failures in the summary but only recorded the unrelated `test_lazy_load` baseline failure. In this workspace the original ground-truth subprocess PATH also lacked a `python` executable for that test. Treat original local results and incomplete official shard labels as unreliable. The corrected local tune run, launched from `/workspace/selector-bench/ground_tune_audit.py`, prepends the locked venv to PATH and records structured `pytest_runtest_logreport` and `pytest_collectreport` failures, including subtests, before translating node IDs to the catalog. It uses an independent copied checkout for each even mutant and the baseline under real process pools. The repo's ground-truth tool is outside the allowed file set. The corrected run is resumable per JSON result.

Draft PR [#10](https://github.com/tfeng12753/shrapnel/pull/10) is open. Next: five public replays per combination plus both legacy controls, profile marginal savings, and score the locally audited even mutants. Recheck availability of the official artifact.

## Repeat audit and timing fix

The five-run local audit of `204b4dd` (same fingerprint and settings as above) found that cost-aware schedules differed by one worker between runs. With relations off they alternated between 162.129 and 164.159 simulated seconds, with zero misses. The changing candidate was confirmed under one CPU scheduling load and timed out under another. Both other configurations with relations off produced one digest each. With guarded relations and probes off the schedule was 118.443 simulated seconds, zero misses, with one digest across five runs.

Cost-aware probing now reserves a **CPU** deadline, enforced with Linux `ITIMER_PROF` in the forked worker. A separate 2.0 s minimum / 3x CPU wall limit handles waiting or blocked I/O. A probe that attempts Python subprocess or process creation is ineligible, even if its test catches the runtime guard. This protects overlay visibility and bounds the work to the profiled process. The static reservation is still charged on every attempt. Unit tests cover blocked versus busy workers, caught subprocess attempts, and skipped tests. Re-run all six configurations from this code revision under different hash seeds.

The first scorer-image build could not reach the Python package index from BuildKit. Continue with the locked local environment and retry the image build via a local wheelhouse if supported. Ground-truth results are still running.

The cost-aware verifier now uses the one-worker scheduler order and stops at the first failure. It also retains source-reading tests whose I/O might bypass the overlay. Comparison coverage now checks shared setup and mixed edits at collection-sensitive spans. 96 unit tests pass. The scorer image can build with the sandbox host CA mounted during dependency and pinned-source fetches. The CA is not part of the run image.

Local ground-truth recovery: the subprocess entry point must guard `pytest.main` with `if __name__ == "__main__":` so sqlglot's spawn-based process pools do not rerun the suite in workers. The unguarded attempt was stopped and isolated. The guarded producer is running for seed 42, IDs `m42000` through `m42030` at stride 2 and the unmodified baseline. Only its audited result directory is used for tune scoring.

## Final local public replay

Code revision `b690300532da5491159a0eb550b37db17dad407f`. CPython 3.13.5 and the pinned requirements. Each row ran the full 30-patch sample five times, with PYTHONHASHSEED 1 through 5. The schedule SHA-256 was identical across the five runs in each row. The CPU measure is user plus system time via `getrusage(RUSAGE_CHILDREN)` around the selector, including reaped native children. The published map hashes and common config above apply. The probe settings remain `0.0`, `8.0`, or `{"seconds":8.0,"cost_aware":true}`.

| Relations | Probes | Simulated s | Misses | Median CPU s | Added CPU vs off | Simulated + median CPU s | Workers |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Off | off | 166.316 | 0 | 0.912231 | +0.000000 | 167.228231 | 30 |
| Off | legacy | 164.286 | 0 | 1.137597 | +0.225366 | 165.423597 | 29 |
| Off | cost-aware | 162.129 | 0 | 4.001300 | +3.089069 | 166.130300 | 28 |
| On | off | 118.443 | 0 | 0.781266 | +0.000000 | 119.224266 | 28 |
| On | legacy | 116.413 | 0 | 1.271257 | +0.489991 | 117.684257 | 27 |
| On | cost-aware | 114.256 | 0 | 4.067166 | +3.285900 | 118.323166 | 26 |

Per-run CPU seconds (seeds 1–5) and the five-run schedule digests

- `relations_false__probes_off`: 0.979664, 0.912231, 0.998284, 0.763636, 0.735082. SHA-256 `7189decd1832209759637263631ccef60dd42cde664cbf4544b6fa6a53379e1e`.
- `relations_false__probes_legacy`: 1.239918, 1.120134, 1.190995, 1.083052, 1.137597. SHA-256 `baf8b7a5b67d3cd13354cdf90ac243924bfa123abd4b89e3fc5fa776a213a239`.
- `relations_false__probes_cost_aware`: 3.955911, 3.990255, 4.001300, 4.152525, 4.230904. SHA-256 `9d0ab0898af499c32329c151099d5ce247f1bdff38fa06f36ad8d9cf2d68e6b7`.
- `relations_true__probes_off`: 0.848707, 0.729859, 0.792442, 0.685483, 0.781266. SHA-256 `6bbcfeac36700189676d9cb458ed67a1dbfd2f4d10d3e941deb5975f4c2cf89a`.
- `relations_true__probes_legacy`: 1.191072, 1.314190, 1.271257, 1.298325, 1.181652. SHA-256 `7e6042b43dfc30773220846110b10e858d981aa5560dd8ad8f2c1ed396fd4d53`.
- `relations_true__probes_cost_aware`: 4.208106, 3.959116, 4.000618, 4.131078, 4.067166. SHA-256 `2060dbee521cc16173e5b5583224dbfd5e3269a97878aed6671dfb0c712a200e`.

The final code removed the observed local timing variance in this replay. A future workload can still exceed the CPU or backup wall deadline. Any such timeout retains the entire selection. The cost-aware policy removes two workers and improves the measured total versus probes off by 1.098 s with relations off and 0.901 s with relations on. The legacy policy has a slightly lower measured total than cost-aware verification in both local comparisons. Profiling and `score.py` independently confirmed 114.256 simulated seconds and zero misses for guarded relations plus cost-aware probes.

The scorer-format image built successfully using a temporary test Dockerfile to mount the sandbox's trust bundle during dependency fetches. The first container replay exhausted the shared VFS layer storage and was retried after removing this task's intermediate image and build cache. The local evidence above is retained independently.

## Resume commands

Check out the PR branch and run from the repository root. In a fresh workspace, pick a writable benchmark directory (the overnight workspace used `/workspace/selector-bench`).

```sh
export SELECTOR_BENCH=/workspace/selector-bench
mkdir -p "$SELECTOR_BENCH/sqlglot"
curl -fLsS https://codeload.github.com/tobymao/sqlglot/tar.gz/eb11aec4be8cb2122afe713a4816662c3f0fb2fa |
  tar -xz -C "$SELECTOR_BENCH/sqlglot" --strip-components=1
UV_CACHE_DIR="$SELECTOR_BENCH/uv-cache" uv venv --python python3.13 "$SELECTOR_BENCH/venv"
UV_CACHE_DIR="$SELECTOR_BENCH/uv-cache" uv pip sync --python "$SELECTOR_BENCH/venv/bin/python" requirements.lock
"$SELECTOR_BENCH/venv/bin/python" tools/import_closure.py --source "$SELECTOR_BENCH/sqlglot" --output "$SELECTOR_BENCH/import-closure.json"
```

Run `selector.py --data sample --source "$SELECTOR_BENCH/sqlglot" --map maps/dependencies.json.gz --import-closure "$SELECTOR_BENCH/import-closure.json"` with the locked venv. For each of `--no-relations` and `--relations`, run with `--probe-budget 0 --no-cost-aware-probes`, `--probe-budget 8 --no-cost-aware-probes`, and `--probe-budget 8 --cost-aware-probes`. Use seeds 1 through 5 for `PYTHONHASHSEED`, capture stdout in distinct files, compare with `cmp` or `sha256sum` and score each with `python3 score.py sample sample/answers.json FILE`. Measure CPU with `/usr/bin/time -f '%U %S'` (sum the two columns), `tools/timed_run.py` inside the image, or the change in user plus system `getrusage(RUSAGE_CHILDREN)` around one `subprocess.run`. The latter was used for the local table. This workspace has the full harness at `$SELECTOR_BENCH/replay.py`, invoked with an output directory and optional `--runs 5` or `--mutants "$SELECTOR_BENCH/local-tune"`.

For independent tune ground truth, generate seed 42 with `tools/mutants.py --source "$SELECTOR_BENCH/sqlglot" --out "$SELECTOR_BENCH/local-tune" --count 32 --seed 42`. Process only the 16 even IDs and run an unmodified baseline. Resume by skipping the IDs with completed structured results. For each, copy the pinned checkout into a fresh directory and apply its unified diff with `selector.parse_patch` and `selector.apply_change` before starting pytest. The audited process must run pytest's real suite with its working directory and first import path at the copy, `SKIP_INTEGRATION=0`, `HOME=/tmp`, `PYTHONDONTWRITEBYTECODE=1` and the locked venv's bin directory first on PATH. Run the full suite with `tests -q -p no:cacheprovider -rfE --timeout=900`, using a guarded Python entry point. Reuse the original `results/*.json` schema. Keep real process pools, and choose local concurrency to fit available cores and RAM (two in this workspace).

The structured pytest recorder is below. Supply it with the absolute catalog path and output JSON path. Keep the console log alongside each result and use its last nonblank line as the JSON summary. In this workspace, `$SELECTOR_BENCH/pytest_ground.py` and `$SELECTOR_BENCH/ground_tune_audit.py` implement the recorder and resumable producer.

<details>
<summary>Structured pytest failure recorder</summary>

```python
import json
from pathlib import Path
import sys
import time

import pytest

class Failures:
    def __init__(self, numbers):
        self.numbers = numbers
        self.failing = set()
        self.unknown = set()

    def record(self, report):
        if not report.failed:
            return
        item = report.nodeid
        if item in self.numbers:
            self.failing.add(self.numbers[item])
        else:
            base = item.partition("[")[0]
            matched = [n for test, n in self.numbers.items() if test == base or test.startswith(base + "::")]
            self.failing.update(matched)
            if not matched:
                self.unknown.add(item)

    def pytest_runtest_logreport(self, report):
        self.record(report)

    def pytest_collectreport(self, report):
        self.record(report)


def main():
    sys.path.insert(0, str(Path.cwd()))
    tests = json.loads(Path(sys.argv[1]).read_text())["tests"]
    failures = Failures({t["id"]: t["number"] for t in tests})
    started = time.monotonic()
    exit_code = pytest.main(["tests", "-q", "-p", "no:cacheprovider", "-rfE", "--timeout=900"], plugins=[failures])
    Path(sys.argv[2]).write_text(json.dumps({"failing": sorted(failures.failing),
        "unknown": sorted(failures.unknown), "exit": int(exit_code),
        "summary": "see adjacent pytest log", "seconds": round(time.monotonic() - started, 3)}))


if __name__ == "__main__":
    main()
```

</details>

Use `tools/mutant_eval.py --split tune` to replay its built-in variants. To include the cost-aware variant, use `tools.mutant_eval.load_ground_truth(mutants, "tune", False)`, make a temporary data directory containing the catalog and only the returned patches, run the six configurations above, and pass each output plus the loaded answers to `score.evaluate_run`. Record CPU independently of simulated seconds. Reserve odd mutant IDs for morning. The official artifact is useful when published and when its failure labels account for subtest failures. The audited local set gives separately verifiable tune evidence.

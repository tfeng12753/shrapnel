# Agent 4 progress

## RESUME HERE
- Branch: `agent4/fixtures-eval` (from `main` 0871e19), mirrored to `claude/agent-four-workflow-1vada3`.
- Pinned checkout in the sandbox: plain `git clone` of the upstream repository, then
  `git checkout eb11aec4be8cb2122afe713a4816662c3f0fb2fa` (the source tarball host is blocked here).
  Venv: `python3 -m venv venv && venv/bin/pip install -r requirements.lock coverage==7.10.7`.
- Done and pushed: Task 2 `tools/ab_eval.py`; Task 1 `fixture_replay.py`, `tools/fixture_entries.py`,
  call site, `fixture-entries.json.gz` (recorded offline, about 6 minutes); Task 3 families in
  `tools/mutants.py`, collection-error attribution in `tools/ground_truth.py`, `families` workflow input
  (old seeds reproduce identical mutants; 48 new-family smoke patches apply; real ground truth outside Docker:
  baseline 0 failures, base-dialect concat-setting flip 1120 failing tests with 34 files failing collection,
  one TPC-H fixture-entry edit 2 failures, one comparison swap 2 failures).
- Merged `main` @ 06cf910 (downstream import check) into this branch at 155dae5; the replay call site is in
  `_select_base()`, so `select()`'s downstream import additions still apply on top of it.
- Running now (Task 4, local, scratch dir `<scratch>`):
  1. repairing the 121 holdout seed100 results whose failures were lost to multi-line subtest titles
     (rerun of the optimizer and executor test files on each patched copy; see Alerts);
  2. full-suite ground truth for 40 fresh new-family mutants (seed 950: 11 setting-flip, 8 typeddict-total,
     8 dict-value, 7 comparison-swap, 6 fixture-entry);
  3. independent safety review of fixture replay (subagent).
- Next: `tools/ab_eval.py origin/main . --b-args='--fixture-replay-budget 8 --fixture-entries <repo>/fixture-entries.json.gz'`
  on the repaired holdout and on seed 950 (both sides `--import-time <built index>`); rerun agents 2 and 3 on the
  repaired mutants; post results below.

## Alerts
- to agent 1 (dataset, not a code miss): 243 of the 2,400 seed100 results (121 in the holdout split) lost failing
  tests to the old multi-line subtest parsing (`unknown` entries starting `SUBFAILED[`). Evaluators exclude them as
  suspicious, so misses in fixture-driven tests are under-counted for every branch. Example: m100011 also fails
  catalog 1123 and 1441, which the stored result omits. I am repairing the holdout ones locally (rerun of
  `tests/test_optimizer.py` and `tests/test_executor.py` with the fixed parser) and will publish them; a full rerun
  with the current `tools/ground_truth.py` is the clean fix.

## Branch status
WIP. All changes are behind config keys that default to off (`fixture_replay_budget: 0.0`).

## Requests
- to agent 1: download Actions run `44941967905` artifacts (`mutants-seed-100` … `mutants-seed-119`, and
  `mutant-evaluation` when the run finishes) and publish them where cloud agents can read them (for example
  a release asset or a branch with the merged `results/` and `commits/`); the artifact blob host is blocked in
  cloud sandboxes.
- to agent 1: add `fixture_replay.py fixture-entries.json.gz` to the `COPY ... /app/` line of the `Dockerfile`
  once fixture replay is measured (the selector only imports it when `fixture_replay_budget > 0`).
- to agent 3: fixture replay computes its own schedule saving with `selector.split` and charges actual child
  CPU; if you add a shared admission helper, tell me its name and I will switch to it.

## Datasets

## Evaluations
- Task 4, seed100 holdout (1,077 usable, 121 excluded as suspicious), batches of 30, local (4 cores, no container
  limits), each branch vs its merge base `0871e19` with its new behavior switched on:
  | B | A misses | B misses | new misses | simulated B-A | CPU B-A |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | `agent3/selector-cpu` @ 1bceb00 `--schedule-admission` | 8 | 8 | 0 | -2.368 | -10.4 s |
  | `agent2/map-data` @ 4788d00 `--dict-key-refinement` | 8 | 8 | 0 | +4.736 | +3.3 s |
  - agent 3: cheaper on m100029, worse on none. agent 2: two commits select one more test (m100019, m119049).
  - The 8 shared misses predate the downstream import check on `main` (m100091, m103013, m104049, m104111, m109103,
    m114069, m118031, m119049).
- Fixture replay, source edits in function bodies (TPC-DS executor test, one entry each; ground truth by running the
  test on the patched copy): neutral reorder, LIKE `%` to `.+`, inverted LIKE result, inverted ORDER BY key. Replay
  drops the TPC-DS executor test in all four and it really passes in all four (that test only checks entries marked
  `execute`). No miss; 2.4-2.7 s replay CPU for a 21.45 s test.
- Fixture replay acceptance (local, budget 8, working tree vs `main` @ 0871e19), two hand-made TPC-H fixture
  patches with full ground truth for the selected tests:
  - expected-half formatting edit (one real failure, the optimizer TPC-H test): `main` selects 2 tests
    (5.892 simulated); replay selects exactly the failure (2.355), 2.6 s replay CPU.
  - input-half interval edit (two real failures, the optimizer TPC-H test and the executor optimized TPC-H test):
    `main` selects 3 tests (7.892); replay selects exactly the two failures (5.537), 2.9 s replay CPU.
- Fresh fixture-entry mutant (seed 91000, entry 6 expected half, identifier edit; ground truth: the optimizer TPC-H
  test and the executor optimized TPC-H test fail): replay keeps both selected tests, 2.6 s CPU. No miss.
- `.` (working tree) vs `main` @ 0871e19 on the 30 sample patches, one batch, local (no container limits):
  both 0 misses, 116.075 simulated, about 2.0 s CPU per 30 commits (`tools/ab_eval.py`).

## Notes
- Ground truth now passes `--continue-on-collection-errors`, and multi-line subtest titles are parsed to the row
  naming the test. Results produced earlier may hide failures as `SUBFAILED[...` unknowns (flagged as suspicious,
  not miscounted); re-run those rather than trusting them.
- Mutant ids are `m{seed}{i}`; use a separate seed range per families launch to avoid id clashes.
- Fixture replay design: per supported test, recorded entry → executed lines of the pinned library,
  base lines (imports, class setup, empty run), entry CPU and a fingerprint of the global state an entry
  changes. Python edits must be confined to function bodies (stripped-AST equality outside them); affected
  entries are those executing any line of an edited body; any edited body executed by base refuses.
  Fixture edits must keep the entry count; affected entries are those whose parsed content differs.
  Each affected entry runs alone in a fork of the prepared process with the test's real assertions and
  inline process pools (pickle round-trips). Drop only if every affected entry passes and its state change
  equals the recorded one.
- Absolute state fingerprints depended on which modules were already imported and which class setups ran;
  switched to before/after deltas, preloading every lazily imported module and preparing every recorded class.

# Agent 3 progress

## RESUME HERE
- Branch: `agent3/selector-cpu` (from `main` 3345ab6).
- Done: Task 1 code (`schedule_admission`, default off) and Task 2 code
  (`exact_comparisons`, default off, synthetic-map tests). Both pushed.
- Next: Task 3 config matrix on the sample patches and local mutant batches
  (see Evaluations), then Task 4 profiling.
- Local setup for a fresh session (nothing here is committed):
  - pinned checkout: `git fetch --depth 1` of the pinned revision into a
    scratch repo, `git archive FETCH_HEAD | tar -x -C /opt/sqlglot`
    (the tarball host returns 403 in this sandbox; git over HTTPS works).
  - `python3 -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.lock coverage==7.10.7`
  - `tools/import_closure.py --output <work>/import-closure.json`,
    `tools/prepare_executor_plans.py --output <work>/executor-plans` (80 s).
  - mutants: `tools/mutants.py --source /opt/sqlglot --out <data>/bN --count 30 --seed N`
    for N in 201..210, plus `cp sample/tests.json <data>/bN/`.
- Half-done work: none. Running jobs: none.

## Alerts

## Branch status
WIP. Nothing to merge yet: both new behaviors are behind keys that default to
off, and admission-off output is byte-identical to `main` on the sample.

Changes so far:
- `schedule_admission` (Task 1). Narrowing probes are collected while a patch
  is processed and decided at the end: a probe runs only if dropping its tests
  from the patch's final selection (`selector.split`) saves at least
  1.5 x expected CPU + 0.5 s. Expected CPU starts from measured priors and
  is replaced by this run's own measurements per probe key. Applied to
  class-setup checks, the executor fixture check, the lazy-test check,
  executor plan replay (CPU capped at the break-even value) and both probe
  modes.
- Isolated copies (`isolated_pytest.stage`, also used by executor plan replay)
  keep build-time bytecode but delete every cached copy of the patched module.
  With precompiled bytecode the lazy-test check costs 0.86 s instead of 1.48 s
  CPU locally (see Requests).
- `exact_comparisons` (Task 2). Value-operator swaps (`==`, `!=`, `<`, `<=`,
  `>`, `>=`) narrow only with the per-site `exact` record proposed below.
- `selector-config.json` now has one key per line so each agent can add its
  own lines.

## Requests
- to agent 1: in `Dockerfile`, after the pinned checkout is fetched and
  before `USER`, add
  `RUN python -m compileall -q --invalidation-mode checked-hash /opt/sqlglot/sqlglot /opt/sqlglot/tests`.
  Why: the container writes no bytecode, so every isolated check recompiles
  the whole library. Measured locally: lazy-test check 1.48 s -> 0.86 s CPU;
  forked import/setup checks and plan replay benefit too. Safe with
  `isolated_pytest.stage`, which removes the patched module's cached copies
  (unit test `StagingTests`). Without it, `schedule_admission` rejects the
  lazy-test check (saving 2.37 s < 1.5 x 1.3 s + 0.5 s).
- to agent 1: please run the held-out batches for `schedule_admission` on/off
  in the image once the bytecode change is in (my sandbox cannot download the
  `mutant-evaluation` artifact: its storage host is blocked).
- to agent 2: exact comparison record, format proposal (see Notes). Reply in
  your Notes with the agreed format or changes; I will adapt the reader.

## Datasets
- local-201..210: `tools/mutants.py` seeds 201-210, 30 patches each (300).
  No full-suite ground truth (too slow here); misses are checked
  differentially: tests a variant drops that `main` keeps are run with pytest
  on the patched checkout.

## Evaluations
- sample, local CPU (not container CPU):
  - `main` 3345ab6: simulated 116.075, misses 0, CPU 1.86-2.08 s.
  - branch, `schedule_admission` off: identical output to `main`.
  - branch, `schedule_admission` on, no precompiled bytecode: simulated
    118.443 (+2.368: lazy-test check not admitted), misses 0, CPU 0.66 s.
- Probe inventory, local-201..210 on `main` settings: lazy-test check 9 calls,
  11.9 s CPU, all passed (each saves 2.368 s); fixture check 15 calls, 0.26 s;
  import check 1 call, 0.1 s; class-setup check 1 call, 2.75 s.

## Notes
- Proposed exact comparison record (map v1, extra key per comparison site):

      "comparisons": {"<line>:<col>": {"op": "Eq", "unknown": "<hex>", "states": {...},
          "exact": {"complete": "<hex>", "states": {"p:4": "<hex>", ...}}}}

  - Observe `Eq`, `NotEq`, `Lt`, `LtE`, `Gt`, `GtE` sites, not only ordering
    against a literal. Record an attempt when the observer is entered (both
    operands have then been evaluated) and before the original operator runs.
  - `exact.complete`: tests for which every attempt at this site completed the
    original operator without raising, with `type(left)` and `type(right)`
    each exactly `int`, `str` or `NoneType` (no subclasses, no `bool`).
  - `exact.states`: the existing relation encoding (`p:`/`e:` with bits
    1 equal, 2 less, 4 greater, 8 identical), from those tests' attempts only.
    A test with several outcomes appears under several states.
  - The selector drops a test only if it is in `complete`, in at least one
    exact state, not in `unknown`, and every one of its states gives the same
    outcome under both operators. Everything else touching the site is kept.
    Swaps to or from `is`/`is not` never use this record.
- Task 2 cost and correctness checks (the long optimizer comparison patch,
  and the two dialect patches whose 8 and 12 failures must all stay) wait for
  the map with exact records.
- `agent3/cpu-governance` was pushed by mistake before the brief arrived; the
  remote refused its deletion. Ignore it.

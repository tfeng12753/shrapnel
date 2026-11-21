# Agent 3 progress

## RESUME HERE
- Branch: `agent3/selector-cpu` (from `main` 3345ab6). Last pushed commit:
  see `git log origin/agent3/selector-cpu -1`.
- State: Tasks 1, 3 and 4 done as far as this sandbox allows; Task 2 code done
  and reviewed, waiting for the exact comparison record in the map.
- Next:
  1. When agent 2 publishes the exact record: rebuild or fetch the map, run
     the long optimizer comparison patch (`len(scopes) == 1` -> `< 1` in
     `sqlglot/optimizer/pushdown_projections.py`, target about 5.8 s) and the
     two dialect `==` patches whose 8 and 12 failures must all stay, with
     `--exact-comparisons`; then the mutant batches with the differential
     evaluator; then run a review subagent again.
  2. When agent 1 lands the bytecode Dockerfile line: confirm in the image
     that the lazy-test check is admitted with `schedule_admission` on.
- Local setup for a fresh session (nothing here is committed):
  - pinned checkout: `git fetch --depth 1` of the pinned revision into a
    scratch repo, `git archive FETCH_HEAD | tar -x -C /opt/sqlglot`
    (the tarball host returns 403 in this sandbox; git over HTTPS works).
    Container-like copy without bytecode: `cp -r /opt/sqlglot <work>/nopyc`
    then delete every `__pycache__`; bytecode copy: `cp -r <work>/nopyc
    <work>/pyc && python -m compileall -q --invalidation-mode checked-hash
    <work>/pyc/sqlglot <work>/pyc/tests`.
  - `python3 -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.lock coverage==7.10.7`
  - `tools/import_closure.py --output <work>/import-closure.json`,
    `tools/prepare_executor_plans.py --output <work>/executor-plans` (80 s).
  - mutants: `tools/mutants.py --source /opt/sqlglot --out <data>/bN --count 30 --seed N`
    for N in 201..210, plus `cp sample/tests.json <data>/bN/`.
  - evaluator (scratch, not committed): runs each variant per batch, then
    runs with pytest every test a variant drops that `main` keeps, on the
    patched checkout and on the pristine one. A test failing only when
    patched is a miss.
- Half-done work: none. Running jobs: none.

## Alerts
- `README.md` on `main` still names the pinned library and links its
  repository in the first paragraph (confidentiality rule). Not my file.

## Branch status
Ready for review. Every new behavior is behind a key that defaults to off, so
merging changes nothing until a key is flipped. Admission-off output is
identical to `main` on the sample and on 300 local mutants. Unit tests: 155
pass.

Recommended `main` config, with evidence in Evaluations:
- keep `lazy_test_budget` 3.0 (net +11.5 s per 330 patches vs 0),
- keep `executor_fixtures` on (net +136 s per 330 patches vs off),
- keep `executor_plan_budget` 0.0: a full replay costs about 15 s CPU locally,
  and with the current cap (half the saving) it never finishes, so it spent
  41 s per 330 patches and dropped nothing,
- set `schedule_admission` true **only together with** the bytecode Dockerfile
  line (Requests): net +7.3 s per 330 patches. Without bytecode, admission
  correctly rejects the lazy-test check and loses 11.4 s.
- `exact_comparisons` stays false until the map has the exact record and the
  checks in RESUME HERE pass.

Changes:
- `schedule_admission` (Task 1). Narrowing probes are collected while a patch
  is processed and decided at the end: a probe runs only if dropping its tests
  from the patch's final selection (`selector.split`) saves at least
  1.5 x expected CPU + 0.5 s. Expected CPU starts from measured priors (plan
  replay 15 s; lazy-test check 1.45 s, or 0.9 s when the checkout has bytecode
  for this interpreter) and is replaced by the run's own measurements per
  probe key. Plan replays record CPU only when they complete. Applied to
  class-setup checks, the executor fixture check, the lazy-test check,
  executor plan replay (CPU capped at the break-even value) and both probe
  modes. A review subagent found no way to drop a failing test, with
  admission on or off.
- Isolated copies (`isolated_pytest.stage`, also used by executor plan replay)
  keep build-time bytecode but delete every cached copy of the patched module.
- Operator-only edits skip the short-circuit walk (it cannot yield spans
  there): identical selections on 330 patches, select() CPU down about 10%.
- `exact_comparisons` (Task 2). Value-operator swaps (`==`, `!=`, `<`, `<=`,
  `>`, `>=`) narrow only with the per-site `exact` record proposed below, and
  only for tests that run before the first unproven test touching the site
  (catalog numbers equal pytest collection order: checked, 1,620 of 1,620).
- `selector-config.json` has one key per line, and `tools/variants.py` keeps
  that layout when it writes the file.

## Requests
- to agent 1: in `Dockerfile`, after the pinned checkout is fetched and
  before `USER`, add
  `RUN python -m compileall -q --invalidation-mode checked-hash /opt/sqlglot/sqlglot /opt/sqlglot/tests`.
  Why: the container writes no bytecode, so every isolated check recompiles
  the whole library. Measured locally: lazy-test check 1.48 s -> 0.86 s CPU,
  whole run CPU 23.4 -> 19.1 s per 330 patches with no other change. Safe
  with `isolated_pytest.stage` (unit test `StagingTests`; reviewed).
- to agent 1: `import_hazard_kind` parses the old and new sources again
  although `select()` has both trees. About 0.6 s of 2.8 s select CPU per 120
  patches is that function (parse plus walk). Accepting the trees, or caching
  per source, would cut it.
- to agent 1: please run the held-out batches in the image for
  `schedule_admission` on/off once the bytecode line is in (my sandbox cannot
  download the `mutant-evaluation` artifact: its storage host is blocked).
- to agent 2: exact comparison record, format and builder rules in Notes.
  Reply in your Notes with the agreed format or changes; I will adapt the
  reader.

## Datasets
- local-201..210: `tools/mutants.py` seeds 201-210, 30 patches each (300).
  No full-suite ground truth (too slow here); misses are checked
  differentially against `main` (see RESUME HERE). Plus the 30 sample
  patches, which have answers.

## Evaluations
Branch @ 1c7b8d1 vs `main` settings (cc9b640 for the rows without admission;
selections identical for those settings), 11 batches of 30 (local-201..210 + sample), local CPU (not
container CPU), container-like checkout without bytecode unless noted.
Differential misses vs `main` settings: 0 for every variant.

| variant | simulated | CPU | net |
| --- | ---: | ---: | ---: |
| `main` settings | 1539.2 | 23.4 | 1562.6 |
| `lazy_test_budget` 0 | 1562.9 | 11.2 | 1574.1 |
| `executor_fixtures` off | 1630.1 | 68.5 | 1698.5 |
| `executor_plan_budget` 20 (old cap) | 1539.2 | 64.7 | 1603.9 |
| `schedule_admission` | 1562.9 | 11.1 | 1574.0 |
| `schedule_admission` + plan 20 | 1562.9 | 11.4 | 1574.3 |
| `main` settings + bytecode | 1539.2 | 19.1 | 1558.3 |
| `schedule_admission` + bytecode | 1539.2 | 16.1 | 1555.3 |
| `schedule_admission` + plan 20 + bytecode | 1539.2 | 17.1 | 1556.3 |

- sample alone: `main` 116.075 s, 2.08 s CPU; admission + bytecode 116.075 s,
  1.48 s CPU.
- Probe inventory on `main` settings, local-201..210: lazy-test check 9 calls,
  all passed, 1.2-1.5 s each, each saves 2.368 s; fixture check 15 calls,
  0.26 s total; import check 1 call, 0.1 s; class-setup check 1 call, 2.75 s.
- Plan replay: works (unpatched and the long optimizer comparison patch both
  match on both seeds), but costs 7.4 s per seed plus the screen. A 21.5 s
  saving cannot pay for it under the admission rule.

## Notes
- Task 4 (fixed CPU), measured locally per run of 30 patches: map load 0.235 s
  (gunzip 0.06, JSON 0.07, mask decoding about 0.1); select() AST work about
  0.7 s, of which parsing about 0.18, `import_hazard_kind` about 0.16 (agent 1,
  Request), whole-file compare in `classify_python_edit` about 0.12,
  `short_circuit_edits` about 0.15 (now skipped for operator-only edits),
  `comparison_contexts` about 0.1. Lazy per-file mask decoding would save under
  0.1 s per run; not done because it changes shared map-loading code for
  little gain. Isolated checks dominate: bytecode is the big win (Requests).
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
  - Builder rules added after review:
    - no `exact` record for a site with any attempt outside a test (import
      time), as for the existing record;
    - an attempt inside the inline process pool (task or initializer), in
      shared setup or teardown (`setUpClass`, class/module/session fixtures)
      or during an import removes that test from `complete`;
    - build under at least two hash seeds: `complete` is the intersection,
      `states`, `unknown` and coverage are the union, and a test whose state
      set differs between seeds is not complete.
  - The selector drops a test only if it is in `complete`, in at least one
    exact state, not in `unknown`, every one of its states gives the same
    outcome under both operators, and it runs before every other test that
    touches the site without that proof. Swaps to or from `is`/`is not` never
    use this record.
- Review notes (no action needed now): child processes started through
  `os.system`, `os.exec*`, `os.spawn*` or `os.forkpty` are not marked
  external by the map's audit hook; the pinned tests use none of them.
- `agent3/cpu-governance` was pushed by mistake before the brief arrived; the
  remote refused its deletion. Ignore it.

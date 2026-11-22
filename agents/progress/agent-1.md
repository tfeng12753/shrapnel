# Agent 1 progress

## RESUME HERE
- `main` is `ac1b6b3`: agent 1's tasks 1 and 2 plus agent 3's branch (merged, its new keys off).
- Running: `schedule_admission` off vs on (sample plus 300 mutants in batches of 30). Turn it on in `selector-config.json` if it saves time with 0 new misses.
- Next: merge agents 2 and 4 when they mark their branches ready (add `dict_keys.py` / `fixture_replay.py fixture-entries.json.gz` to the `Dockerfile` `COPY` line); rebuild the map after agent 2.
- Scratch scripts are local. Mutant data is on branch `data/mutants-seed100`.

## Alerts
- to agent 3: the lazy-test check is nondeterministic on `main`. Sample patch `2haz` scores 118.443 in some runs and 116.075 in others (seen 1 run in 4 on `main` before this merge). It looks like its CPU-time-based deadline depends on machine load. Please make it deterministic (for example, a wall-clock or call-count limit that doesn't depend on load) or drop the check for that case.

## Branch status
Task 2 merged into `main` at `b9049ed`: `typeddict_state_check` (on). A `total=` flip of each of the 5 TypedDict classes in the checkout fails 0 tests (full suite); selection on those 5 went from 184.085 to 4.736 simulated seconds, 0 misses. Sample unchanged (116.075, 0 misses).

Integration: agent 3's `agent3/selector-cpu` merged into `main` at `ac1b6b3`. Conflicts were additive. Container sample: 116.075, 0 misses, CPU 1.83-1.87 s.

Task 1 merged into `main` at `06cf910`. Measured in the container on the 30 sample patches, 3 runs: 116.075 simulated, 0 misses, CPU 2.63–2.71 s, default command output identical.

Evidence:
- Base-dialect setting flip (`DPIPE_IS_STRING_CONCAT` True to False): 1,020 failing tests. `main` before this change selected 1,283 (203 missed); now 1,486, 0 missed.
- Held-out mutants that the relations config missed (12 of 2,155 usable, seed 100): 9 are now fully selected. The 3 left are runtime argument-requirement flips (`arg_types` False to True: m107102, m109103, m118031), which belong to agent 2's argument tracking.
- 300 held-out mutants in batches of 30, before precompiled bytecode: identical simulated time and misses; CPU +3.0 s total (about +0.3 s per 30-patch run).

## Requests
- DONE (to agent 3): bytecode precompile line in `Dockerfile`; `import_hazard_kind` reuses the selector's parsed trees.
- DONE (to agent 4): mutant data published on branch `data/mutants-seed100` (`git fetch origin data/mutants-seed100`; `seed100/commits`, `seed100/results`, `seed100/manifest.json`).
- PENDING (agent 2, agent 4): add `dict_keys.py` / `fixture_replay.py fixture-entries.json.gz` to the `Dockerfile` `COPY` line when those branches merge.
- to agent 2: three held-out mutants flip an `arg_types` entry from optional to required (m107102 in `expressions/functions.py`, m109103 in `expressions/constraints.py`, m118031 in `expressions/functions.py`) and fail 8, 23 and 7 tests at runtime; we miss 3, 19 and 1 of them. Tracking which tests construct each expression **without** that argument would select them. Data: branch `data/mutants-seed100`.
- to agents 2 and 4: rebase on `main` (`ac1b6b3`) before marking ready. `selector.select()` is now a wrapper around `_select_base()`; put selection changes in `_select_base()`.

## Datasets
- seed100-full: branch `data/mutants-seed100` (2,400 mutants, subtest-aware ground truth).

## Evaluations

## Notes
- Import-time owners: library or test modules whose own import runs library functions. A patch triggers the check when a changed line ran during that import, or when a changed setting or class attribute is referenced by code the import ran and that code can reach it (the file's code ran, or the edited class was used). On the sample, no patch triggers it.
- Class setups: `setUpClass` of each test class is traced the same way; a broken class setup errors every test in the class.

# Agent 4 progress

## RESUME HERE
- Branch: `agent4/fixtures-eval` (from `main` 0871e19), mirrored to `claude/agent-four-workflow-1vada3`.
- Pinned checkout in the sandbox: plain `git clone` of the upstream repository, then
  `git checkout eb11aec4be8cb2122afe713a4816662c3f0fb2fa` (the source tarball host is blocked here).
  Venv: `python3 -m venv venv && venv/bin/pip install -r requirements.lock coverage==7.10.7`.
- Done: `tools/ab_eval.py` (Task 2), `fixture_replay.py` + `tools/fixture_entries.py` + call site (Task 1, WIP).
- In progress: re-recording `fixture-entries.json.gz` with delta state fingerprints, then acceptance runs.
- Next command: `venv/bin/python tools/fixture_entries.py --source <pinned> --out fixture-entries.json.gz`
  (about 7 minutes, 4 cores), then
  `venv/bin/python selector.py --data <dir> --source <pinned> --map maps/dependencies.json.gz --fixture-replay-budget 8 --fixture-entries fixture-entries.json.gz`.

## Alerts

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
- `.` (working tree) vs `main` @ 0871e19 on the 30 sample patches, one batch, local (no container limits):
  both 0 misses, 116.075 simulated, about 2.0 s CPU per 30 commits (`tools/ab_eval.py`).

## Notes
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

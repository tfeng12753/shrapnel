# Agent 3 progress

## RESUME HERE
- Branch: `agent3/cpu-governance` (from `main` 3345ab6).
- Step: blocked before Task 1. The agent 3 task list is not in the repository
  (only `agents/coordination.md` and `AGENTS.md` are), so no selector work has
  started.
- Next: once the agent 3 task list is available, start Task 1 from it and
  replace this section.
- Half-done work: none. Logs: none.

## Alerts

## Branch status
WIP. Only this progress file has been added; no selector changes.

## Requests

## Datasets
- Using the shared seed100-full set from `agents/coordination.md` when
  evaluations start.

## Evaluations

## Notes
- Existing CPU budgets in `selector-config.json` (on `main` 3345ab6):
  `probe_budget` 0.0, `import_check_budget` 6.0, `setup_check_budget` 8.0,
  `executor_plan_budget` 0.0, `lazy_test_budget` 3.0.
- Where CPU is charged in `selector.py`: lazy-import checks (around line 705),
  executor plan replay (around line 725), setUpClass checks (around line 769),
  import checks (around line 784) and probe batches (around lines 831-868,
  cost-aware path with `strict_cpu`). Child CPU is measured in
  `native_probes.py` (`verify`, `verify_import`, `verify_setup`).

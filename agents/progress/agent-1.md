# Agent 1 progress

## RESUME HERE
- Branch `agent1/import-safety`. WIP. **Not ready to merge.**
- Done (Task 1):
  - Build-time index `tools/import_time.py` (Dockerfile step, `/app/import-time.json`) traces library lines and names used by each module's import, each test file's import closure, and each test class's `setUpClass`.
  - `import_effects.py` plus `Selector.select()` wrapper (`downstream_imports`): forked import check (`native_probes.verify_imports`) and setup check (`verify_setup`); budget key `downstream_import_budget` (6 s).
  - Any error in the check falls back to the previous behavior.
- Results:
  - Base-dialect setting flip: 1020 failing, all selected (was 203 missed).
  - Of the 12 mutants the relations config misses (seed-100 run), 9 are now fully caught and m119049 partly (201 to 62 missed).
  - Still missed: m114069 (class attribute `arg_types` in a file whose code never runs during the import); m107102, m109103 and m118031 (runtime `arg_types` flips, not collection: belongs to agent 2's argument tracking).
- **Open problems to fix next:**
  1. Sample CPU rose to about 4.2 s (main about 3.3 s), and one of 3 sample runs scored 118.443 instead of 116.075 (nondeterminism, probably the lazy-test check's time limit after a class-setup check). The trigger report script found no sample triggers in its last run; check whether the setup check runs at all on the sample (`a1_triggers.sh`, image `shrapnel-a1`), and profile.
  2. m114069: match class-level names when an instance of the edited class is created during the import (record `type(self)` in the trace).
- Commands: scratch scripts are local only (`a1_validate.sh`, `a1_misses.sh`, `a1_triggers.sh`). Data is in `~/work/agent1/`; seed-100 ground truth is in the Actions run 44941967905 artifact `mutant-evaluation`.

## Alerts

## Branch status
WIP.

## Requests

## Datasets

## Evaluations

## Notes

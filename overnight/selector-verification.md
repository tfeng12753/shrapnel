# Selector and verification track

The branch starts at `f08f491` on `main`. Submit changes only through `overnight/selector-verification` and a draft PR. Preserve the shipped configuration, leave map generation to the other track, and use even-numbered mutants only.

- The pinned sqlglot source (`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`), generated import closure, and Python 3.13.5 environment with all 43 locked packages are in `/workspace/selector-bench` in this workspace.
- The [ground-truth run](https://github.com/tfeng12753/shrapnel/actions/runs/44888276378) is in progress. No artifact was available at kickoff.
- Next: implement whole-selection verification with deterministic budget admission and conservative timeout handling, inspect the map for boolean/literal evidence, harden relations, and run five public replays per configuration. Record exact configurations, map fingerprint, code revision, schedule costs, misses and CPU here as measurements finish.

## Whole-selection verification

The probe estimates CPU as 0.22 s plus 1.25 times catalog duration. It requires a zero-worker saving above three times that estimate and reserves a deadline of 0.5 s plus twice catalog duration, capped at 1.5 s per batch. The global budget charges that fixed reservation whether the probe passes, fails or times out. Admission is based on the normal sorted patch order, catalog timings and available budget. Only a complete batch of actual native unittest successes removes a selection. Skips, failures, unknown tests, unsupported overlays, and partial results retain it.

Enable with `--probe-budget 8 --cost-aware-probes` or `"probe_budget": {"seconds": 8.0, "cost_aware": true}`. The optional `cost_aware` child key defaults to false. The existing root key stays in `tools/competitive.py:BASE` with a numeric value. The config tests require BASE to match the checked-in JSON's exact root keys, so the nested form preserves both the benchmark schema and the shipped file. Numeric command-line budgets select the existing policy unless the new flag is also present.

82 unit tests pass. Public replay and official tune results are pending. For a fallback independent tune sample, generate 32 mutants at seed 42 and run only the 16 even IDs plus the unmodified baseline through `tools/ground_truth.py`. This is running in `/workspace/selector-bench/local-tune`. The GitHub run has 20 shards in progress. The next checkpoint will record what the current map can prove about boolean and literal edits.

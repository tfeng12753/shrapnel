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

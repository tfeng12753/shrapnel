# Validation

All numbers are simulated seconds on the 30 sample patches under the container
limits from the `Makefile` (3 runs each, `linux/amd64`, Python 3.13), unless
stated otherwise.

| Configuration | Simulated s | Misses |
| --- | ---: | ---: |
| Full suite on every patch | 1104.510 | 0 |
| Conservative Python selection | 651.870 | 0 |
| Targeted selection with the child-process guard (earlier map) | 470.062 | 0 |
| Targeted selection with the inline-pool map | 166.316 | 0 |
| + comparison relations | 118.443 | 0 |
| + TPC executor fixture check and lazy-test check (current `main`) | 116.075 | 0 |

## Map

The map is built from the unmodified pinned checkout; all 1,621 tests pass
(25,440 subtests) during map generation. Process-pool work runs inline with
pickle round-trips, so worker-process code (including `sqlglot.serde`) is
attributed to the calling test. Its fingerprint is recorded in
`maps/dependencies.meta.json` and checked at build time.

## Held-out mutants

`tools/mutants.py` generates seeded single-token mutants of the pinned code
(operators, booleans, numbers, fixture corruptions, comment-only edits and
multi-file combinations), avoiding lines touched by the sample patches.
`tools/ground_truth.py` runs the full real suite on each inside the image and
records failing tests; pytest 9 `SUBFAILED` lines count against their parent
test. `tools/mutant_eval.py` scores configurations against them with a
tune/holdout split (tune on even ids, decide on odd ids).

Rules: mutants and their results are evaluation data only. They never become
selector input, and no rule may be keyed to specific patches, files or test
outcomes.

## Known limits

- State shared between tests, reflection, and native code that reads files
  directly can create dependencies the map doesn't observe; the selector falls
  back conservatively where it can detect this.
- Class-setup attribution relies on a forked `setUpClass` check; if it can't
  run (budget, timeout), the whole class stays selected.

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
| + TPC executor fixture check and lazy-test check | 116.075 | 0 |
| + creation-hook attribution, lambda bodies, docstring prose, entry point | 115.843 | 0 |

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

### Creation hooks, lambda bodies, docstrings and entry points

Seed-100 held-out mutants (2,147 with usable ground truth), selector run
locally on the pinned checkout with a map built by the same builder on the
same machine, budgets as shipped. Per 30 includes 300 s per miss.

| Split | Configuration | Misses | Simulated s | Per 30 |
| --- | --- | ---: | ---: | ---: |
| tune (even) | previous map and settings | 1 | 4459.5 | 132.82 |
| tune (even) | new map | 0 | 4464.0 | 124.58 |
| tune (even) | new map + `lambda_bodies`, `docstring_edits`, `entry_point_edits` | 0 | 4301.8 | 120.05 |
| holdout (odd) | previous map and settings | 2 | 4368.9 | 139.06 |
| holdout (odd) | new map | 0 | 4368.8 | 122.26 |
| holdout (odd) | new map + `lambda_bodies`, `docstring_edits`, `entry_point_edits` | 0 | 4218.4 | 118.05 |

The three previous misses all flipped an expression class's argument from
optional to required; the derived required-argument set was read through an
attribute the class got at creation, which the earlier map did not follow.
Selector CPU on the 30 sample patches is unchanged (about 1.2 s locally).

## Known limits

- State shared between tests, reflection, and native code that reads files
  directly can create dependencies the map doesn't observe; the selector falls
  back conservatively where it can detect this.
- Class-setup attribution relies on a forked `setUpClass` check; if it can't
  run (budget, timeout), the whole class stays selected.

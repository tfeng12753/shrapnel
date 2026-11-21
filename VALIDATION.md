# Local validation

On macOS arm64, Python 3.13.15, the locked packages and coverage 7.10.7 ran the
unchanged pinned SQLGlot baseline. All 1,621 tests passed, including 25,454
subtests. The map builder produced a map of 314 files. The challenge catalog
contains 1,620 numbered tests. The selector translated the map by test ID.

The official local scorer evaluated the 30 public patches. Three fresh runs
using the conservative Python policy each cost 651.870 simulated seconds, emitted
119,984 bytes and missed zero commits. User and system CPU brought the median
to 652.5 seconds. The historical no-map run in `validation.json` cost
1,104.510 simulated seconds. Docker limits, cgroup CPU, and the hidden batch
still require separate measurement.

Before the short-circuit fix, the targeted Python strategy scheduled 139.455 seconds of tests, but five
missed patches added 1,500 seconds. Coverage alone, comparison refinement,
and refinement with probes all missed the same five. Every omitted test was
in the executor or optimizer test module. Both use process pools, and the
local map flagged only one catalog test as external. Worker-process line attribution remains the main gap in that strategy.

| Missed patch | Missing test numbers in the sample catalog |
| --- | --- |
| 2jrq | 1106, 1325, 1355, 1440, 1441 |
| 3utz | 1324, 1325, 1327, 1353, 1355, 1362, 1375, 1377, 1417, 1420, 1440, 1441, 1454 |
| 535z | 1434 |
| ary6 | 1355, 1417, 1420 |
| rszg | 1355 |

The next default retained tests that the then-current map placed on possible
process launch or pool-dispatch sites (26 on that map). All three public runs cost
470.062 simulated seconds with zero misses; the median including CPU was 471.2
seconds. That guard was a workaround for the map's blind spot, not a selection
rule worth keeping. See "Container-verified result" below for its replacement.

Three additional patches were applied separately to fresh copies of the pinned
source. All 1,621 tests were run on each copy to obtain the failing test IDs;
subtest failures were attributed to the owning numbered test. The patches changed
derived-table elimination, SQLite generated-column storage, and the expected SQL
in a merge-subqueries fixture. They caused five, one, and one distinct test
failures respectively. The default selected all seven. Targeted selection
without the child-process guard missed four tests on the optimizer patch.

The selector passed 56 project tests. The mixed-block regression checks that
adding a branch beside a replacement retains other users of the enclosing
function. All public patches applied to the pinned source identically before
and after the parser change.

Run `make competitive` on a Docker host to rebuild and compare all five
configurations under the challenge limits. For a local run with a complete map:

```sh
python3 tools/benchmark.py --source /path/to/pinned/sqlglot --map /path/to/dependencies.json.gz
```

## Container-verified result

The cached dependency map (`maps/dependencies.json.gz`) was built with process-pool
work run inline in the calling test, plus pickle round-trips of submissions and
results. That attributes worker-process line execution to the test that
requested it, which was the gap the child-process guard compensated for. The
guard is no longer needed.

With `{"conservative_python":false,"child_work":false,"relations":false,"probe_budget":0.0,"import_check_budget":6.0}`,
the scorer's container flags, and three runs on the 30 public patches:

| Configuration | Simulated seconds | Missed commits |
| --- | ---: | ---: |
| Full suite | 1104.510 | 0 |
| Conservative Python | 651.870 | 0 |
| Child-work guard | 470.062 | 0 |
| Targeted, inline-pool map | 166.316 | 0 |

Selector CPU was about 1.4 s per run. Two further configurations were measured
locally on the public patches only (not in the container): comparison relations,
115.907 s with zero misses, and relations with probe budget 8, 113.877 s. Neither
is the default. The default is chosen by the held-out mutant evaluation below.

The public patches were used while building the selector, so a zero-miss score on
them is not evidence of zero misses on other patches. The mutant evaluation exists
to measure that.

## Held-out mutant evaluation

Evaluation only. None of these tools is copied into the image, and none of their
output is selector input.

- `tools/mutants.py` generates seeded mutants of the public patch kinds:
  operator, boolean and number edits, fixture corruptions, comment-only edits,
  and multi-file patches. It avoids every line the public patches touch. The plan
  is 40 fresh mutants.
- `tools/ground_truth.py` runs inside the image. It applies one mutant with the
  selector's patch reader and runs the full real suite with real process pools,
  recording the failing catalog tests (subtest failures count against the owning
  test).
- `tools/mutant_eval.py` scores selector variants against those
  recorded results, with a tune/holdout split.

Anti-overfitting rule: tune on one split and report only the other. Mutants and
their results never become selector input, lookup tables, or map content. A
selector change made after looking at a holdout failure turns that split into a
tuning split, and the result is reported on mutants generated afterwards.

| Item | Value |
| --- | --- |
| Final configuration | `{"conservative_python":false,"child_work":false,"relations":false,"probe_budget":0.0,"import_check_budget":6.0}` (unchanged; relations stays off until held-out evaluation) |
| Public sample score and misses | 166.316 s simulated, 0 misses, median total 168.0 s with CPU (container, 3 runs, commit 4c6b976, CPU 1.86/1.65/1.64 s) |
| Held-out mutant misses and score | Not yet run (scheduled after this submission) |
| Project commit | The commit that adds this table (see `git log`) |
| `builder_sha256` | `cc729b2e5ee84c1fb3fb7d656fbc8f03c9f6a362a6f6801d6407e9a9d48ad7d3` (verified by `tools/map_fingerprint.py --check`) |
| `map_sha256` | `02198f60b3809b26d3f5f3fcfd55e9882880b1483c38e6d94d6c56021418b372` (verified by `tools/map_fingerprint.py --check`) |

### Evidence convention

A published result names the exact `selector-config.json` contents, the map
fingerprint (`builder_sha256` and `map_sha256` in `maps/dependencies.meta.json`),
and the project commit. CI (`.github/workflows/checks.yml`) runs the unit tests
and `tools/map_fingerprint.py --check` on every pull request and push to main, so
a builder change without a regenerated map fails before it reaches a build.

## Earlier experiment: inline worker map

The separate map-builder change in PR #3 was tested at commit
`3c22982d170f44bc523cda0bac6df9200ff8d97d` on the same pinned macOS checkout.
All 1,621 baseline tests passed. Ordinary targeted selection with comparisons
initially missed two public patches. For the mixed patch `rszg`, replaying its
three file edits separately showed that only the Snowflake boolean edit made
`TestOptimizer.test_optimize` fail. That test reached the first operand at
baseline line 99, then short-circuited before the changed operator at line 101.

Selecting users of the regrouped boolean expression recovered this test.
The preview map with ordinary targeted selection then cost 143.067 scheduled
seconds plus one 300-second penalty, for `3utz`. The guarded default scored
470.062 simulated seconds with zero misses using either map.

On `3utz`, `TestOptimizer.test_quote_identifiers` failed while deserializing a
result from a real process pool. It passed with the inline pool. Adding an
isolated serialize/deserialize step for the returned result made the patched
test fail again, while the same experiment passed on the unchanged source.
That experiment led to the pickle round-trips in the current map builder; the
container-verified result above uses them.

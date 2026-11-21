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

The targeted Python strategy scheduled 139.455 seconds of tests, but five
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

The new default retains tests that the existing map places on possible process
launch or pool-dispatch sites. It retained 26 such tests on this map. All three
public runs cost 470.062 simulated seconds with zero misses. The median including
CPU was 471.2 seconds. Dynamic dispatch
through native code or wrappers outside the pinned source can still escape the
scan. `--conservative-python` remains available when the map or workload changes.

Three additional patches were applied separately to fresh copies of the pinned
source. All 1,621 tests were run on each copy to obtain the failing test IDs;
subtest failures were attributed to the owning numbered test. The patches changed
derived-table elimination, SQLite generated-column storage, and the expected SQL
in a merge-subqueries fixture. They caused five, one, and one distinct test
failures respectively. The default selected all seven. Targeted selection
without the child-process guard missed four tests on the optimizer patch.

The selector passed 52 project tests. The mixed-block regression checks that
adding a branch beside a replacement retains other users of the enclosing
function. All public patches applied to the pinned source identically before
and after the parser change.

Run `make competitive` on a Docker host to rebuild and compare all five
strategies under the challenge limits. For a local run with a complete map:

```sh
python3 tools/benchmark.py --source /path/to/pinned/sqlglot --map /path/to/dependencies.json.gz
```

# Local validation

On macOS arm64, Python 3.13.15, the locked packages and coverage 7.10.7 ran the
unchanged pinned SQLGlot baseline. All 1,621 tests passed, including 25,454
subtests. The map builder produced a map of 314 files. The challenge catalog
contains 1,620 numbered tests. The selector translated the map by test ID.

The official local scorer evaluated the 30 public patches. Three fresh runs
using the committed default each cost 651.870 simulated seconds, emitted
119,984 bytes and missed zero commits. User and system CPU brought the median
to 652.5 seconds. The historical no-map run in `validation.json` cost
1,104.510 simulated seconds. Docker limits, cgroup CPU, and the hidden batch
still require separate measurement.

The targeted Python strategy scheduled 139.455 seconds of tests, but five
missed patches added 1,500 seconds. Coverage alone, comparison refinement,
and refinement with probes all missed the same five. Every omitted test was
in the executor or optimizer test module. Both use process pools, and the
local map flagged only one catalog test as external. Check worker-process
coverage and its attribution to the parent test before enabling targeted
Python selection by default.

| Missed patch | Missing test numbers in the sample catalog |
| --- | --- |
| 2jrq | 1106, 1325, 1355, 1440, 1441 |
| 3utz | 1324, 1325, 1327, 1353, 1355, 1362, 1375, 1377, 1417, 1420, 1440, 1441, 1454 |
| 535z | 1434 |
| ary6 | 1355, 1417, 1420 |
| rszg | 1355 |

The selector passed 47 project tests. The mixed-block regression checks that
adding a branch beside a replacement retains other users of the enclosing
function. All public patches applied to the pinned source identically before
and after the parser change.

Run `make competitive` on a Docker host to rebuild and compare all four
strategies under the challenge limits. For a local run with a complete map:

```sh
python3 tools/benchmark.py --source /path/to/pinned/sqlglot --map /path/to/dependencies.json.gz
```

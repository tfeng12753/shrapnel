# Blast Radius selector

A submission project for the sqlglot test-selection challenge. The target is below 100 seconds. The
default selector scored **471.2 seconds with zero missed commits** on the 30
public patches in a local macOS arm64 run. The scorer includes the simulated
schedules and selector CPU. The Linux container score remains unmeasured.

| Mode | Simulated seconds including penalties | Missed commits |
| --- | ---: | ---: |
| Full suite on every patch | 1104.510 | 0 |
| Conservative Python selection with the local baseline map | 651.870 | 0 |
| Targeted selection with child-process callers retained (default) | 470.062 | 0 |
| Targeted Python selection before the short-circuit fix, with comparison refinement and probes | 1639.455 | 5 |

For executable Python changes, the default keeps tests selected by the map
and tests observed on lines that can dispatch work to a child process.
The parent can reach a pool submission without recording which lines its child
executes. Keeping those callers recovered the five misses seen with ordinary
targeted selection on the public samples. Use `--conservative-python` to run
the full suite for executable Python changes. See [VALIDATION.md](VALIDATION.md)
for measurements and limitations.

## Run

Docker must support `linux/amd64`. From this directory:

```sh
make unit                 # standard library only
make sample               # build dependency map, run with official limits, score
make check                # validate output format under the same Docker limits
```

The first dependency-map build installs the supplied locked test environment,
downloads only sqlglot at commit
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, and runs its unchanged tests. This may
take several minutes. Build failure or the 10-minute budget must be resolved
before submitting this mode; build time has not been measured here.

To validate just the scheduling-only backup:

```sh
make safe-sample
```

This builds the same top-level Dockerfile with `BUILD_MAP=0`. The competition
uses default build arguments. To submit the backup instead of the dependency
selector, replace the top-level Dockerfile and commit that change:

```sh
cp Dockerfile.safe Dockerfile
git add Dockerfile
git commit -m 'Use scheduling-only submission'
make sample
```

Local scoring without Docker is also available:

```sh
make local-benchmark
python3 tools/benchmark.py --source /absolute/pinned/sqlglot --map maps/dependencies.json
```

The local benchmark does not reproduce container isolation, the scorer's Python
version, or cgroup CPU accounting.

## How selection works

The image's build generates a map from the unchanged pinned source:

1. Coverage records executed source lines in a separate context for each test.
2. Targeted AST observers record instance, classmethod, module, and dynamic
   `getattr` reads of settings. This avoids wrapping every object attribute.
3. Audit events record fixture and explicit source reads, including cached
   `open` aliases. Ordinary import-loader reads are excluded. Class setup reads
   are shared with that class rather than the entire test suite.
4. Static references use the exact executed use line, rather than all callers
   of a containing function. Initializer copies and inheritance still propagate.
5. Comparison observers record baseline operand relations. A change confined to
   comparison operators can retain only boundary-reaching or unknown tests.
   Unsupported user-defined comparisons remain conservative.
6. Eligible plain unittest tests can be verified in isolated forked processes.
   Only confirmed passes are removed; subtest failures, import errors, class
   teardown errors, and timeouts retain selections. The batch has an 8-second
   child CPU budget, with a 1.5-second wall limit per probe.

At runtime the selector verifies each patch against the pinned files in memory.
It unions affected lines, constant users, function-header users, insertion
regions, and fixture readers. It checks all files in a patch. Python changes with
the same AST retain tests observed reading that source; SQL/fixture comments are
never assumed harmless. Unknown files, malformed patches, syntax errors,
unsupported import-time changes, and incomplete maps select the entire suite.

Maps use test IDs and are translated to the supplied test numbers. Optional
native verification runs small candidate sets while deciding; its CPU is charged
and measured. Tests with pytest fixtures or marks are excluded from native
verification, and large candidate sets are not probed. Each selected set is scheduled with longest-processing-time
first for all worker counts from 1 through 8; the selector chooses the smallest
`max(load) + 2 * worker_count` using integer milliseconds. For the entire sample
suite the optimal count is 4, because the slowest individual test is 28.817 s.

Run `make competitive` to compare conservative Python selection, coverage with and without child-process callers, comparison refinement, and
comparison refinement with verification, three times each. It selects the best
global configuration among zero-miss sample variants and exports
`research-bundle.zip` with the pinned source, maps, outputs, and CPU measurements.
The project has 56 passing local checks, including real forked native probes.

Baseline coverage is an approximation of dependencies. Reflection, state shared
between tests, indirect class-level access, and behavior in uninstrumented child
processes can require dependencies the map does not observe. Static propagation
and fallbacks reduce this risk; they are not a proof against missed failures.
Full sample measurement is required before judging the tradeoff.

## Cache a map for faster rebuilds

After a successful `make sample`:

```sh
make export-map
git add maps/dependencies.json
git commit -m 'Cache dependencies from pinned baseline'
make sample
```

The export contains baseline observations and symbols only. It is a plain JSON
file, has a revision check, and is limited to 18 MB to leave room under the
20 MB repository allowance. Larger maps can stay generated at build time.

## Competitive measurement

```sh
make competitive
```

This requires Docker and network access during build. It enforces a 600-second
build deadline, applies the scoring container limits to every run, measures CPU,
and compares five globally configured strategies. It writes
`selector-config.json` for the best zero-miss sample variant, caches the plain
baseline map when it fits the repository allowance, and exports
`research-bundle.zip`. Commit the chosen config/map and rebuild before submission.
The timing wrapper adds a small amount of CPU, so the official score may differ.

If Docker is inconvenient locally, the repository includes a manual GitHub
Actions workflow, **Benchmark pinned sqlglot**. Run it after publishing the
repository and download its `blast-radius-research` artifact.

See `COMPETITIVE.md` for the evidence needed to continue tuning without Docker.

## Publish

The download includes `submission.bundle`, a Git bundle preserving the local
commit and its source history. Clone it to create a working repository:

```sh
git clone submission.bundle blast-radius-submission
cd blast-radius-submission
make sample
gh auth login
gh repo create blast-radius-submission --public --source . --push
git rev-parse HEAD
```

Enter the resulting public GitHub repository URL and the commit from the last
command on the competition page. Keep the repository public and preserve every
submitted commit until results are published. Nothing has been published or
submitted from the authoring environment.

`sample/answers.json` is used only by local validation tooling. The sample
folder, scorer, and validation results are excluded from the Docker build.

# Blast Radius selector

A submission project for the sqlglot test-selection challenge. The target is below 100 seconds.
The current configuration scored **166.316 simulated seconds with zero missed
commits** on the 30 public patches, measured in the scoring container
configuration (`linux/amd64`, the scorer's `docker run` flags), three runs, with
about 1.4 s of selector CPU per run. The scorer includes the simulated
schedules and selector CPU.

| Mode | Simulated seconds including penalties | Missed commits |
| --- | ---: | ---: |
| Full suite on every patch | 1104.510 | 0 |
| Conservative Python selection | 651.870 | 0 |
| Targeted selection with the child-process guard (earlier map) | 470.062 | 0 |
| Targeted selection with the inline-pool map (current config) | 166.316 | 0 |
| Targeted selection plus comparison relations (local, not default) | 115.907 | 0 |
| Relations plus probe budget 8 (local, not default) | 113.877 | 0 |

The cached dependency map attributes process-pool work to the calling test
(pool work runs inline) and round-trips submissions and results through pickle.
That captured the dependencies the child-process guard had been retaining
tests for, so the guard (`child_work`) is off. The first four rows were
container-verified; the last two were measured locally on the public patches and
have not been run in the container. They are not the default.

The default configuration is chosen by a held-out mutant evaluation, described in
[VALIDATION.md](VALIDATION.md). Final result, to be filled in:

| Item | Value |
| --- | --- |
| Final configuration | `{"conservative_python":false,"child_work":false,"relations":false,"probe_budget":0.0,"import_check_budget":6.0}` (unchanged; relations stays off until held-out evaluation) |
| Public sample score | OFFICIAL_RUN (container, 3 runs, 0 misses) |
| Held-out mutant misses and score | Not yet run (scheduled after this submission) |
| Project commit | The commit that adds this table (see `git log`) |
| Map fingerprint | `builder_sha256` `cc729b2e5ee84c1fb3fb7d656fbc8f03c9f6a362a6f6801d6407e9a9d48ad7d3`, `map_sha256` `02198f60b3809b26d3f5f3fcfd55e9882880b1483c38e6d94d6c56021418b372` (verified by `tools/map_fingerprint.py --check`) |

Use `--conservative-python` to run the full suite for executable Python changes.
See [VALIDATION.md](VALIDATION.md) for measurements and limitations.

### Evidence convention

Every published result names the exact `selector-config.json` contents, the map
fingerprint (`builder_sha256` and `map_sha256` from `maps/dependencies.meta.json`),
and the project commit. A number without those three items cannot be reproduced
and is not reported as a result. The config currently in the working tree is
`{"conservative_python":false,"child_work":false,"relations":false,"probe_budget":0.0,"import_check_budget":6.0}`.

## Run

Docker must support `linux/amd64`. From this directory:

```sh
make unit                 # standard library only
make build                # docker build (uses the cached map)
make sample               # build, run with official limits, score
make check                # validate output format under the same Docker limits
make safe-sample          # scheduling-only backup (BUILD_MAP=0)
make local-benchmark      # no Docker; full-suite fallback without a map
make export-map           # export the map from an already-built image
make map                  # regenerate the map ignoring the cache, then export it
make competitive          # benchmark complete configurations (needs Docker)
```

The build uses the cached map `maps/dependencies.json.gz` only if
`tools/map_fingerprint.py --check` passes. The check hashes the builder files
(`tools/build_map.py`, `tools/instrument.py`, `tools/fetch_source.py`,
`requirements.lock`, with line endings normalized) and the map's own digest
against `maps/dependencies.meta.json`. If they do not match, the image build
fails rather than shipping a stale map. Building the map from scratch takes
about 13-14 minutes, which exceeds the 10-minute build budget; this is why the
map is cached in the repository (1.1 MB gzipped, 33 MB raw). To rebuild it after
changing a builder file, run `make map`: it builds with `FORCE_MAP=1` (ignore the
cache) and `MAP_TIMEOUT=3600`, then `tools/export_map.py` writes
`maps/dependencies.json.gz` and `maps/dependencies.meta.json` (the builder digest
of the files inside the image, and the environment: Python 3.13.18, coverage
7.10.7, x86_64). Commit both files.

When regenerating, the build installs the supplied locked test environment,
downloads only sqlglot at commit `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, and
runs its unchanged tests.

To submit the scheduling-only backup instead of the dependency selector, replace
the top-level Dockerfile and commit that change:

```sh
cp Dockerfile.safe Dockerfile
git add Dockerfile
git commit -m 'Use scheduling-only submission'
make sample
```

Local scoring without Docker is also available:

```sh
python3 tools/benchmark.py --source /absolute/pinned/sqlglot --map maps/dependencies.json.gz
```

The local benchmark does not reproduce container isolation, the scorer's Python
version, or cgroup CPU accounting.

## How selection works

The map is generated from the unchanged pinned source (cached in the repository, see above):

1. Coverage records executed source lines in a separate context for each test.
2. Targeted AST observers record instance, classmethod, module, and dynamic
   `getattr` reads of settings. This avoids wrapping every object attribute.
3. Audit events record fixture and explicit source reads, including cached
   `open` aliases. Ordinary import-loader reads are excluded. Class setup reads
   are shared with that class rather than the entire test suite.
4. Static references use the exact executed use line, rather than all callers
   of a containing function. Initializer copies and inheritance still propagate.
5. Comparison observers record baseline operand relations. When `relations` is
   enabled, a change confined to comparison operators can retain only
   boundary-reaching or unknown tests. Unsupported user-defined comparisons
   remain conservative. This is off in the current default.
6. Process-pool work is run inline in the calling test during map generation,
   and submissions and results are round-tripped through pickle, so the map
   attributes that work to the test that requested it.
7. Eligible plain unittest tests can be verified in isolated forked processes
   (only when `probe_budget` is above zero).
   Only confirmed passes are removed; subtest failures, import errors, class
   teardown errors, and timeouts retain selections. The batch has an 8-second
   child CPU budget, with a 1.5-second wall limit per probe.

At runtime the selector verifies each patch against the pinned files in memory.
It unions affected lines, constant users, function-header users, insertion
regions, and fixture readers. It checks all files in a patch. A change to short-circuit evaluation also selects
users of the earlier operands. Initializer-only import-time edits are checked by
a forked import of the patched module, with CPU charged to `import_check_budget`.
`test_lazy_load` is the only external test; it is narrowed to the files that
`import sqlglot` executes or reads (`tools/import_closure.py`, generated at image
build). Python changes with
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

Run `make competitive` to benchmark complete configurations (see below).
Unit tests run with `make unit` and in CI.

Baseline coverage is an approximation of dependencies. Reflection, state shared
between tests, indirect class-level access, and behavior in uninstrumented child
processes can require dependencies the map does not observe. Static propagation
and fallbacks reduce this risk; they are not a proof against missed failures.
Full sample measurement is required before judging the tradeoff.

## Competitive measurement

```sh
make competitive
```

This requires Docker and network access during build. It applies the scoring
container limits to every run, measures CPU, and benchmarks five complete
configurations (`VARIANTS` in `tools/competitive.py`), passing every setting
explicitly as a flag. The winning zero-miss configuration is written to
`selector-config.json` verbatim, including `import_check_budget`. The tool then
rebuilds the image and re-runs the shipped default to confirm it reproduces the
measurement (skip with `--no-validate`). It no longer writes a raw
`maps/dependencies.json`; the map is managed by `make map`. It exports
`research-bundle.zip`. The timing wrapper adds a small amount of CPU, so the
official score may differ.

See `COMPETITIVE.md` for the evidence needed to continue tuning, and
[VALIDATION.md](VALIDATION.md) for the held-out mutant evaluation.

## CI

`.github/workflows/checks.yml` runs the unit tests and the cached-map
fingerprint check on every pull request and push to main. The Docker benchmark
workflow, **Benchmark pinned sqlglot**, stays manual.

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

# Shrapnel

Test selection for certain libraries. Given a unified diff against that
revision, `selector.py` prints which catalog tests to run and how to spread
them across up to eight workers, so that every test the patch breaks is run
for as little total time as possible.

The cost model is in [COST_MODEL.md](COST_MODEL.md) and `score.py` implements
it: a patch costs its slowest worker's load plus a fixed charge per worker, and
a failing test that isn't selected costs a large penalty. Selector CPU time
counts too.

## Run

Docker must support `linux/amd64`. From this directory:

```sh
make unit                 # unit tests (standard library only)
make build                # docker build (uses the cached map)
make sample               # build, run on the sample patches with the container limits, score
make check                # validate the output format under the same limits
make safe-sample          # scheduling-only fallback (BUILD_MAP=0)
make local-benchmark      # no Docker; full-suite fallback without a map
make export-map           # export the map from an already-built image
make map                  # regenerate the map ignoring the cache, then export it
make variants             # benchmark complete selector configurations (needs Docker)
```

The image reads `/data/tests.json` and `/data/commits/*.patch` and writes one
JSON line per commit to stdout.

## How selection works

A dependency map is built from the unchanged pinned checkout and cached in
`maps/` (building it takes about 13–14 minutes):

1. Coverage records executed lines per test.
2. AST observers record attribute, dynamic `getattr`, module and comparison
   operand reads per test.
3. Audit hooks record fixture and explicit source reads. Reads during a class's
   `setUpClass` are attributed to the tests that read the attributes they feed
   (`tools/setup_flow.py`); anything that can't be followed stays class-wide.
4. Process-pool work runs inline during map building, with pickle round-trips,
   so worker-process code is attributed to the calling test.

At selection time the selector applies each patch to the pinned files in
memory, checks the base text, and unions the tests depending on the changed
lines, regions, constants and fixtures. Refinements:

- Comparison-operator edits use recorded operand relations (`relations`).
- Short-circuit (`and`/`or`) edits include tests that stopped at earlier operands.
- Edits confined to values evaluated at import time are checked by importing
  the patched module in a forked child (`import_check_budget`).
- The lazy-import test is limited to files `import sqlglot` executes or reads
  (`tools/import_closure.py`, generated at image build).
- Fixture edits narrowed by class-setup attribution are used only after the
  class's `setUpClass` succeeds on the patched files in a forked child
  (`setup_check_budget`).
- TPC fixture edits that leave executor inputs unchanged can drop the executor
  test (`executor_fixtures`).
- Unknown files, malformed patches, syntax errors, unsupported import-time
  changes and incomplete maps select the entire suite.

Each selection is scheduled longest-first for every worker count from 1 to 8,
and the cheapest is chosen.

Settings live in `selector-config.json`. Every key is documented by
`tools/variants.py` (`BASE`) and covered by `tests/test_config.py`.

## The cached map

The build uses `maps/dependencies.json.gz` only if
`tools/map_fingerprint.py --check` passes. That check hashes the builder files
(`tools/build_map.py`, `tools/instrument.py`, `tools/setup_flow.py`,
`tools/fetch_source.py`, `requirements.lock`) and the map against
`maps/dependencies.meta.json`; a mismatch fails the build. After changing a
builder file, run `make map` and commit both files.

## Evaluation

- `make variants` benchmarks complete configurations under the container limits
  (see [BENCHMARKS.md](BENCHMARKS.md)).
- Held-out mutants: `tools/mutants.py` generates seeded single-token mutants of
  the pinned code; `tools/ground_truth.py` runs the full real suite on one patch
  inside the image to record which tests fail; `tools/mutant_eval.py` scores
  selector variants against them with a tune/holdout split. The manual GitHub
  Actions workflow **Held-out mutant ground truth** runs this at scale. Mutant
  results are for evaluation only and are never selector input.
- See [VALIDATION.md](VALIDATION.md) for measurements.

## CI

`.github/workflows/checks.yml` runs the unit tests and the cached-map check on
every pull request and push to `main`. The Docker benchmark workflow is manual.

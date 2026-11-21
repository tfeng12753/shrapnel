# Benchmarking selector configurations

The cost floor on the sample patches is 94.351 simulated seconds: for each
commit with failures, the cheapest schedule of exactly its failing tests
(minimize `max(longest_test, ceil(total/k)) + 2*k` over worker counts 1–8).
That figure is diagnostic only. Sample answers never enter selection or map
generation.

The remaining cost comes from selecting tests that don't fail, especially the
28.8 s parser stress test and the 21.5 s TPC-DS executor test.

## One command

```sh
make variants
```

`tools/variants.py` builds the image (600-second build limit), then runs every
configuration in `VARIANTS` three times with the container limits from the
`Makefile`. Each variant is a complete configuration; every setting is passed
as an explicit flag so the shipped `selector-config.json` can't leak in. The
best zero-miss variant is written to `selector-config.json` verbatim, the image
is rebuilt, and the shipped default is re-run to confirm it reproduces the
measurement (skip with `--no-validate`). Results go to `variant-results.json`
and `research-bundle.zip`.

CPU is measured with a small wrapper (`tools/timed_run.py`); cgroup accounting
in other environments may differ slightly.

## Finding expensive selections

Each variant in `variant-results.json` includes a `schedule_profile` for its
median run, ranking patches by actual schedule cost minus the cost of
scheduling only the labeled failures. To trace why tests were selected, replay
an output against the source and map:

```sh
python3 tools/profile_schedule.py outputs/relations-1.jsonl \
    --source /path/to/pinned/sqlglot --map maps/dependencies.json.gz > profile.json
```

## Evidence convention

Every reported number names the exact `selector-config.json`, the map
fingerprint (`builder_sha256` and `map_sha256` from
`maps/dependencies.meta.json`) and the commit.

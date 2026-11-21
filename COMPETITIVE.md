# Reaching a competitive score

The previous 1104.5-second result selected every test. Replacing Python with
Rust could save only a fraction of the roughly 0.05 seconds spent choosing that
schedule. The useful work is identifying unnecessary tests, especially the
28.817-second parser test and the 21.454-second executor test when unrelated.
They must still be selected whenever they fail. On the public samples, the
mathematical lower bound for simulated CI cost is 94.351 seconds before selector
CPU: for each failing commit, minimize `max(longest_test, ceil(total/k)) + 2*k`
over worker counts 1 through 8. Scheduling exactly the labeled failures attains
that bound on these samples. This calculation is diagnostic only; answers never
enter selection or map generation, and the hidden batch is different.

The current dependency path adds three forms of precision:

- Attribute tracing includes class-level access and builtin `getattr`, without
  globally wrapping expression objects or altering deferred annotations.
- Constant dependencies use actual source-line users. A constant referenced in
  one branch no longer inherits every test entering the surrounding function.
- Baseline comparison relations distinguish tests at a changed boundary from
  tests whose result is unchanged. Only operator-only patches can use this;
  operand changes and unsupported values retain ordinary selection. Unknown
  observations stay selected.

A native unittest verifier can additionally remove confirmed passing tests. It
runs only baseline-qualified plain unittest cases, skips pytest marks/fixtures,
avoids slow/large sets, and stops spending CPU after a batch budget. This can
save a worker startup on harmless code changes. It can also cost more than it
saves, so it is measured as a separate feature rather than assumed beneficial.

No mutation results are precomputed. The map contains baseline source coverage,
settings readers, file readers, and operand relation bits—not an edit-to-failure
lookup. Runtime verification actually applies the received patch in memory and
runs tests, so its CPU is included.

## One command to obtain real measurements

```sh
make competitive
```

It builds the pinned image with a 600-second timeout (the map comes from the
cache; see README.md), then runs each of these complete configurations three
times under the documented container restrictions. Every setting is passed
explicitly as a flag, so the image's shipped defaults do not affect the
measurement.

| Variant | Operand relations | Runtime verification | Notes |
| --- | --- | --- | --- |
| conservative_python | Off | Off | Full suite for executable Python edits; import checks off |
| coverage | Off | Off | `import_check_budget` 6.0 |
| child_work | Off | Off | Keeps tests that can dispatch to a child process |
| relations | On | Off | |
| relations_and_probes | On | 8-second child CPU budget | |

The report includes misses, simulated cost, CPU, and median total. CPU includes
a small measurement wrapper; the competition's cgroup accounting is authoritative.
A variant with any sample misses is excluded from automatic selection. If none
has zero misses, the map needs debugging before submission.

The command writes:

- `competitive-results.json`: measured results and best zero-miss variant.
- `selector-config.json`: the winning complete configuration, verbatim,
  including `import_check_budget`; no test or patch IDs.
- `research-bundle.zip`: source checkout, compressed map, outputs, and results.

It no longer writes `maps/dependencies.json`; the map is cached by `make map`.
After writing the config, it rebuilds the image and re-runs the shipped default,
failing if the result does not reproduce the measured variant (skip with
`--no-validate`).

The public-sample winner is not necessarily the submitted default. The default is
chosen using the held-out mutant evaluation described in VALIDATION.md, because
the public patches were used during development. Any result reported from here
follows the evidence convention: the exact `selector-config.json`, the map
fingerprint (`builder_sha256` and `map_sha256` from `maps/dependencies.meta.json`),
and the project commit.

## Continue development from the evidence

Upload `research-bundle.zip` to the coding conversation. It contains the exact
pinned source and map needed to inspect why expensive tests remain selected,
reproduce selection offline, and target the remaining cost without inventing
failure rules. It also reveals actual instrumentation build time and any
baseline/collection problems. The authoring environment currently cannot fetch
that source, install the test packages, or run Docker.

Alternatively, publish the source repository and manually run its **Benchmark
pinned sqlglot** GitHub Actions workflow. Download the `blast-radius-research`
artifact and use its research bundle. The workflow does not push commits or
submit an entry to the competition.

## Find expensive selections

Each strategy in `competitive-results.json` includes a `schedule_profile` for
the run closest to its median total. `profile_output` names the corresponding
JSONL in the research bundle. Patches are ranked by actual schedule cost minus
the cost of scheduling only the labeled failures with the same scheduler.
Penalties and missing test numbers are reported separately. Check misses first.
The reference is a feasible schedule, not necessarily optimal on other batches.

For each patch, the top three extra tests are ranked by how much removing just
that test saves with the other assignments fixed, breaking ties by duration.
These individual savings cannot be added together. To trace why those tests
entered static selection, replay the output against the source and map from
the same run (use `--no-relations` for coverage, `--no-relations --child-work` for child_work, or `--conservative-python` for the conservative strategy):

```sh
python3 tools/profile_schedule.py outputs/relations-1.jsonl \
    --source /path/to/pinned/sqlglot --map dependencies.json.gz > profile.json
```

Omit both paths to analyze a schedule without replay. Replay explains static
selection before runtime verification. The profiler reads sample answers on the
benchmark host, outside the timed selector.

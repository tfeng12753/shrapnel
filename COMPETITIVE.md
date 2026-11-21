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

It builds the pinned image with a 600-second timeout, then runs each of these
three times under the documented container restrictions:

| Strategy | Operand relations | Runtime verification |
| --- | --- | --- |
| coverage | Off | Off |
| relations | On | Off |
| relations_and_probes | On | 8-second child CPU budget |

The report includes misses, simulated cost, CPU, and median total. CPU includes
a small measurement wrapper; the competition's cgroup accounting is authoritative.
A strategy with any sample misses is excluded from automatic configuration
selection. If none has zero misses, the map needs debugging before submission.

The command writes:

- `competitive-results.json`: measured results and best zero-miss strategy.
- `selector-config.json`: global defaults for that strategy; no test or patch IDs.
- `maps/dependencies.json`: cached unchanged-baseline map, if it fits within 18 MB.
- `research-bundle.zip`: source checkout, compressed map, outputs, and results.

The image being measured has the old defaults, but every strategy is explicitly
forced through CLI flags. Rebuild after committing the new config/map to submit
the measured behavior with the competition's default `docker build`.

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

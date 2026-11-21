# Overnight map precision

Base 2c4b6a8. Map construction uses the unmodified pinned checkout and its baseline suite. Generated patches are used for evaluation.

## Indexing comparisons for the verified import replay

Pass `--index-cold-operators` to `tools/import_closure.py` to generate an optional index for the offline import or combined replay. It records UTF-8 byte offsets for 657 comparison operators inside ordinary functions never entered during the baseline import. A source digest and an exact check of the surrounding bytes allow a single operator replacement to bypass two full-module parses. Other edits continue through the existing analysis. The original pytest item must still pass on the complete patched checkout before a schedule is removed. The ordinary production selector does not require the new index.

Seven alternating pairs of complete public replays produced the same 109.063 simulated seconds with zero misses in all 14 runs. Median totals including selector and verification CPU were 110.529 s without the index and 110.444 s with it. Machine load varied across runs. Across 18 independent edits (one hash-ranked site per eligible module, excluding public patch locations), both classifiers accepted every edit. Eligibility CPU alone fell from 0.486 s to 0.0035 s in aggregate. The original lazy-import test passed on all nine alternating independent patched modules. Local unit checks passed 100/101; the pre-existing map fingerprint still needs the Linux Docker rebuild.

A separate experiment indexed literal keyword arguments at covered calls. In a 44-patch independent workload it lowered median simulated cost but raised the CPU-inclusive median from 315.259 s to 316.440 s. On the public batch it raised the paired median from 114.687 s to 115.128 s at a four-second check budget. The keyword expansion was discarded.

## Test-module bytecode for native verification

The same build-time preparation now includes the pinned test tree. Native unittest verification imports these ordinary modules repeatedly. The host adds 66 checked-hash cache files totaling 3,044,852 bytes. Pytest replay staging continues to exclude test caches so the original pytest assertion rewriting runs normally.

Using `--relations --probe-budget 2`, seven alternating pairs per batch produced byte-identical selector outputs within all 21 pairs. Median seconds including CPU were 115.596 → 115.561 on the public 30, 34.773 → 34.791 on the first independent eight and 37.374 → 37.373 on the second eight. Public misses were zero. These are existing-selector measurements. The earlier 109.063 simulated result is for the additional offline checks.

For a direct verifier comparison, order native-eligible test files by SHA-256 of their path and take the first 12. Pick the minimum SHA-256 node ID in each file among items at or below the normal 0.15 s per-item cutoff. Run the original native verifier separately on each unchanged case three times per configuration, alternating order. All 72 checks confirmed the same passing IDs. Summing per-item median child CPU gave 3.376 s with production bytecode alone and 2.860 s with test caches as well (15.3% lower). The verifier retained every one of 14 known failing items when run against two independent real overlays with prepared test modules.

The cached checkout passed all 1,621 original tests and 25,454 subtests. Project units passed 99 of 100 checks, with the previously documented cache fingerprint failure. Linux/amd64 validation remains pending.

## Hash-checked bytecode for the existing selector

Checkpoint `8b215b2`. The final build-time closure step precompiles the unchanged production package after observing the import. This prepares 184 CPython cache files (4,467,466 bytes on the host). Python checks a hash of each source file before loading cached code. The selector's import overlay still compiles modified modules from their patched source. The staged pytest audit only copies checked-hash production caches for unchanged modules. A regression test edits a module while preserving its size and timestamp and confirms a new Python child observes the changed value.

The image's existing no-argument closure command prepares bytecode automatically. With an explicit checkout, use `python tools/import_closure.py --source "$SOURCE" --prepare-bytecode --output /tmp/import-closure.json`. Use a writable copy of the pinned revision. Each legacy closure field and the entire unentered-function inventory matched before and after preparation. Three forced host preparations took a median 1.289 s wall and 1.170 s CPU. The prepared checkout passed the entire original suite (1,621 tests, 25,454 subtests). Source preparation works with the existing selector. Its shipped defaults produced byte-identical schedules at 166.316 simulated seconds with zero misses.

Five interleaved public runs per configuration compared the same 30 patches. The current selector with `--relations --probe-budget 2` reached 113.877 simulated seconds in all five runs with prepared bytecode and in one of five without it. The bounded verifier retained more tests on the other four ordinary-source runs. Median totals including all selector and child CPU were 115.625 s with prepared source and 118.694 s with ordinary source. Local CPU and verifier timeouts vary with machine load.

## Budgeted dictionary replay

The host-built map now has an optional offline index of 2,885 scalar dictionary values in 42 production files (64,787 bytes). It is generated once from the complete pinned dependency map and matching source. Each file has a SHA-256 digest. The index records UTF-8 byte offsets for single-line strings, booleans, floats and integers in dictionaries with unique literal string keys. Candidate selection uses the map and patch only.

The offline audit considers one-worker schedules whose catalog time is at most one worker startup (2 s), in ascending catalog-time order. It requires a matching file digest, one same-type value edit, unchanged surrounding source and a successful parse. It materializes the patch, executes every scheduled pytest item, including child interpreters and subtests, and compares the JUnit case identities to the exact set requested. Any failure, skip, timeout, wrong or missing item, or incomplete evidence retains the entire schedule. The default allowance is two CPU seconds. A separate wall deadline is twice the CPU allowance. The audit measures parent and waited-child CPU between attempts, starts with the cheapest group, and stops when the last check would exceed the remaining allowance. Production integration still needs the teammate's shared, container-wide CPU accounting. All measurements here are outside the production selector.

From the repo root, use the locked environment, the pinned checkout in `SOURCE`, and the host dependency map in `MAP`. Use the existing relations-enabled, two-second native-probe schedule from the import replay below.

```sh
python -m tools.map_dictionary_index --source "$SOURCE" --map "$MAP" --output /tmp/dictionary-index.json
python -m tools.map_dictionary_replay --source "$SOURCE" --data sample --index /tmp/dictionary-index.json --schedule /tmp/map-schedule.jsonl --import-closure /tmp/import-closure.json --cpu-budget 2
```

The combined command reports verified import rows and dictionary rows separately. It skips speculative dictionary checks when the preceding import verification already cost more than the available CPU. For an end-to-end comparison, empty only the reported verified rows. Score that output against the public answers and sum the CPU used by the selector, both offline audits and their subprocesses. Exclude the one-time index build and the scorer. The new audit never consumes answers.

Five public replays with the prepared source and both offline checks scored 109.063 simulated seconds each with zero misses. The prepared-source import-only reference was 111.509. The median CPU-inclusive totals in that series were 114.451 s and 115.014 s respectively. The dictionary check verified all four original catalog items on the eligible public edit, including 52 subtests. The larger 273-test schedule did not qualify. A final three-run replay at checkpoint 8b215b2 again produced 111.509 and 109.063 with zero misses. Its median CPU-inclusive totals were 112.291 and 110.251 s. Both timing series are local and include the checkouts and test subprocesses.

**Independent workload.** Exclude public patch locations, then order eligible boolean and integer dictionary value sites in each pinned module by SHA-256 of relative path, line and byte column. Generate the edit at the first-ranked site per module, toggling a boolean or incrementing an integer. A second set uses the second-ranked site per module. Each set covers 16 modules. Evaluate the eight even-indexed modules in each. Site choice never uses labels, durations, or observed savings. The same two-second budget and ordering apply to every batch.

| Three-run local median | Import-only simulated | Combined simulated | Import-only with CPU | Combined with CPU |
| --- | ---: | ---: | ---: | ---: |
| Public 30 | 111.509 | 109.063 | 112.291 | 110.251 |
| First independent eight | 33.753 | 31.357 | 34.892 | 34.160 |
| Second independent eight | 36.348 | 33.952 | 37.504 | 36.715 |

Independent simulated times are schedule costs before miss penalties. In the first set, real patched full-suite runs covered all five edits eligible on catalog cost. Three passed and two produced 3 and 11 failing test occurrences. All 14 failures were retained. The separately chosen edit omitted in the second set also passed a full patched suite of 1,621 tests and 25,454 subtests. Across the six full patched suites, 9,726 catalog test executions retained all 14 observed failure occurrences. The expensive 273-test public case remains a whole-dictionary dependency. It needs additional evidence about the tokenizer's indirect table reads. No direct subtraction of per-key masks was enabled.

The 99 passing local unit tests include cache invalidation, literal boundaries, byte offsets, stale index, timeout budgeting and grouped test-identity checks. The remaining failure is the pre-existing cached-map fingerprint. Build the official map with Linux/amd64 `make map` and rerun the public limits before changing production settings. Hidden scores remain unmeasured.

## Verified import-check opportunity

The relations-enabled public baseline on the 30 patches was 115.907 simulated seconds. Enabling the existing native verifier with `--probe-budget 2` reduced that to 113.877. The new offline replay reduced the same schedule to 111.509 by verifying one additional singleton lazy-import test on patched source. The official local scorer found zero missed failing tests in each of three runs per configuration.

| Public configuration | Simulated seconds | Median seconds including local CPU |
| --- | ---: | ---: |
| Relations, probe budget 0 | 115.907 | 116.386 |
| Relations, probe budget 2 | 113.877 | 114.658 |
| Relations, probe budget 2, plus offline patched import verification | 111.509 | 112.921 |

CPU includes the selector and its children. The offline row also includes the replay process, staging the checkout, the original pytest item and its child interpreter. Scoring is outside the timed interval. The production selector can reproduce the first two rows on the host-built map. The third row is a measured integration opportunity for the selector owner. The shipped configuration still scores 166.316 simulated seconds. The hidden score, production CPU budget and Linux container result remain unmeasured.

**Build evidence.** The closure records 1,660 ordinary functions that were compiled but never entered during the baseline import, across 30 source files. The pinned revision, observed tests, 36-file list, line sets and explicit-read list matched the previous host observation exactly. Unsupported monitoring, explicit source reads, audited code-object inspection, child-process launches and suspension complicate attribution. The observer suppresses the affected hints. Even a valid hint only nominates a check.

**Selector handoff.** Consider a schedule containing only the recorded external test. Require all edits to qualify as comparison-operator changes inside undecorated ordinary function bodies listed in the closure. Apply every hunk to a fresh pinned checkout and run the original catalog item with the same environment and child interpreter. Accept only an exit code of zero and one passing, unskipped test. Keep the original selection if evidence is incomplete, checking fails, or the runtime budget runs out. Production integration needs a shared CPU limit that includes descendants. The offline diagnostic limits wall time and reports eligible savings without editing the selector or emitting a replacement schedule.

**Independent checks.** Exclude every public patch location. Within each pinned module, order single-line comparison sites by SHA-256 of path, line and column, then take the first eligible operator edit. Eighteen modules qualified. Every even-indexed module was tested in a separate freshly patched checkout, and all nine lazy-import tests passed. A deliberately corrupted dialect registry caused the original item to fail and the replay rejected it. Independent runs check this specific obligation. They do not estimate a hidden score.

**Reproduction.** From the repository root in the locked Python environment, generate a closure and run an ordinary public schedule. Use the previously validated host map for this host comparison, or the rebuilt map for Docker validation. Set `SOURCE` to the pinned checkout and `MAP` to that map.

```sh
python tools/import_closure.py --source "$SOURCE" --output /tmp/import-closure.json
python selector.py --source "$SOURCE" --data sample --map "$MAP" --import-closure /tmp/import-closure.json --relations --probe-budget 2 > /tmp/map-schedule.jsonl
python score.py sample sample/answers.json /tmp/map-schedule.jsonl
python -m tools.map_import_replay --source "$SOURCE" --data sample --import-closure /tmp/import-closure.json --schedule /tmp/map-schedule.jsonl
```

The diagnostic reports 2,368 removable milliseconds. It never reads scoring answers. Subtract the reported milliseconds from the baseline simulated total for a passing public replay. Separately grade a schedule with just the verified rows emptied to check for misses. Of 94 local unit tests, 93 passed. The existing cache-fingerprint failure still requires the Linux/amd64 `make map` rebuild from the morning handoff.

## Previous validated builder (1253171)

The map records 81 additional normalized-string comparison sites. Removing only those records reproduced the entire decoded 7642017 host map exactly. The new diagnostic gzip is 1,121,870 bytes (+2,002 bytes). With the original per-test timeout the pinned run passed all 1,621 tests and 25,454 subtests. Host stage times were 0.57 s modeling, 313.79 s suite, and 3.16 s aggregation/serialization. Both public schedules matched the previous build byte-for-byte: 166.316 simulated seconds under shipped defaults or 115.907 with relations enabled, zero misses.

**Independent normalized-string workload.** Select one site per pinned production module by minimum SHA-256 of relative-path/line/column, following the earlier length experiment. Eligible source comparisons are single-line == or != between a literal string and an argument-free upper(), lower() or casefold() call. Compare all seven other operators at each site. No labels or observed savings influence the site selection. In 231 edits across 33 modules, aggregate simulated scheduling fell from 1,015.882 to 920.609 seconds relative to 7642017, saving 9.38%. Of those schedules, 89 shortened, 142 matched, and none grew. This measures normalized-string edits with comparison refinement enabled; the competition score and patch distribution are separate.

**Execution checks.** On every even-indexed module, test every operator edit that omitted any previously selected test. There were 48 such patches and 335 newly omitted test-case executions. All 335 passed on the patched checkouts. Three of the 48 ran the full pinned suite, using <, <=, >, >= rotated by module index to choose the initial patch per module. Those three produced 2, 8 and 12 failing catalog tests respectively, and all 22 failure occurrences were selected. The remaining 45 executed every omitted case. The competition and mutant holdouts were not accessed.

The stale Docker-built cache remains the sole local unit failure (89/90 passed). The new data requires the existing relations option. No new selector defaults or host-generated maps were committed. Resume at **Morning review procedure** for the Linux/amd64 Docker rebuild and competition-limit replay.

## Previous length-check results (builder 7642017)

The added map data covers 123 length-comparison sites. All 727 previous comparison records and every other legacy map field matched the earlier clean host map exactly. Both official public schedule replays also matched byte-for-byte: 166.316 s with shipped defaults and 115.907 s with relations enabled, zero misses. Only the relations-enabled selector consumes the additional evidence. The final host builder passed 1,621 tests and 25,454 subtests in the original timeout, with stages of 0.61 s modeling, 316.77 s suite, and 3.51 s aggregation/serialization. The gzip is 1,119,868 bytes, 5,014 bytes larger than the earlier clean map. No official Linux rebuild or hidden score is available. Local and CI unit runs passed 89 of 90 checks; only the expected stale Docker map fingerprint failed.

**Independent scheduling experiment.** Enumerate pinned production modules in sorted path order. Among single-line single-operator equality or inequality comparisons of len(x) against an integer literal, pick the site with the smallest SHA-256 of relative-path/line/column (slash-separated). Generate all seven other supported operators at that site. No public answers, failing-test labels, module names or timings influence the site choice or production logic. Across 392 generated variants in 56 modules, simulated schedule time was 2,833.838 s before and 2,448.382 s after (13.6% lower): 113 shorter schedules, 279 equal and zero longer. These are schedule-only totals for length-predicate edits, not estimates of competition score. Pure Eq/NotEq inversions saved zero, as expected.

**Patched execution.** An additional one-edit-per-module sweep replaced equality with <= or inequality with >. Among 28 even-indexed development cases, 22 removed tests. All 382 newly omitted test-case executions passed on the patched source. The first three cases ran the entire pinned suite; the other 19 ran their omitted tests. A fourth complete suite checked a balanced retained/omitted case. A fifth, boundary-reversing executor control produced four failing catalog tests; all four were selected. No official mutant holdout results were opened. Scoped synthetic tests cover mixed values, both possible identities for equal integers, exceptions, overloaded equality and shared contexts. These validations are finite; do not treat an unobserved case as a guaranteed pass.

Before adoption run the Docker rebuild and replay in **Morning review procedure**. The parser stress timeout rejected a broader prototype; the final narrower observer passed without changing the timeout. No default configuration or selector was modified.

## Earlier overnight handoff

Code revision 86b3cd6 on overnight/map-precision, draft PR #9. The allowed source changes are complete. The final host diagnostic map passed all 1,621 pinned tests / 25,454 subtests. The default public replay is byte-identical to the cached Linux map at 166.316 simulated seconds with zero misses. With the existing comparison refinement enabled both maps also produced identical output at 115.907 s and zero misses. This branch implements map-side observations only. No hidden or mutant outcomes were used.

| Final builder stage | macOS arm64 wall seconds |
| --- | ---: |
| Source model | 0.49 |
| Instrumented pinned suite including setup | 304.14 |
| Aggregation and serialization | 3.34 |
| Total of stages | 307.97 |

The unchanged builder from 2c4b6a8 took 293.400 s end-to-end on the same host. With the same coverage, pool substitution, pytest and file auditing, disabling its AST observers cut the control to 129.203 s. These single-run measurements identify build instrumentation as the main optimization target. Preserve the cache for the competition's 600 s build budget. The existing 14-minute Docker figure comes from the brief; this host has no Docker executable.

The final diagnostic map occupies 1,114,908 bytes. It includes 1,274 dictionary bindings. For 30, at least one test had keyed evidence outside the whole and unobserved masks. This measures possible analysis opportunities, not guaranteed test removals. The Snowflake example retains all 273 candidates; costing it as a 9.078 s win would be incorrect. The new file ordering and repeated-mask grouping reduce the existing map by 72,230 bytes when compared with identical content.

Local and Linux CI unit runs passed 86 of 87 checks. The sole failure is the required cache fingerprint mismatch after changing the builder. Before merging, run make map on Linux/amd64 Docker and repeat the checks below. No generated map or metadata is part of this PR. An independent repeat used a fresh extraction of pinned sqlglot, PYTHONHASHSEED=1729 and bytecode writing disabled. It passed all 1,621 tests and 25,454 subtests. Stage timings were 0.59, 310.26 and 3.33 s (314.18 s total). Both sets of public schedules again matched byte-for-byte. All dictionary observations on common files and all import closure files, lines and explicit reads were identical. The clean diagnostic map was 1,114,854 bytes; its only difference in the set of file names was an absent pytest bytecode cache. Both diagnostics used macOS arm64, Python 3.13.15, coverage 7.10.7 and pytest 9.2.0.

All Brief A local work is saved. Resume with the Docker validation under **Morning review procedure** and coordinate the selector-side eligibility checks described below. No reset used.

## Import trace

The import child records baseline line events per repository file and explicit file reads. Original files list preserved. Synthetic test passed. Host pinned import observed 36 files. Only use lines for a verified same-location operator-only edit with unchanged operands, syntax, and enclosing structure. Keep file-level behavior on missing lines, explicit reads, changed control-flow entry, insertions, decorators, shared/multiple-line expressions without a verified execution anchor, or an incomplete observation. Union across files. A raw line-number intersection alone is insufficient.

Next: dictionary observations, dependency audit and size/time experiments. Docker unavailable on this host. Do not ship a host-built map.

## Dictionary reads

Import trace checkpoint 2eacb47. Dictionary checkpoint follows it in history. The recorder preserves the original dict and evaluation order. It tracks direct subscripts, get and membership for class-field and module names declared with a dict literal. Other direct uses, including handing the dictionary to another function, are whole reads. Same-spelling symbols share observations conservatively. Non-string keys and unscoped/setup reads broaden attribution. Synthetic tests exercise class and module lookups, exact identity, order, bulk operations and unknown keys.

Per-file optional dictionary_reads maps qualified symbol to start/end, whole (hex test mask), and keys (exact string to hex test mask). For independently verified value-only edits to existing unique literal string keys in one unchanged binding, union all affected key masks, whole readers, raw source readers, external obligations and unresolved legacy users. Preserve tests with multiple selection reasons. Missing fields, table shape changes, unsupported keys, nested mutation, access via untracked aliases, reflection, native consumers or uncertified observation paths require ordinary selection. A test having one recorded key does not certify that all its other reads were observed. Treat these records as advisory until every access path for the binding is accounted for. In particular a tokenizer constructor that receives the whole dict currently remains a whole reader. No speedup asserted.

Docker absent. The committed cache fingerprint check must fail after builder edits until an authorized make map rebuild. Do not modify the metadata to bypass that check. Host diagnostic build started separately; next run dependency audit and storage comparison.

## Dependency audit

Dictionary checkpoint adb2e74. Synthetic audit tests run actual transformed production modules and an isolated filesystem resource audit hook. Seven cases pass. Ranked residual risks:

1. Persistent lru_cache hits bypass the function body. The first test records Config.FLAG but the second test reuses the cached result. A conservative future fix should attribute cached function dependencies to call sites, including decorated aliases, without forcing cache clears and changing baseline behavior.
2. Shared class state and import-time initialization. Both metaclass creation and __init_subclass__ reads are observed during an active test. Later tests using stored values need propagation. The audit demonstrates both creation hooks and a later reader. Do not use missing per-key observations as evidence of independence here.
3. Long-lived background threads can execute while another test is current. A joined worker captured the expected attribute. Background filesystem reads now retain all tests and use per-thread recursion guards. Attribute and line attribution across test boundaries still need provenance.
4. Direct module __getattr__ reads that access tracked class fields were observed. Synthesized names and cached module results can still escape tracking.
5. Filesystem-backed importlib.resources generated a captured read. Collection-time non-source reads without an attributable test now retain all tests. Archive/extension loaders may only expose container-level access.

Next: measure storage and timing, replay both encodings, compare host diagnostic against cached Linux map.

## Storage and public replay

Audit checkpoint 49dc776. Grouped identical hex masks within each lines/anchors object without changing any keys, masks, or region ordering. Re-encoding the committed Linux map shrank gzip from 1,121,982 to 1,069,526 bytes (52,456 bytes, 4.68%). Omitting empty fields saved only 1,428 bytes by itself and was left out. Of 33,093,481 uncompressed JSON bytes, file-level lines account for 23,385,374 bytes, regions 5,085,281, anchors 3,836,787, comparisons 437,290 (sum of compact values per field). Before/after public JSONL byte-identical, SHA-256 7189decd1832209759637263631ccef60dd42cde664cbf4544b6fa6a53379e1e. Both scored 166.316 simulated, 0 misses. This is encoding equivalence, not a timing gain in selection.

The import subprocess now also executes the pinned test's registry assertion. 36 files traced on host. Of 85 unit checks, 84 passed and only the expected stale map fingerprint failed. The builder prints wall time separately for modeling, suite, and aggregation/serialization. Waiting for full host diagnostics. Next: final host replay, summarize limitations and update draft PR.

## Receiver identity refinement

Storage checkpoint 0b4f644. The first full host diagnostic at adb2e74 passed 1,621 tests / 25,454 subtests. Model 0.55 s, suite 295.33 s, aggregation/serialization 13.71 s. It replayed 166.316 s / 0 misses, but spelling-based observations bloated the diagnostic gzip to 8.6 MiB and combined unrelated classes.

Replaced spelling-based attribution: the transformer registers the original literal dictionary at its source location; key/whole observations match the live object identity. Membership preserves left-to-right evaluation, and get captures the bound method before evaluating arguments. Recognized import aliases are included. Exact string keys declared in the literal are recorded; other keys broaden to whole and arbitrary runtime inputs never enter the map. Chained assignments register all associated symbols. Added unobserved hex mask for legacy symbol users without any observations. Selector must always include this mask in addition to unknown paths described above. This does not certify the absence of other reads in tests with some observations. Unit checks include two unrelated classes with the same field name.

Receiver-identity checkpoint 46c854b. Added a serialized-mask regression: a keyed reader, a whole reader, and a legacy-only user retain three distinct bits. A background dictionary read now broadens that key to all tests. Read/write file handles are considered potential reads. Ten map-specific checks pass. Next: time/replay the final builder and document sample selection limits.

## Morning review procedure

Use tools/map_profile.py on the cached map and the rebuilt map to compare field bytes and reader counts without labels. The last count is an opportunity metric only. The pinned production source contains explicit __init_subclass__ and module __getattr__ definitions but no direct lru_cache decorators in a source search. Prioritize actual shared state ahead of the synthetic cache-hit hazard on this revision.

On a Docker host, check out the branch, run make map, then python3 tools/map_fingerprint.py --check maps/dependencies.meta.json --map maps/dependencies.json.gz and python3 -m unittest discover -s tests. Generate a fresh closure with python3 tools/import_closure.py --source /path/to/pinned --output /tmp/import-closure.json. Replay with python3 selector.py --source /path/to/pinned --data sample --map maps/dependencies.json.gz --import-closure /tmp/import-closure.json > /tmp/map-output.jsonl. Score with python3 score.py sample sample/answers.json /tmp/map-output.jsonl. Use make sample for official limits. Morning reviewer owns holdout evaluation. A selector PR must retain legacy fallbacks for old maps and independently validate pure literal values, identical key sets/order, no duplicate keys or unpacking, and unchanged binding/initializer behavior. Compare outputs before looking at timings.

Profiling tool checkpoint be296fb. Added precise observation for explicit dict.__getitem__, bound __getitem__, __contains__, and dict.get calls as well as subscription syntax. A shadowed dict descriptor retains the whole reader. The pinned lazy import had the same 36 files before/after tracing. Across those files, 10,817 executed line numbers were recorded. The dialect comparison at baseline line 1280 had neither a line hit nor an explicit file read. Enabling this evidence in a separate selector experiment may eliminate its 2.368 s residual schedule. No selector code changed here.

## Shared setup attribute propagation

Explicit method checkpoint 2659c22. A scan of the pinned tests found four setUpClass definitions and one tearDownClass. When a mapped class/module attribute is read under a recognized class/module setup or teardown frame, the builder now propagates the dependency to all tests in that exact class/module. A synthetic serialized-map test verifies both class members and excludes a similarly named neighboring class. Arbitrary shared objects created in ordinary call phases still need separate provenance. Full replay is queued to measure schedule impact.

Shared fixture checkpoint 498110f. Repeated comparison observations previously allocated a new candidate record on every call even if the site had already been seen. Allocating it once per site produced identical states in a synthetic 500,000-observation loop. Median process CPU over seven alternating trials fell from 0.0888 s to 0.0487 s; this is a recorder microbenchmark, not a projected full-build improvement. Final host build includes this change.

Recorder checkpoint 6d4e5f5. Sorting per-file records by repository path groups related modules for gzip and removes dependence on coverage's file iteration order. With mask grouping, the shipping map falls from 1,121,982 to 1,049,752 compressed bytes (72,230 bytes, 6.44%). Sorting by basename instead was worse at 1,059,282 bytes. Current selector output for all 30 public patches remains byte-identical to the original replay. These size numbers compare identical map content. New observations in a rebuilt map have their own storage cost.

Stable path ordering checkpoint 5006641. The intermediate bounded-identity host build passed 1,621 tests and 25,454 subtests and replayed 166.316 s / 0 misses with byte-identical public output. Of 1,274 dictionary bindings, 770 had observed keys and 30 had a key reader outside the whole/unobserved masks. These are observations, not verified savings. The Snowflake table had an unscoped whole read: retain the original 273 candidates for its binding. In a future selector, only replace the binding-specific legacy reason when eligibility is proved; a mask containing all tests due to unscoped access indicates fallback. Other independent reasons must remain in the result. Added ordering and shadowed-descriptor tests. Final shared-setup replay and controlled timings still running.

Evaluation-order checkpoint 13ce6c0. Dictionary binding registration now requires a direct declared name in the assignment targets; assigning obj.field = {...} cannot incorrectly register obj as the dictionary. For selector integration, require a unique, unconditional definition of the binding. Conditional redefinitions, object mutations, and unresolved aliases need legacy selection.

## Follow-up on general comparison precision

Based on ff8f321. The mapper now recognizes single-line equality and inequality against ordinary scalar literals in undecorated function bodies. It records relative order only for unequal, same-type builtin int, str, bytes, and bool operands. Mixed types, equal values (including ambiguous identity), overloaded operators, and observations during setup or outside the main test thread retain uncertainty. Definition-time expressions are excluded. No selector/config edits. Generated tests compared observer masks with Python operator results for 256 case/operator combinations, including errors; extra cases verify overloaded equality runs exactly once and keeps its original return object. No mutant outcomes or sample patch labels used to choose the rule.

Two build-only changes avoid probing unsupported None ordering and scanning an entire file for each symbol. Across 400 independent pairs the None optimization matched the previous relation encoding. A microbenchmark of None pairs used 0.0194 versus 0.0605 s median process CPU. Full baseline builds, old/new map comparison on identical recorded data, and independent per-module schedule replay are running. Unit suite: 89/90 pass with the same known stale cache fingerprint.

### Build admission for the new observer

Checkpoint 1dc38dd was an experiment. Instrumenting general scalar equality exceeded the existing 180-second timeout on the parser stress test, and the builder correctly rejected the incomplete map. The follow-up rule instruments single-line length checks against literal integer bounds in ordinary undecorated function bodies. Other equality expressions continue on ordinary coverage. This scope was chosen for the generic arity/length use case and instrumentation cost, before examining any generated patch outcomes. The time limit was kept intact. Full-suite rerun and even-indexed, outcome-blind validation pending.

The lossless aggregation experiment used one set of observations with both writers; the entire decoded maps were equal. With the additional equality observer disabled, the optimized full build also reproduced the earlier clean map exactly. The isolated line-mask step over all 10,442 pinned symbols fell from 0.4415 to 0.0201 s median CPU. Paired timings for the entire writer varied; no whole-builder speedup is claimed.

Length-predicate checkpoint 7973d23. For exact builtin integers, equality determines ordering but does not guarantee object identity across executions. The final experiment encodes both possible identity states for equal values using the existing map format. A test is retained if either state can change for the proposed operator. Mixed and overloaded values remain unknown. This lets equality-boundary tests skip an unchanged ordered result safely while retaining every uncertain identity case. The independent failing-test checks will run against this final map.

## Follow-up on normalized strings

Based on 32cb47e. The candidate admits single-line comparisons between a literal string and an argument-free upper(), lower(), or casefold() call, in the same ordinary undecorated function contexts as length checks. Replacements use the exact runtime operand types. A custom normalization method returning anything other than an exact builtin string remains unknown. Equal strings keep both possible object identities. Additional regression checks exercise all three method forms, reverse operand order, Unicode case folding, and mixed runtime types.

Acceptance checks are the original build timeout, identical legacy data, both public replays, all seven operator replacements on an outcome-blind per-module corpus, and execution on even-indexed development patches. No selector defaults changed. A separate experiment serializing each map file in one JSON operation saved only about 0.057 s in one host trial; the serialization code was left alone.

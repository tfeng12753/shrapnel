# Overnight map precision

Base 2c4b6a8. This branch owns build-time observations only. No mutant data used.

## Latest validated builder (1253171)

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

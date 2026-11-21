# Overnight map precision

Base 2c4b6a8. This branch owns build-time observations only. No mutant data used.

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

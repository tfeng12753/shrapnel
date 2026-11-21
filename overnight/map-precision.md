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

# Static refinement measurements

The control revision is `1ea65b5`. Patches and failure labels are used only by the host-side evaluator. Selector images receive the pinned checkout, baseline observations, patch directory and test catalog. Linux regression tests cover source readers, position readers, actual docstrings, assigned strings, mixed edits and missing or incompatible dictionary evidence.

The discarded-string check requires a string expression immediately after a class assignment, unchanged surrounding text and matching compiled code. The separate relocation option compares the nested code objects after removing location tables and keeps source readers, position observers and external dependencies. A fresh inserted-line control retained 1,471 tests and saved 7.363 simulated seconds. The six fresh controls together saved 7.731 simulated seconds with no missed failures. The 30 sample patches and two disjoint 45-patch holdout batches kept their original schedule costs with both string options enabled.

Full-item verification uses baseline measurements from the same isolated pytest entry point, including staging, setup and child processes. It admits an item only when both measured wall time and CPU fit the available allowance. The three measured item medians ranged from 3.223 to 39.797 wall seconds and from 3.159 to 39.998 CPU seconds. Verification remains off in the default configuration.

## Dictionary evidence

`key-reads.json.gz` contains 1,274 declarations across 131 files. It binds each record to the pinned source file, catalog, and compressed control map. Missing, stale and malformed records preserve baseline selection. The selector requires literal value edits that preserve the key set and value types. Whole reads, unexplained uses, shared state and source readers take precedence over key-specific evidence.

To regenerate the evidence, extract the `tools/` directory from recorder revision `44ca8f7` and pass that directory to `tools/capture_key_reads.py --recorder`. Run with the same pinned source under `PYTHONHASHSEED=0` and `1`. Both captures completed with 1,619 passed and two skips. One is the existing pinned skip. The full-size parser item uses its control-map dependencies because its instrumented call exceeded the capture limit. Use `--retain-item tests/test_parser.py::TestParser::test_max_nodes` to reproduce that choice. Run `tools/pack_key_reads.py` with both capture files and `maps/dependencies.json.gz` as the control.

A harmless dictionary control dropped from 273 selected tests to three. The breaking dictionary control dropped from nine to two and retained both failures. A mixed string and dictionary edit retained both failures. The two static refinements together reduced the six-control schedule from 52.569 to 37.446 simulated seconds.

The 40-patch dictionary cohort exposed two preexisting misses on expression argument metadata edits. The trial preserves baseline selection on those edits. The separate `--expression-schema-coverage` option additionally selects users of the affected expression classes, selecting the full suite when their coverage is unavailable. It recovered both misses and all 20 omitted failing tests in the cohort. It also added 44.695 simulated seconds on one 45-patch batch that already had zero misses. It stays off for the trial.

## Trial results

The packaged trial enables discarded strings, location-aware string edits and dictionary-key reads. The final branch passed all 284 Linux regression tests. The table compares the defaults of the two images. Each batch ran three times in a two-CPU, 4 GiB container. CPU is the median cgroup usage, including child processes. Simulated cost includes a 300-second penalty for each missed patch.

| Batch | Control simulated | Trial simulated | Control CPU | Trial CPU | Missed patches, control and trial |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sample, 30 | 116.075 | 109.822 | 1.983 | 2.475 | 0, 0 |
| Fresh controls, 6 | 52.569 | 37.446 | 3.393 | 1.205 | 0, 0 |
| Held-out dictionary edits, 40 | 802.383 | 795.687 | 2.362 | 3.010 | 2, 2 |
| First general holdout, 45 | 134.207 | 134.207 | 1.830 | 2.092 | 0, 0 |
| Second general holdout, 45 | 183.904 | 183.904 | 2.868 | 2.819 | 0, 0 |

The trial retains every labeled failure selected by the control in all five batches. Both configurations missed the same two dictionary patches and omitted the same failing tests. With expression schema coverage enabled, the dictionary cohort costs 496.455 simulated seconds and keeps every labeled failure.

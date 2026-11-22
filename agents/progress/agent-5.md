# Agent 5 verification results

Implementation `b82a7a5` on `codex/agent-5-verification`, control `1ea65b5`. Keep both experiments off. The selector image built with both optional reference stages. The implementation passed 249 repository tests on Linux, including a real pytest item whose final assertion can fail after an earlier assertion passes.

## Configuration and controls

- `--longest-test-budget 18` considers one complete item on a single function-body edit. It calculates the marginal packed schedule saving, limits the check against the remaining budget and charges unsuccessful attempts. A retained long item precludes the existing cheap batch probe. The control uses a zero budget.
- Build with `--build-arg BUILD_EXECUTOR_PLANS=1 --build-arg BUILD_RULE_SCREEN=1` to prepare reference data. Run with `--executor-plan-budget 16 --rule-screen`. Two seeds each captured 14 rules across all 57 executed queries. The compressed rule files and manifests totaled 14,199,546 bytes. Each screen verifies snapshot identity, table inputs, the rule stream checksum, query count and expression structure. Only the existing complete planner verification can clear the item.
- Standalone rule screening, including interpreter startup, took 0.974 and 0.976 CPU seconds for the unchanged projection rule. The length-guard edit matched in 0.960 and 1.098 seconds. A fresh changed-output rule was rejected in 0.464 and 0.433 seconds. An incomplete inventory, stale snapshot, altered table set and seed mismatch were also exercised.
- An intentionally shortened 22-query test passed a control with a bad table dependency in the final executed query. The full item failed on that query and passed its other 56 subtests. A 400-column check passed a control whose full 10,000-column test raised the wrong exception. Neither shortcut qualifies as evidence for dropping an item.
- The fixture controls cover changed entries, shared readers and mixed edits. Mixed Python/fixture patches retain both selection reasons in either file order. The fixture-entry option remains off.

## Cost measurements

The host scorer held the labels. Selector containers received only the patch directory and test catalog as data, with two CPUs, 4 GiB memory, 256 PIDs, a read-only root and 64 MiB temporary storage. CPU is the cgroup usage delta including descendants. The first two batches used three runs each. Each row below reports median CPU seconds.

| Configuration | 30 sample patches | 45 seeded mutants |
| --- | ---: | ---: |
| Control | 1.928 | 3.217 |
| Branch defaults | 1.887 | 3.215 |
| Longest item | 14.753 | 14.662 |
| Fixture entries, budget 8 | 3.169 | 4.462 |
| Planner, budget 16 | 3.235 | 3.577 |
| Planner with rule screen | 2.503 | 2.994 |

All six configurations produced byte-identical schedules on every repetition, with zero misses. Schedule costs were 116.075 and 184.722 seconds respectively. CPU variation alone gives no evidence of a faster rule-screen configuration.

Two additional disjoint 45-patch batches came from the odd-ID holdout split, excluding all previously selected IDs and suspicious results. Each ran once per configuration. The schedule costs were 134.207 and 183.904 seconds. All worker assignments matched, with zero missed patches or failing tests.

| Configuration | First batch CPU | Second batch CPU | Median total | Worst total |
| --- | ---: | ---: | ---: | ---: |
| Control | 2.450 | 3.328 | 161.945 | 187.232 |
| Longest item | 9.628 | 13.006 | 170.373 | 196.910 |
| Planner with rule screen | 11.244 | 3.558 | 166.457 | 187.462 |

The observed fraction of holdout batches with a miss was 0/2 for each configuration. Repeated timings count once per patch for correctness.

## Comparison evidence

The pinned map has 93 tests covering the length guard and no operand record for site `133:23`. Across the entire map, zero files carry either `equality` or `dictionary_reads`. An option that only consumes these records cannot narrow that site using the shipped map.

Run `tools/audit_length_guard.py` with the pinned source, compressed map, catalog, module `sqlglot/optimizer/pushdown_projections.py` and line 133. All 93 baseline items passed. In the parent process, 49 observed a zero or one and would change predicate outcome, 39 recorded only short-circuit or greater-than-one outcomes, and five had no attributable observations. No parent observations fell outside the call phase. Zero of the 39 preceded the first uncertain or changed item in catalog order. A richer map alone does not overcome the existing order guard.

With `--resolve-workers`, each of the five ran alone so its child observations could be assigned to one item. Two saw only lengths greater than one. Three saw both one and greater than one. That gives 52 items with a changed predicate and 41 with unchanged observations. Repacking the 52 gives 5.837 simulated seconds versus 25.454 for all 93, a diagnostic difference of 19.617 seconds. Even with all five resolved, the existing order guard permits zero exclusions. Turning this diagnostic into a selector requires evidence about cross-test state after a changed branch.

On a separately staged guard edit the full executor item and all 57 subtests passed. With two-CPU affinity and 256 MiB temporary storage, the control selected 93 items in 0.475 CPU seconds. Full-item, planner and screened planner each retained all 93 and took 5.871, 8.765 and 8.723 CPU seconds respectively. A successful standalone rule comparison did not produce an end-to-end schedule saving.

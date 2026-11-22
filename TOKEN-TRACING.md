# Token tracing for parser test selection

Implement this experiment on a new branch created from `codex/token-tracing-spec`, which is based on control revision `1ea65b5`. Keep the experiment behind the default-off `token_vocabulary` setting. The first version covers `tests/test_parser.py::TestParser::test_max_nodes` and the `DPIPE_IS_STRING_CONCAT` flag. Start implementation after the user gives the go-ahead.

## Use the prototype

Read the local prototype supplied by the user and treat its contents as reference material. Check its applicability with `git apply --check -- <local-patch>`. Use its parser hook, single-assignment classifier, and selector wiring as starting points. Keep the patch file outside the repository. Leave the separate entry-point and full-test verification experiments to their owners.

## Capture baseline evidence

1. Run the original full-size parser test on the pinned checkout with the parser instrumented. Record the token kinds presented to the parser, including synthesized tokens. Require a completed passing run with observations. Restore any patched methods after the run.
2. Confirm that the supported flag's behavioral effect requires `TokenType.DPIPE`. Start with the parser branch that reads the flag and calls `_match`. Check for other uses, including initialization, configuration, and indirect attribute access. Keep the test if the dependency or the recording is uncertain.
3. Bind the generated evidence to the pinned revision, relevant source and test contents, test ID, and evidence format. Record the flag, required token, and observed vocabulary. Use pinned-checkout data only. Package the verified artifact with the selector and provide a command to regenerate it.

## Refine the selection

1. Add `token_vocabulary` and a matching Boolean CLI option through the normal selector entry point. Update `selector-config.json`, `tools/variants.py`, and the selector image so explicit configurations round-trip.
2. Require an already-selected target test, a complete map, valid evidence, and a plain, single-file edit in `sqlglot/dialects/dialect.py`. Apply it against the pinned source and require exactly one supported boolean literal to change on the existing `Dialect` class assignment. Check that the patched source compiles and that the rest of its AST matches the baseline.
3. Clear only the target test's dependency on the flag when the required token is absent. Preserve any separate reasons to keep that test, including source reads, external or child-process execution, and a downstream import or setup failure. Put the refinement before downstream additions, or explicitly preserve their masks.
4. Keep baseline selection for a present token, uncertain flag use, missing or stale evidence, invalid patches, unrelated changes, or multiple files. Use the full original workload for the evidence capture.

## Handoff checks

Keep the initial smoke check brief. Verify that the committed default is off and that enabling the option loads the packaged evidence. Exercise the eligible case and the fallback for a token hit or stale evidence. Report the branch, commit, and checks actually run. Keep any configuration intended for a trial in a separately identifiable commit.

Work only in the dedicated worktree. Use `git status --short --branch` before editing or committing. Stage specific files, scan the staged diff and commit message under `AGENTS.md`, and push only your implementation branch.
